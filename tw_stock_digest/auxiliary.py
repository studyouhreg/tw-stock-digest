"""Supplementary official data for the daily digest.

Each source is fetched and parsed on its own, so one that is late or broken
never costs the others. Three kinds of result come back:

- per-symbol numbers (margin, securities lending, day trading, large holders),
  saved in extras.csv next to flows and valuation;
- small market-level payloads (indices, futures positions, attention and
  disposal lists, announcements, monthly revenue, ex-dividend calendar),
  saved in context.json under ``auxiliary``;
- a status per source: ``ok``, ``not_published`` (try again on a later run),
  ``unavailable`` (cannot be had for this day any more) or ``failed:<type>``.

Date-specific reports work for any trading day. The "as of now" lists (the
second group below) describe the present, so they are only fetched while the
day being collected is recent.
"""
import csv
import datetime as dt
import io
import re
import statistics
import time
import urllib.parse
from zoneinfo import ZoneInfo

from .http import HttpClient, RemoteError, persistent_json
from .insights import AUX_FIELDS, num, report_date
from .market import parse_date

TAIPEI = ZoneInfo('Asia/Taipei')
SUPPORTED = re.compile(r'[1-9]\d{3}|00[0-9A-Z]{2,4}')
RECENT_DAYS = 3
BUDGET_SECONDS = 420   # supplementary data must never cost the run its quotes
HOST_FAILURES = 2      # sources of a host that is down are not all waited for
TWSE, TPEX, TAIFEX = 'https://www.twse.com.tw/rwd/zh', 'https://www.tpex.org.tw/www/zh-tw', 'https://www.taifex.com.tw/cht/3'
URLS = {
    'twse_index': TWSE + '/afterTrading/MI_INDEX?date={d:%Y%m%d}&type=IND&response=json',
    'twse_turnover': TWSE + '/afterTrading/FMTQIK?date={d:%Y%m%d}&response=json',
    'tpex_index': TPEX + '/afterTrading/tradingIndex?date={d:%Y}%2F{d:%m}%2F{d:%d}&response=json',
    'twse_margin': TWSE + '/marginTrading/MI_MARGN?date={d:%Y%m%d}&selectType=ALL&response=json',
    'tpex_margin': TPEX + '/margin/balance?date={d:%Y}%2F{d:%m}%2F{d:%d}&response=json',
    'twse_sbl': TWSE + '/marginTrading/TWT93U?date={d:%Y%m%d}&response=json',
    'tpex_sbl': TPEX + '/margin/sbl?date={d:%Y}%2F{d:%m}%2F{d:%d}&response=json',
    'twse_daytrade': TWSE + '/dayTrading/TWTB4U?date={d:%Y%m%d}&response=json',
    'tpex_daytrade': TPEX + '/intraday/stat?date={d:%Y}%2F{d:%m}%2F{d:%d}&type=Daily&response=json',
    'taifex_futures': TAIFEX + '/futContractsDateDown',
    'taifex_pcr': TAIFEX + '/pcRatioDown',
    'taifex_large': TAIFEX + '/largeTraderFutDown',
    'twse_notice': TWSE + '/announcement/notice?startDate={d:%Y%m%d}&endDate={d:%Y%m%d}&response=json',
    'twse_punish': TWSE + '/announcement/punish?startDate={s:%Y%m%d}&endDate={d:%Y%m%d}&response=json',
    'tpex_notice': TPEX + ('/bulletin/attention?startDate={d:%Y}%2F{d:%m}%2F{d:%d}'
                           '&endDate={d:%Y}%2F{d:%m}%2F{d:%d}&response=json'),
    'tpex_punish': TPEX + ('/bulletin/disposal?startDate={s:%Y}%2F{s:%m}%2F{s:%d}'
                           '&endDate={d:%Y}%2F{d:%m}%2F{d:%d}&response=json'),
    'mops_material': 'https://mops.twse.com.tw/mops/api/t05st02',
    'twse_revenue': 'https://openapi.twse.com.tw/v1/opendata/t187ap05_L',
    'tpex_revenue': 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O',
    'twse_exright': 'https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL',
    'tpex_exright': 'https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost',
    'tdcc': 'https://opendata.tdcc.com.tw/getOD.ashx?id=1-5',
}
AS_OF_NOW = ('twse_revenue', 'tpex_revenue', 'twse_exright', 'tpex_exright', 'tdcc')
DISPOSAL_LOOKBACK = 30   # days of disposal announcements that can still be in force
# Per-symbol columns each source fills in extras.csv, and the market it covers (None: both).
SYMBOL_FIELDS = {
    'twse_margin': ('TWSE', ('margin', 'margin_change', 'short', 'short_change')),
    'tpex_margin': ('TPEX', ('margin', 'margin_change', 'short', 'short_change')),
    'twse_sbl': ('TWSE', ('sbl', 'sbl_change')), 'tpex_sbl': ('TPEX', ('sbl', 'sbl_change')),
    'twse_daytrade': ('TWSE', ('daytrade',)), 'tpex_daytrade': ('TPEX', ('daytrade',)),
    'tdcc': (None, ('big400_pct', 'big1000_pct', 'holders')),
}
# Legacy combined sector indices that would duplicate their components in a ranking.
COMBINED_SECTORS = {'水泥窯製類指數', '塑膠化工類指數', '機電類指數', '化學生技醫療類指數', '電子工業類指數'}
IDENTITIES = {'外資及陸資': 'foreign', '外資': 'foreign', '投信': 'trust', '自營商': 'dealer'}


class NotPublished(Exception):
    """The source has nothing for this day yet."""


class Unavailable(Exception):
    """The source can no longer describe this day."""


def text_of(value):
    return re.sub(r'<[^>]+>', '', str(value)).strip()


def table(data, title_part=None, field=None):
    """The first table whose title or fields match, as (fields, rows)."""
    tables = data.get('tables') or ([data] if 'fields' in data else [])
    for t in tables:
        fields = [str(f).strip() for f in t.get('fields') or []]
        if (title_part is None or title_part in (t.get('title') or '')) and (field is None or field in fields):
            return fields, t.get('data') or []
    return [], []


def require_day(data, day):
    """Per-date JSON reports echo their date; anything else means it is not this day's report."""
    if not isinstance(data, dict) or str(data.get('stat', '')).lower() != 'ok' or report_date(data) != day:
        raise NotPublished()


def roc_slash(day):
    return f'{day.year - 1911}/{day:%m/%d}'


def as_date(value):
    return dt.date.fromisoformat(parse_date(re.sub(r'\D', '', str(value))))


# --- indices -------------------------------------------------------------------

def twse_index(data, day):
    require_day(data, day)
    _, rows = table(data, '價格指數(臺灣證券交易所)')
    taiex, sectors = None, []
    for r in rows:
        name, close, points, percent = text_of(r[0]), num(r[1]), num(r[3]), num(r[4])
        if close is None or percent is None:
            continue
        if name == '發行量加權股價指數':
            sign = -1 if '-' in text_of(r[2]) else 1
            taiex = dict(close=close, change=round(sign * (points or 0), 2), change_pct=percent)
        elif name.endswith('類指數') and name not in COMBINED_SECTORS:
            sectors.append(dict(name=name[:-2], close=close, change_pct=percent))
    if taiex is None:
        raise NotPublished()
    return dict(taiex=taiex, sectors=sectors), {}


def twse_turnover(data, day):
    if not isinstance(data, dict) or data.get('stat') != 'OK':
        raise NotPublished()
    _, rows = table(data, field='成交金額')
    for r in rows:
        if r[0].strip() == roc_slash(day):
            return dict(turnover_twd=num(r[2]), volume_shares=num(r[1]), trades=num(r[3])), {}
    raise NotPublished()


def tpex_index(data, day):
    if not isinstance(data, dict) or str(data.get('stat', '')).lower() != 'ok':
        raise NotPublished()
    _, rows = table(data, field='櫃買指數')
    for r in rows:
        if str(r[0]).strip() == roc_slash(day):
            close, change = num(r[4]), num(r[5])
            previous = close - change if close is not None and change is not None else None
            return dict(close=close, change=change,
                        change_pct=round(100 * change / previous, 2) if previous else None,
                        turnover_twd=(num(r[2]) or 0) * 1000), {}
    raise NotPublished()


# --- futures and options (TAIFEX CSV downloads) --------------------------------

def csv_rows(text):
    return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text)) if row and row[0].strip()]


def by_date(rows, day):
    """Rows of the day and of the latest earlier day present, keyed by ISO date."""
    grouped = {}
    for r in rows[1:]:
        try:
            grouped.setdefault(dt.date.fromisoformat(r[0].replace('/', '-')), []).append(r)
        except ValueError:
            continue
    if day not in grouped:
        raise NotPublished()
    earlier = [d for d in grouped if d < day]
    return grouped[day], grouped[max(earlier)] if earlier else []


def taifex_futures(text, day):
    today, previous = by_date(csv_rows(text), day)

    def net(rows, column):
        return {IDENTITIES[r[2]]: int(r[column]) for r in rows if r[1] == '臺股期貨' and r[2] in IDENTITIES}
    oi, before, traded = net(today, 13), net(previous, 13), net(today, 7)
    if 'foreign' not in oi:
        raise NotPublished()
    return dict(net_oi=oi, net_oi_change={k: oi[k] - before[k] for k in oi if k in before} or None,
                net_traded=traded), {}


def taifex_pcr(text, day):
    today, previous = by_date(csv_rows(text), day)
    row = today[0]
    out = dict(volume_ratio=num(row[3]), oi_ratio=num(row[6]), put_oi=num(row[4]), call_oi=num(row[5]))
    if previous and out['oi_ratio'] is not None and num(previous[0][6]) is not None:
        out['oi_ratio_change'] = round(out['oi_ratio'] - num(previous[0][6]), 2)
    return out, {}


def taifex_large(text, day):
    today, _ = by_date(csv_rows(text), day)
    # TX with month 999999 is every expiry combined; trader class 0 is all traders, 1 the "specific institutions".
    rows = {r[4]: r for r in today if r[1] == 'TX' and r[3] == '999999'}
    if '0' not in rows:
        raise NotPublished()
    everyone = [int(x) for x in rows['0'][5:10]]
    out = dict(top5_net=everyone[0] - everyone[1], top10_net=everyone[2] - everyone[3], market_oi=everyone[4])
    if '1' in rows:
        specific = [int(x) for x in rows['1'][5:9]]
        out['top10_specific_net'] = specific[2] - specific[3]
    return out, {}


# --- margin, lending, day trading ----------------------------------------------

def lots(value):
    return None if value is None else round(value / 1000, 3)


def twse_margin(data, day):
    require_day(data, day)
    _, summary = table(data, '信用交易統計')
    _, rows = table(data, '融資融券彙總')
    if not rows or not summary:
        raise NotPublished()
    totals = {r[0].strip(): (num(r[4]), num(r[5])) for r in summary}
    money, margin, short = (totals.get(k, (None, None)) for k in ('融資金額(仟元)', '融資(交易單位)', '融券(交易單位)'))
    payload = dict(margin_twd=(money[1] or 0) * 1000, margin_change_twd=((money[1] or 0) - (money[0] or 0)) * 1000,
                   margin_lots=margin[1], margin_lots_change=(margin[1] or 0) - (margin[0] or 0),
                   short_lots=short[1], short_lots_change=(short[1] or 0) - (short[0] or 0))
    symbols = {r[0].strip(): dict(margin=num(r[6]), margin_change=(num(r[6]) or 0) - (num(r[5]) or 0),
                                  short=num(r[12]), short_change=(num(r[12]) or 0) - (num(r[11]) or 0)) for r in rows}
    return payload, symbols


def tpex_margin(data, day):
    require_day(data, day)
    tables = data.get('tables') or [{}]
    rows, summary = tables[0].get('data') or [], tables[0].get('summary') or []
    if not rows:
        raise NotPublished()
    totals = {r[1].strip(): r for r in summary}
    lot_row, money_row = totals.get('合計(張)'), totals.get('融資金(仟元)')
    payload = {}
    if money_row:
        payload.update(margin_twd=(num(money_row[6]) or 0) * 1000,
                       margin_change_twd=((num(money_row[6]) or 0) - (num(money_row[2]) or 0)) * 1000)
    if lot_row:
        payload.update(margin_lots=num(lot_row[6]), margin_lots_change=(num(lot_row[6]) or 0) - (num(lot_row[2]) or 0),
                       short_lots=num(lot_row[14]), short_lots_change=(num(lot_row[14]) or 0) - (num(lot_row[10]) or 0))
    symbols = {r[0].strip(): dict(margin=num(r[6]), margin_change=(num(r[6]) or 0) - (num(r[2]) or 0),
                                  short=num(r[14]), short_change=(num(r[14]) or 0) - (num(r[10]) or 0)) for r in rows}
    return payload, symbols


def lending(data, day):
    """Securities-lending short sale balances (TWSE TWT93U and TPEx sbl share one layout); shares -> lots."""
    require_day(data, day)
    _, rows = table(data)
    if not rows:
        raise NotPublished()
    symbols = {r[0].strip(): dict(sbl=lots(num(r[12])), sbl_change=lots((num(r[12]) or 0) - (num(r[8]) or 0)))
               for r in rows}
    payload = dict(sbl_lots=round(sum(s['sbl'] or 0 for s in symbols.values())),
                   sbl_lots_change=round(sum(s['sbl_change'] or 0 for s in symbols.values())))
    return payload, symbols


def daytrade(data, day):
    require_day(data, day)
    _, stats = table(data, field='當日沖銷交易總成交股數')
    _, rows = table(data, field='當日沖銷交易成交股數')
    if not stats or not rows:
        raise NotPublished()
    values = [num(str(v).rstrip('%')) for v in stats[0]]
    payload = dict(volume_share_pct=values[1], buy_value_share_pct=values[3], sell_value_share_pct=values[5])
    return payload, {r[0].strip(): dict(daytrade=num(r[3])) for r in rows}


# --- "as of now" lists ---------------------------------------------------------

def published(data):
    return isinstance(data, dict) and str(data.get('stat', 'ok')).lower() == 'ok'


def notice(data, day, settled):
    """Securities in the day's attention announcement (per-date report of either exchange).

    An empty list on the day itself usually means the evening announcement is
    not out yet; once the day is over (``settled``) it means there were none.
    """
    if not published(data):
        raise NotPublished()
    fields, rows = table(data, field='證券代號')
    if not rows and not settled:
        raise NotPublished()
    c = {name: i for i, name in enumerate(fields)}
    out = []
    for r in rows:
        symbol = str(r[c['證券代號']]).strip()
        if SUPPORTED.fullmatch(symbol):
            out.append(dict(symbol=symbol, name=text_of(r[c['證券名稱']]), reason=text_of(r[c['注意交易資訊']])[:100]))
    return sorted(out, key=lambda x: x['symbol']), {}


def punish(data, day):
    """Securities under a disposal announced by the day whose period has not ended."""
    if not published(data):
        raise NotPublished()
    fields, rows = table(data, field='證券代號')
    c = {name: i for i, name in enumerate(fields)}
    period_field = next((f for f in fields if f.startswith('處置起')), None)  # 起迄 (TWSE) or 起訖 (TPEx)
    out = {}
    for r in rows if period_field else []:
        symbol = str(r[c['證券代號']]).strip()
        period = re.findall(r'\d{3}/\d{2}/\d{2}', str(r[c[period_field]]))
        if not SUPPORTED.fullmatch(symbol) or len(period) != 2:
            continue
        announced, start, end = as_date(r[c['公布日期']]), as_date(period[0]), as_date(period[1])
        if announced <= day <= end and (symbol not in out or end.isoformat() > out[symbol]['end']):
            name = re.sub(r'\(\.\./.*$', '', text_of(r[c['證券名稱']]))  # TPEx appends a link
            out[symbol] = dict(symbol=symbol, name=name, start=start.isoformat(), end=end.isoformat(),
                               measure=text_of(r[c['處置措施']])[:20])
    return sorted(out.values(), key=lambda x: (x['start'], x['symbol'])), {}


def material_days(day):
    """Calendar days whose announcement lists cover everything since the last close.

    A day's list starts at 17:30 the evening before, so after a weekend the
    two days before Monday are needed as well.
    """
    days = [day]
    while days[-1].weekday() in (0, 6) and len(days) < 3:  # Monday needs Sunday, Sunday needs Saturday
        days.append(days[-1] - dt.timedelta(days=1))
    return days[::-1]


def material(lists, day, watchlist):
    """Material announcements since the previous close: the watchlist's, and how many the day's list holds."""
    if not lists or not all(isinstance(d, dict) and isinstance((d.get('result') or {}).get('data'), list)
                            for d in lists):
        raise NotPublished()
    items, count, since = {}, 0, None
    for position, data in enumerate(lists):
        for r in data['result']['data']:
            try:
                spoke, clock, symbol = as_date(r[0]), str(r[1]).strip(), str(r[2]).strip()
            except (ValueError, IndexError):
                continue
            if spoke > day:
                continue
            count += position == len(lists) - 1
            stamp = f'{spoke.isoformat()} {clock[:5]}'
            since = stamp if since is None or stamp < since else since
            if symbol in watchlist:
                subject = re.sub(r'\s+', '', str(r[4]))[:90]
                items[(stamp, symbol, subject)] = dict(symbol=symbol, name=str(r[3]).strip(), date=spoke.isoformat(),
                                                       time=clock[:5], subject=subject)
    return dict(count=count, since=since, items=[items[k] for k in sorted(items)]), {}


def revenue(rows, watchlist):
    """Latest monthly revenue the exchange lists: watchlist rows and a summary of the newest month."""
    def month_of(r):
        digits = re.sub(r'\D', '', str(r.get('資料年月', '')))
        return f'{int(digits[:-2]) + 1911}-{digits[-2:]}' if len(digits) >= 4 else None
    months = [m for m in (month_of(r) for r in rows) if m]
    if not months:
        raise NotPublished()
    newest = max(months)
    growth = [num(r.get('營業收入-去年同月增減(%)')) for r in rows if month_of(r) == newest]
    growth = [g for g in growth if g is not None]
    items = []
    for r in rows:
        symbol = str(r.get('公司代號', '')).strip()
        if symbol in watchlist:
            thousand = num(r.get('營業收入-當月營收'))
            items.append(dict(symbol=symbol, name=str(r.get('公司名稱', '')).strip(), month=month_of(r),
                              revenue_100m_twd=round(thousand / 1e5, 2) if thousand is not None else None,
                              mom_pct=rounded(num(r.get('營業收入-上月比較增減(%)'))),
                              yoy_pct=rounded(num(r.get('營業收入-去年同月增減(%)'))),
                              ytd_yoy_pct=rounded(num(r.get('累計營業收入-前期比較增減(%)')))))
    return dict(month=newest, reported=len(growth),
                median_yoy_pct=rounded(statistics.median(growth)) if growth else None, items=items), {}


def rounded(value):
    return None if value is None else round(value, 2)


def exright(rows, day, watchlist, date='Date', code='Code', name='Name', kind='Exdividend', cash='CashDividend'):
    """Ex-rights and ex-dividend dates in the week after the day."""
    upcoming = []
    for r in rows:
        symbol = str(r.get(code, '')).strip()
        try:
            when = as_date(r[date])
        except (KeyError, ValueError):
            continue
        if SUPPORTED.fullmatch(symbol) and day < when <= day + dt.timedelta(days=7):
            upcoming.append(dict(symbol=symbol, name=str(r.get(name, '')).strip(), date=when.isoformat(),
                                 kind=str(r.get(kind, '')).replace('除', '').strip(), cash=rounded(num(r.get(cash)))))
    upcoming.sort(key=lambda x: (x['date'], x['symbol']))
    first = upcoming[0]['date'] if upcoming else None
    return dict(count=len(upcoming), next_date=first,
                next=[x for x in upcoming if x['date'] == first][:15],
                watchlist=[x for x in upcoming if x['symbol'] in watchlist]), {}


def tdcc(text, day):
    """Share of each security held in accounts of at least 400 and 1,000 lots (weekly)."""
    symbols, date = {}, None
    for r in csv.reader(io.StringIO(text.lstrip('﻿'))):
        if len(r) < 6 or not r[0].strip().isdigit():
            continue
        symbol, level = r[1].strip(), int(r[2])
        if not SUPPORTED.fullmatch(symbol):
            continue
        date = date or as_date(r[0])
        entry = symbols.setdefault(symbol, dict(big400_pct=0.0, big1000_pct=0.0, holders=None))
        if 12 <= level <= 15:
            entry['big400_pct'] = round(entry['big400_pct'] + float(r[5]), 2)
        if level == 15:
            entry['big1000_pct'] = float(r[5])
        if level == 17:
            entry['holders'] = int(r[3])
    if not symbols:
        raise NotPublished()
    if date > day:
        raise Unavailable()  # the file only holds the newest week, which this day had not seen yet
    return dict(date=date.isoformat()), symbols


# --- fetching ------------------------------------------------------------------

def post_json_persistently(http, url, payload, attempts=8, pause=2.0, sleep=time.sleep):
    """MOPS, like TWSE, answers some requests with a security page; a few short retries get through."""
    for attempt in range(attempts):
        try:
            return http.json('POST', url, payload=payload, headers={'User-Agent': 'Mozilla/5.0'}, retry=False)
        except RemoteError:
            if attempt == attempts - 1:
                raise
            sleep(pause)


def fetch_auxiliary(trade_date, watchlist, *, http=None, get=None, post=None, text=None, post_json=None,
                    today=None, clock=time.monotonic, budget=BUDGET_SECONDS):
    """Return (per-symbol values, payload by source, status by source). Never raises for one bad source.

    ``get(url)`` returns JSON, ``post(url, form)`` and ``text(url)`` return decoded
    text, ``post_json(url, payload)`` returns JSON.
    The whole call is bounded: once ``budget`` seconds have passed, or a host
    has been unreachable twice in a row, the remaining sources are marked
    failed and left to the next run.
    """
    day = dt.date.fromisoformat(trade_date)
    today = today or dt.datetime.now(TAIPEI).date()
    http = http or HttpClient(timeout=30)
    get = get or (lambda url: persistent_json(http, url, attempts=10, pause=2.0))
    post = post or (lambda url, form: http.request(
        'POST', url, body=form.encode(), retry=True,
        headers={'User-Agent': 'Mozilla/5.0', 'Content-Type': 'application/x-www-form-urlencoded',
                 'Accept': 'text/csv,*/*'}).decode('cp950', errors='replace'))
    text = text or (lambda url: http.request('GET', url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': '*/*'})
                    .decode('utf-8-sig', errors='replace'))
    post_json = post_json or (lambda url, payload: post_json_persistently(http, url, payload))
    watch = set(watchlist)
    week = f'queryStartDate={day - dt.timedelta(days=10):%Y%%2F%m%%2F%d}&queryEndDate={day:%Y%%2F%m%%2F%d}'
    single = f'queryStartDate={day:%Y%%2F%m%%2F%d}&queryEndDate={day:%Y%%2F%m%%2F%d}'

    def url(name):
        return URLS[name].format(d=day, s=day - dt.timedelta(days=DISPOSAL_LOOKBACK))

    def announcements():
        return [post_json(URLS['mops_material'], dict(year=str(d.year - 1911), month=f'{d:%m}', day=f'{d:%d}'))
                for d in material_days(day)]

    sources = {
        'twse_index': lambda: twse_index(get(url('twse_index')), day),
        'twse_turnover': lambda: twse_turnover(get(url('twse_turnover')), day),
        'tpex_index': lambda: tpex_index(get(url('tpex_index')), day),
        'taifex_futures': lambda: taifex_futures(post(URLS['taifex_futures'], week + '&commodityId=TXF'), day),
        'taifex_pcr': lambda: taifex_pcr(post(URLS['taifex_pcr'], week), day),
        'taifex_large': lambda: taifex_large(post(URLS['taifex_large'], single), day),
        'twse_margin': lambda: twse_margin(get(url('twse_margin')), day),
        'tpex_margin': lambda: tpex_margin(get(url('tpex_margin')), day),
        'twse_sbl': lambda: lending(get(url('twse_sbl')), day),
        'tpex_sbl': lambda: lending(get(url('tpex_sbl')), day),
        'twse_daytrade': lambda: daytrade(get(url('twse_daytrade')), day),
        'tpex_daytrade': lambda: daytrade(get(url('tpex_daytrade')), day),
        'twse_notice': lambda: notice(get(url('twse_notice')), day, today > day),
        'twse_punish': lambda: punish(get(url('twse_punish')), day),
        'tpex_notice': lambda: notice(get(url('tpex_notice')), day, today > day),
        'tpex_punish': lambda: punish(get(url('tpex_punish')), day),
        'mops_material': lambda: material(announcements(), day, watch),
        'twse_revenue': lambda: revenue(get(URLS['twse_revenue']), watch),
        'tpex_revenue': lambda: revenue(get(URLS['tpex_revenue']), watch),
        'twse_exright': lambda: exright(get(URLS['twse_exright']), day, watch),
        'tpex_exright': lambda: exright(get(URLS['tpex_exright']), day, watch, 'ExRrightsExDividendDate',
                                        'SecuritiesCompanyCode', 'CompanyName', 'ExRrightsExDividend'),
        'tdcc': lambda: tdcc(text(URLS['tdcc']), day),
    }
    recent = (today - day).days <= RECENT_DAYS
    symbols, payloads, status = {}, {}, {}
    started, unreachable = clock(), {}
    for name, fetch in sources.items():
        host = urllib.parse.urlsplit(URLS[name]).hostname
        if name in AS_OF_NOW and not recent:
            status[name] = 'unavailable'
            continue
        if clock() - started > budget:
            status[name] = 'failed:TimeBudget'
            continue
        if unreachable.get(host, 0) >= HOST_FAILURES:
            status[name] = 'failed:HostDown'
            continue
        try:
            payload, values = fetch()
        except NotPublished:
            status[name] = 'not_published'
            unreachable[host] = 0
        except Unavailable:
            status[name] = 'unavailable'
            unreachable[host] = 0
        except RemoteError:
            status[name] = 'failed:RemoteError'
            unreachable[host] = unreachable.get(host, 0) + 1
        except Exception as exc:  # one source must not lose the others
            status[name] = f'failed:{type(exc).__name__}'
        else:
            unreachable[host] = 0
            payloads[name] = payload
            for symbol, record in values.items():
                if SUPPORTED.fullmatch(symbol):
                    symbols.setdefault(symbol, {}).update(record)
            status[name] = 'ok'
    return symbols, payloads, status


__all__ = ['AUX_FIELDS', 'SYMBOL_FIELDS', 'URLS', 'NotPublished', 'Unavailable', 'fetch_auxiliary']
