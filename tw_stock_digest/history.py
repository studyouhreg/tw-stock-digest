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


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    tmp.replace(path)


def jobs(cache, start, end, datasets, today):
    """Yield (host, path, url) for everything not cached yet, newest first."""
    for name in datasets:
        if name in RANGES:
            host, url = RANGES[name]
            for a, b in reversed(list(months(start, end))):
                path = range_path(cache, name, a)
                # A month that was still open when fetched is fetched again.
                if not path.exists() or json.loads(path.read_text()).get('_complete') is False:
                    yield host, path, url(a, b), b < today
    # Dataset-major order, so earlier datasets (prices) finish first.
    for name in datasets:
        if name in DAILY:
            host, url = DAILY[name]
            for day in sorted(weekdays(start, end), reverse=True):
                path = daily_path(cache, name, day)
                if not path.exists():
                    yield host, path, url(day), True


def download(cache, start, end, datasets, *, interval=2.5, http=None, log=print, today=None):
    today = today or dt.date.today()
    http = http or HttpClient(timeout=60)
    queues = {}
    for host, path, url, complete in jobs(cache, start, end, datasets, today):
        queues.setdefault(host, []).append((path, url, complete))
    failures = []

    def worker(host, items):
        throttle = Throttle(interval)
        log(f'[{host}] {len(items)} requests queued')
        for index, (path, url, complete) in enumerate(items, 1):
            try:
                data = fetch_json(http, url, throttle)
            except RemoteError:
                failures.append(str(path))
                log(f'[{host}] failed {path.name}')
                continue
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
    unknown = set(names) - set(DAILY) - set(RANGES)
    if unknown:
        parser.error(f'unknown datasets: {sorted(unknown)}')
    failures = download(args.cache, args.start, args.end, names, interval=args.interval,
                        log=lambda m: print(m, flush=True))
    print(f'done, {len(failures)} failures', flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
