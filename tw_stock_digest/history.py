"""Download and cache official historical daily data for backtests.

The latest-snapshot OpenAPI used by the daily pipeline cannot be backfilled, so
backtests read the exchanges' per-date reports instead. Every response is kept
verbatim under the cache directory; parsing happens later in ``panel``.
Requests are spaced per host because both sites block rapid clients.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import sys
import threading
import time

from .http import HttpClient, RemoteError

# The TWSE firewall rejects descriptive agent strings, so send a generic one.
HEADERS = {'User-Agent': 'Mozilla/5.0'}
DAILY = {
    # name: (host key, url template taking a date)
    'twse_price': ('twse', lambda d: 'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX'
                   f'?date={d:%Y%m%d}&type=ALLBUT0999&response=json'),
    'tpex_price': ('tpex', lambda d: 'https://www.tpex.org.tw/www/zh-tw/afterTrading/otc'
                   f'?date={d:%Y}%2F{d:%m}%2F{d:%d}&type=EW&response=json'),
    'twse_inst': ('twse', lambda d: 'https://www.twse.com.tw/rwd/zh/fund/T86'
                  f'?date={d:%Y%m%d}&selectType=ALLBUT0999&response=json'),
    'tpex_inst': ('tpex', lambda d: 'https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade'
                  f'?type=Daily&sect=EW&date={d:%Y}%2F{d:%m}%2F{d:%d}&response=json'),
}
# Sampled daily reports: (host, url template, sample) where sample picks the
# last trading day of each 'week' or 'month' (trading days come from twse_price).
SAMPLED = {
    'twse_margin': ('twse', lambda d: 'https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN'
                    f'?date={d:%Y%m%d}&selectType=ALL&response=json', 'week'),
    'tpex_margin': ('tpex', lambda d: 'https://www.tpex.org.tw/www/zh-tw/margin/balance'
                    f'?date={d:%Y}%2F{d:%m}%2F{d:%d}&response=json', 'week'),
    'twse_valuation': ('twse', lambda d: 'https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d'
                       f'?date={d:%Y%m%d}&selectType=ALL&response=json', 'month'),
    'tpex_valuation': ('tpex', lambda d: 'https://www.tpex.org.tw/www/zh-tw/afterTrading/peQryDate'
                       f'?date={d:%Y}%2F{d:%m}%2F{d:%d}&response=json', 'month'),
}
MOPS = 'https://mopsov.twse.com.tw'
MARKETS = {'sii': 'TWSE', 'otc': 'TPEX'}
RANGES = {
    # Ex-rights / ex-dividend reference prices, fetched per calendar month.
    'twse_exright': ('twse', lambda a, b: 'https://www.twse.com.tw/rwd/zh/exRight/TWT49U'
                     f'?response=json&startDate={a:%Y%m%d}&endDate={b:%Y%m%d}'),
    'tpex_exright': ('tpex', lambda a, b: 'https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ'
                     f'?startDate={a:%Y}%2F{a:%m}%2F{a:%d}&endDate={b:%Y}%2F{b:%m}%2F{b:%d}&response=json'),
}


class Throttle:
    """Per-host spacing that slows down after a block and recovers slowly."""

    def __init__(self, interval, clock=time.monotonic, sleep=time.sleep, ceiling=8.0):
        self.base = self.interval = interval
        self.clock, self.sleep, self.ceiling = clock, sleep, ceiling
        self.last, self.streak = None, 0

    def wait(self):
        if self.last is not None:
            remaining = self.interval - (self.clock() - self.last)
            if remaining > 0:
                self.sleep(remaining)
        self.last = self.clock()

    def success(self):
        self.streak += 1
        if self.streak >= 30 and self.interval > self.base:
            self.interval, self.streak = max(self.base, self.interval * 0.9), 0

    def blocked(self):
        self.streak = 0
        self.interval = min(self.ceiling, self.interval * 1.1)


def weekdays(start, end):
    day = start
    while day <= end:
        if day.weekday() < 5:
            yield day
        day += dt.timedelta(days=1)


def months(start, end):
    first = start.replace(day=1)
    while first <= end:
        following = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        yield max(first, start), min(following - dt.timedelta(days=1), end)
        first = following


def daily_path(cache, dataset, day):
    return Path(cache) / dataset / f'{day:%Y}' / f'{day:%Y%m%d}.json'


def range_path(cache, dataset, start):
    return Path(cache) / dataset / f'{start:%Y%m}.json'


def fetch_json(http, url, throttle, *, attempts=10, cooldown=2, sleep=time.sleep):
    for attempt in range(attempts):
        throttle.wait()
        try:
            data = http.json('GET', url, headers=HEADERS)
            if isinstance(data, dict):
                throttle.success()
                return data
        except RemoteError:
            # TWSE randomly answers about half of requests with a 307 security page
            # (even at a few seconds' spacing); short retries recover fastest.
            pass
        throttle.blocked()
        if attempt < attempts - 1:
            sleep(cooldown)
    raise RemoteError(url.split('/')[2])


BLOCKED = (b'FOR SECURITY REASONS', '因為安全性考量'.encode())


def fetch_text(http, method, url, body, throttle, *, attempts=10, cooldown=2, sleep=time.sleep):
    """Fetch an HTML report; MOPS answers blocks with a short security page."""
    for attempt in range(attempts):
        throttle.wait()
        try:
            data = http.request(method, url, body=body, headers={
                **HEADERS, 'Accept': 'text/html',
                **({'Content-Type': 'application/x-www-form-urlencoded'} if body else {})}, retry=False)
            if len(data) > 2000 and not any(marker in data[:3000] for marker in BLOCKED):
                throttle.success()
                return data
        except RemoteError:
            pass
        throttle.blocked()
        if attempt < attempts - 1:
            sleep(cooldown)
    raise RemoteError(url.split('/')[2])


def trading_days(cache, start, end):
    """Trading days known from the cached TWSE price reports."""
    days = []
    for path in sorted((Path(cache) / 'twse_price').glob('*/*.json')):
        day = dt.date(int(path.stem[:4]), int(path.stem[4:6]), int(path.stem[6:]))
        if start <= day <= end and path.stat().st_size > 1000:
            days.append(day)
    return days


def sample_days(days, period):
    key = (lambda d: d.isocalendar()[:2]) if period == 'week' else (lambda d: (d.year, d.month))
    last = {}
    for day in days:
        last[key(day)] = day
    return sorted(last.values())


def quarters(start, end):
    for year in range(start.year, end.year + 1):
        for q in range(1, 5):
            first = dt.date(year, 3 * q - 2, 1)
            if start <= dt.date(year, 3 * q, 28) and first <= end:
                yield year, q


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    tmp.replace(path)


def jobs(cache, start, end, datasets, today):
    """Yield (host, path, request, complete) for everything not cached yet, newest first."""
    cache = Path(cache)
    for name in datasets:
        if name in RANGES:
            host, url = RANGES[name]
            for a, b in reversed(list(months(start, end))):
                path = range_path(cache, name, a)
                # A month that was still open when fetched is fetched again.
                if not path.exists() or json.loads(path.read_text()).get('_complete') is False:
                    yield host, path, dict(url=url(a, b)), b < today
        elif name == 'mops_revenue':
            for a, _ in reversed(list(months(start, end))):
                for market in MARKETS:
                    path = cache / name / market / f'{a:%Y%m}.html'
                    if not path.exists():
                        url = f'{MOPS}/nas/t21/{market}/t21sc03_{a.year - 1911}_{a.month}_0.html'
                        yield 'mops', path, dict(url=url, kind='html'), True
        elif name in ('mops_income', 'mops_balance'):
            endpoint = 'ajax_t163sb04' if name == 'mops_income' else 'ajax_t163sb05'
            for year, q in reversed(list(quarters(start, end))):
                for market in MARKETS:
                    path = cache / name / market / f'{year}Q{q}.html'
                    if not path.exists():
                        body = (f'encodeURIComponent=1&step=1&firstin=1&off=1&isQuery=Y&TYPEK={market}'
                                f'&year={year - 1911}&season=0{q}').encode()
                        yield 'mops', path, dict(url=f'{MOPS}/mops/web/{endpoint}', method='POST',
                                                 body=body, kind='html'), True
    # Dataset-major order, so earlier datasets (prices) finish first.
    for name in datasets:
        if name in DAILY or name in SAMPLED:
            host, url, *period = DAILY.get(name) or SAMPLED[name]
            days = (sample_days(trading_days(cache, start, end), period[0]) if period
                    else list(weekdays(start, end)))
            for day in sorted(days, reverse=True):
                path = daily_path(cache, name, day)
                if not path.exists():
                    yield host, path, dict(url=url(day)), True


def download(cache, start, end, datasets, *, interval=2.5, http=None, log=print, today=None):
    today = today or dt.date.today()
    http = http or HttpClient(timeout=60)
    queues = {}
    for host, path, request, complete in jobs(cache, start, end, datasets, today):
        queues.setdefault(host, []).append((path, request, complete))
    failures = []

    def worker(host, items):
        # TWSE blocks at random, not by rate, so slowing down only loses time.
        throttle = Throttle(interval, ceiling=interval)
        log(f'[{host}] {len(items)} requests queued')
        for index, (path, request, complete) in enumerate(items, 1):
            try:
                if request.get('kind') == 'html':
                    raw = fetch_text(http, request.get('method', 'GET'), request['url'],
                                     request.get('body'), throttle)
                else:
                    data = fetch_json(http, request['url'], throttle)
            except RemoteError:
                failures.append(str(path))
                log(f'[{host}] failed {path.name}')
                continue
            if request.get('kind') == 'html':
                path.parent.mkdir(parents=True, exist_ok=True)
                path.with_name(path.name + '.tmp').write_bytes(raw)
                path.with_name(path.name + '.tmp').replace(path)
            else:
                data['_complete'] = complete
                save(path, data)
            if index % 25 == 0 or index == len(items):
                log(f'[{host}] {index}/{len(items)} {path.parent.name}/{path.name} '
                    f'interval={throttle.interval:.1f}s')

    threads = [threading.Thread(target=worker, args=item) for item in queues.items()]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description='下載證交所／櫃買中心歷史日資料到本機快取（供回測）')
    parser.add_argument('--start', type=dt.date.fromisoformat, required=True)
    parser.add_argument('--end', type=dt.date.fromisoformat, required=True)
    parser.add_argument('--cache', type=Path, default=Path('cache/history'))
    parser.add_argument('--datasets', default='twse_price,tpex_price,twse_exright,tpex_exright')
    parser.add_argument('--interval', type=float, default=2.5, help='同一網站兩次請求的最短秒數')
    args = parser.parse_args(argv)
    names = [n.strip() for n in args.datasets.split(',') if n.strip()]
    unknown = set(names) - set(DAILY) - set(RANGES) - set(SAMPLED) - {
        'mops_revenue', 'mops_income', 'mops_balance'}
    if unknown:
        parser.error(f'unknown datasets: {sorted(unknown)}')
    failures = download(args.cache, args.start, args.end, names, interval=args.interval,
                        log=lambda m: print(m, flush=True))
    print(f'done, {len(failures)} failures', flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
