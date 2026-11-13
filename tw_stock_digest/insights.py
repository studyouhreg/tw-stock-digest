"""Valuation and money-flow sections of the daily digest.

Four sections, all computed by this module (the model only reads them):

1. Valuation: official P/E, P/B and dividend yield, with market and industry medians.
2. Institutional flows: foreign, investment-trust and dealer net buying in TWD
   (net shares x that day's close), 5-day totals and consecutive buy/sell days.
3. Industry rotation: each industry's share of turnover today versus its
   20-day average, its 5-day median return and 5-day foreign net buying.
4. Stock money flow: volume surges on up days, 14-day Money Flow Index (MFI)
   and 10-day On-Balance Volume (OBV) in days of average volume.

Inputs are the exchanges' per-date reports for the snapshot's trading day
(institutional flows, valuation) and the current industry classification.
Momentum windows use earlier days already saved in ``data/daily``; every
section reports how many days it had, and says so when that is not enough.
"""
import csv
import datetime as dt
import io
import json
from pathlib import Path
import re
import statistics

from .http import HttpClient, persistent_json

# Industry codes used by both exchanges' company lists. Names were matched by
# grouping MOPS revenue reports (which name each industry) by company code.
INDUSTRIES = {
    '01': '水泥工業', '02': '食品工業', '03': '塑膠工業', '04': '紡織纖維', '05': '電機機械',
    '06': '電器電纜', '08': '玻璃陶瓷', '09': '造紙工業', '10': '鋼鐵工業', '11': '橡膠工業',
    '12': '汽車工業', '14': '建材營造', '15': '航運業', '16': '觀光餐旅', '17': '金融保險業',
    '18': '貿易百貨', '20': '其他', '21': '化學工業', '22': '生技醫療業', '23': '油電燃氣業',
    '24': '半導體業', '25': '電腦及週邊設備業', '26': '光電業', '27': '通信網路業',
    '28': '電子零組件業', '29': '電子通路業', '30': '資訊服務業', '31': '其他電子業',
    '32': '文化創意業', '33': '農業科技業', '35': '綠能環保', '36': '數位雲端',
    '37': '運動休閒', '38': '居家生活', '91': '存託憑證',
}
URLS = {
    'twse_flows': 'https://www.twse.com.tw/rwd/zh/fund/T86?date={d:%Y%m%d}&selectType=ALLBUT0999&response=json',
    'tpex_flows': 'https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW&date={d:%Y}%2F{d:%m}%2F{d:%d}&response=json',
    'twse_valuation': 'https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?date={d:%Y%m%d}&selectType=ALL&response=json',
    'tpex_valuation': 'https://www.tpex.org.tw/www/zh-tw/afterTrading/peQryDate?date={d:%Y}%2F{d:%m}%2F{d:%d}&response=json',
    'twse_industry': 'https://openapi.twse.com.tw/v1/opendata/t187ap03_L',
    'tpex_industry': 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O',
}
# Columns of extras.csv. The second group is filled by ``auxiliary`` (margin and short
# balances in lots, securities-lending short balance in lots, day-traded shares, and the
# share of the company held in accounts of at least 400 and 1,000 lots).
AUX_FIELDS = ('margin', 'margin_change', 'short', 'short_change', 'sbl', 'sbl_change', 'daytrade',
              'big400_pct', 'big1000_pct', 'holders')
EXTRA_FIELDS = ('industry', 'pe', 'pb', 'dividend_yield', 'foreign', 'trust', 'dealer') + AUX_FIELDS
HISTORY_FIELDS = ('foreign', 'trust', 'dealer') + AUX_FIELDS
FLOW_DAYS, SHARE_DAYS, MFI_DAYS, OBV_DAYS = 5, 20, 14, 10
HISTORY_DAYS = 80   # enough for 60-day averages and MACD warm-up


def num(text):
    text = str(text if text is not None else '').replace(',', '').strip()
    try:
        return float(text) if text and text.strip('-') else None
    except ValueError:
        return None


def report_date(data):
    """The trading day a TWSE/TPEx JSON report says it describes."""
    value = str(data.get('date') or (data.get('tables') or [{}])[0].get('date') or '')
    digits = re.sub(r'\D', '', value)
    if len(digits) == 8:
        return dt.date(int(digits[:4]), int(digits[4:6]), int(digits[6:]))
    if len(digits) == 7:
        return dt.date(int(digits[:3]) + 1911, int(digits[3:5]), int(digits[5:]))
    return None


def rows_of(data):
    if 'fields' in data:
        return data['fields'], data.get('data') or []
    table = (data.get('tables') or [{}])[0]
    return table.get('fields') or [], table.get('data') or []


# --- parsers -------------------------------------------------------------------

def parse_twse_flows(data):
    fields, rows = rows_of(data)
    i = {f: n for n, f in enumerate(fields)}
    return {r[0].strip(): dict(
        foreign=(num(r[i['外陸資買賣超股數(不含外資自營商)']]) or 0) + (num(r[i['外資自營商買賣超股數']]) or 0),
        trust=num(r[i['投信買賣超股數']]) or 0, dealer=num(r[i['自營商買賣超股數']]) or 0) for r in rows}


def parse_tpex_flows(data):
    _, rows = rows_of(data)
    # Column groups: foreign ex-dealer, foreign dealer, foreign total, trust,
    # dealer proprietary, dealer hedge, dealer total, grand total.
    return {r[0].strip(): dict(foreign=num(r[10]) or 0, trust=num(r[13]) or 0, dealer=num(r[22]) or 0)
            for r in rows}


def parse_valuation(data):
    fields, rows = rows_of(data)
    i = {f.strip(): n for n, f in enumerate(fields)}
    code = i.get('證券代號', i.get('股票代號'))
    return {str(r[code]).strip(): dict(pe=num(r[i['本益比']]), pb=num(r[i['股價淨值比']]),
                                       dividend_yield=num(r[i['殖利率(%)']])) for r in rows}


def parse_industry(rows, code_field, industry_field):
    return {str(r[code_field]).strip(): INDUSTRIES.get(str(r[industry_field]).strip(), '未分類') for r in rows}


# --- fetching ------------------------------------------------------------------

def fetch_extras(trade_date, *, http=None, get=None):
    """Return (extras by symbol, status by source). Never raises for one bad source."""
    day = dt.date.fromisoformat(trade_date)
    http = http or HttpClient(timeout=60)
    get = get or (lambda url: persistent_json(http, url, attempts=15, pause=3.0))
    extras, status = {}, {}

    def merge(values):
        for symbol, record in values.items():
            extras.setdefault(symbol, {}).update(record)

    for name, parser in (('twse_flows', parse_twse_flows), ('tpex_flows', parse_tpex_flows),
                         ('twse_valuation', parse_valuation), ('tpex_valuation', parse_valuation)):
        try:
            data = get(URLS[name].format(d=day))
            fields, rows = rows_of(data) if isinstance(data, dict) else ([], [])
            if not rows:
                status[name] = 'not_published'
                continue
            if report_date(data) != day:
                status[name] = 'date_mismatch'
                continue
            merge(parser(data))
            status[name] = 'ok'
        except Exception as exc:  # one source must not lose the others
            status[name] = f'failed:{type(exc).__name__}'
    for name, code, field in (('twse_industry', '公司代號', '產業別'),
                              ('tpex_industry', 'SecuritiesCompanyCode', 'SecuritiesIndustryCode')):
        try:
            rows = get(URLS[name])
            if not isinstance(rows, list) or not rows:
                status[name] = 'not_published'
                continue
            merge({s: {'industry': v} for s, v in parse_industry(rows, code, field).items()})
            status[name] = 'ok'
        except Exception as exc:
            status[name] = f'failed:{type(exc).__name__}'
    return extras, status


SOURCE_FIELDS = {
    'twse_flows': ('TWSE', ('foreign', 'trust', 'dealer')), 'tpex_flows': ('TPEX', ('foreign', 'trust', 'dealer')),
    'twse_valuation': ('TWSE', ('pe', 'pb', 'dividend_yield')), 'tpex_valuation': ('TPEX', ('pe', 'pb', 'dividend_yield')),
    'twse_industry': ('TWSE', ('industry',)), 'tpex_industry': ('TPEX', ('industry',)),
}


def read_saved_extras(day_dir):
    """(extras by symbol, status) from an already saved snapshot, or ({}, {})."""
    day_dir = Path(day_dir)
    try:
        status = json.loads((day_dir / 'manifest.json').read_text(encoding='utf-8')).get('extras') or {}
        rows = read_csv(day_dir / 'extras.csv')
    except (OSError, ValueError):
        return {}, {}
    extras = {}
    for r in rows:
        extras[r['symbol']] = {f: (r.get(f) or None) if f == 'industry' else num(r.get(f)) for f in EXTRA_FIELDS}
    return extras, status


def read_saved_payloads(day_dir):
    """Market-level auxiliary payloads from an already saved snapshot, or {}."""
    try:
        context = json.loads((Path(day_dir) / 'context.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return context.get('auxiliary') or {}


def keep_previous(extras, status, previous, previous_status, market_of, *, fields=None,
                  payloads=None, previous_payloads=None):
    """For each source that did not succeed now but had in the saved snapshot, reuse the saved values.

    ``fields`` maps a source to (market or None, per-symbol columns); ``payloads``
    are the market-level results keyed by source.
    """
    fields = SOURCE_FIELDS if fields is None else fields
    kept = []
    for source, earlier in previous_status.items():
        if earlier != 'ok' or status.get(source) == 'ok':
            continue
        restored = False
        if source in fields:
            market, names = fields[source]
            for symbol, record in previous.items():
                if market is None or market_of.get(symbol, market) == market:
                    values = {f: record.get(f) for f in names if record.get(f) is not None}
                    if values:
                        extras.setdefault(symbol, {}).update(values)
            restored = True
        if payloads is not None and previous_payloads and source in previous_payloads:
            payloads[source] = previous_payloads[source]
            restored = True
        if restored:
            status[source] = 'ok'
            kept.append(source)
    return kept


CORE_SOURCES = ('twse_flows', 'tpex_flows', 'twse_valuation', 'tpex_valuation', 'twse_industry', 'tpex_industry',
                'twse_index', 'tpex_index')
SETTLED = ('ok', 'demo', 'not_requested', 'unavailable')


def core_complete(status):
    """Enough for the digest to be worth pushing: flows, valuation, industries and the indices."""
    return bool(status) and all(v in SETTLED for name, v in status.items() if name in CORE_SOURCES or name in (
        'extras', 'all'))


def extras_complete(status):
    return bool(status) and all(v in SETTLED for v in status.values())


def extras_csv(extras):
    text = io.StringIO(newline='')
    writer = csv.writer(text)
    writer.writerow(('symbol',) + EXTRA_FIELDS)
    for symbol in sorted(extras):
        writer.writerow([symbol] + ['' if extras[symbol].get(f) is None else extras[symbol][f]
                                    for f in EXTRA_FIELDS])
    return text.getvalue().encode('utf-8-sig')


# --- history -------------------------------------------------------------------

def read_csv(path):
    with Path(path).open(encoding='utf-8-sig') as handle:
        return list(csv.DictReader(handle))


def load_history(root, before, n=HISTORY_DAYS):
    """Up to ``n`` saved trading days before ``before``, oldest first.

    Each day is {symbol: row} with numeric fields, plus flows and the
    auxiliary per-symbol columns when that day's snapshot has them.
    """
    daily = Path(root) / 'daily'
    if not daily.is_dir():
        return []
    dates = sorted(p.name for p in daily.iterdir()
                   if re.fullmatch(r'\d{4}-\d{2}-\d{2}', p.name) and p.name < before and (p / 'quotes.csv').exists())
    days = []
    for name in dates[-n:]:
        rows = {}
        for r in read_csv(daily / name / 'quotes.csv'):
            rows[r['symbol']] = dict(market=r['market'], kind=r['kind'], name=r['name'],
                                     **{f: num(r[f]) for f in ('open', 'high', 'low', 'close', 'change_pct',
                                                               'volume_shares', 'turnover_twd')})
        has_flows = (daily / name / 'extras.csv').exists()
        if has_flows:
            for r in read_csv(daily / name / 'extras.csv'):
                if r['symbol'] in rows:
                    rows[r['symbol']].update({f: num(r.get(f)) for f in HISTORY_FIELDS})
        try:
            tdcc_date = json.loads((daily / name / 'manifest.json').read_text(encoding='utf-8')).get('tdcc_date')
        except (OSError, ValueError):
            tdcc_date = None
        days.append(dict(date=name, rows=rows, has_flows=has_flows, tdcc_date=tdcc_date))
    return days


def today_day(quotes, extras, tdcc_date=None):
    rows = {}
    for q in quotes:
        rows[q['symbol']] = dict(market=q['market'], kind=q['kind'], name=q['name'], open=q['open'],
                                 high=q['high'], low=q['low'], close=q['close'], change_pct=q['change_pct'],
                                 volume_shares=q['volume_shares'], turnover_twd=q['turnover_twd'],
                                 **{f: extras.get(q['symbol'], {}).get(f) for f in HISTORY_FIELDS})
    return dict(date=quotes[0]['trade_date'], rows=rows, tdcc_date=tdcc_date, has_flows=any(
        extras.get(s, {}).get('foreign') is not None for s in rows))


# --- calculations --------------------------------------------------------------

def pct(value):
    return None if value is None else round(value, 2)


def median(values):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 2) if values else None


def amount(row, field):
    shares, close = row.get(field), row.get('close')
    return shares * close if shares is not None and close else None


def to_100m(value):
    return None if value is None else round(value / 1e8, 2)  # TWD -> 億元


def valuation(today, extras, watchlist):
    equities = {s: r for s, r in today['rows'].items() if r['kind'] == 'equity'}
    markets = {}
    for market in sorted({r['market'] for r in equities.values()}):
        members = [extras.get(s, {}) for s, r in equities.items() if r['market'] == market]
        covered = [e for e in members if 'pb' in e or 'pe' in e]
        positive = [e['pe'] for e in covered if e.get('pe') and e['pe'] > 0]
        markets[market] = dict(median_pe=median(positive), median_pb=median(e.get('pb') for e in covered),
                               median_dividend_yield=median(e.get('dividend_yield') for e in covered),
                               with_pe=len(positive), without_pe=len(covered) - len(positive))
    industries = {}
    for s in equities:
        e = extras.get(s, {})
        if e.get('industry') and e.get('pe') and e['pe'] > 0:
            industries.setdefault(e['industry'], []).append(e['pe'])
    by_industry = {k: dict(median_pe=median(v), count=len(v)) for k, v in industries.items() if len(v) >= 5}
    rows = []
    for s in watchlist:
        if s in today['rows']:
            e = extras.get(s, {})
            ind = e.get('industry')
            rows.append(dict(symbol=s, name=today['rows'][s]['name'], industry=ind, pe=e.get('pe'),
                             industry_median_pe=by_industry.get(ind, {}).get('median_pe'),
                             pb=e.get('pb'), dividend_yield=e.get('dividend_yield')))
    return dict(markets=markets, industries=dict(sorted(by_industry.items(), key=lambda kv: kv[1]['median_pe'])),
                watchlist=rows)


def streak(days, symbol, field):
    """Consecutive days ending today with the same sign of net buying (+ buy, - sell)."""
    count, sign = 0, 0
    for day in reversed(days):
        if not day['has_flows']:
            break
        value = day['rows'].get(symbol, {}).get(field)
        current = (value > 0) - (value < 0) if value is not None else 0
        if current == 0 or (sign and current != sign):
            break
        sign, count = current, count + 1
    return sign * count


def institutions(days, watchlist, top=10):
    today = days[-1]
    flow_days = []
    for day in reversed(days):
        if not day['has_flows']:
            break
        flow_days.insert(0, day)
    window = flow_days[-FLOW_DAYS:]
    out = dict(days_available=len(flow_days), window_days=len(window))
    if not today['has_flows']:
        out['note'] = '本日法人資料尚未取得'
        return out
    totals = {}
    for market in ('TWSE', 'TPEX', 'ALL'):
        totals[market] = {f: to_100m(sum(amount(r, f) or 0 for r in today['rows'].values()
                                                   if market in ('ALL', r['market'])))
                          for f in ('foreign', 'trust', 'dealer')}
    out['today_100m_twd'] = totals
    out['foreign_series_100m_twd'] = [
        dict(date=d['date'], foreign=to_100m(sum(amount(r, 'foreign') or 0 for r in d['rows'].values())),
             trust=to_100m(sum(amount(r, 'trust') or 0 for r in d['rows'].values()))) for d in window]

    def total(day, field):
        return sum(amount(r, field) or 0 for r in day['rows'].values())

    def market_streak(field):
        count, sign = 0, 0
        for day in reversed(flow_days):
            value = total(day, field)
            current = (value > 0) - (value < 0)
            if current == 0 or (sign and current != sign):
                break
            sign, count = current, count + 1
        return sign * count

    out['market'] = {f: dict(today_100m_twd=totals['ALL'][f],
                             window_100m_twd=to_100m(sum(total(d, f) for d in window)),
                             streak_days=market_streak(f)) for f in ('foreign', 'trust', 'dealer')}

    def cumulative(symbol, field):
        values = [amount(d['rows'].get(symbol, {}), field) for d in window]
        return sum(v for v in values if v is not None) if any(v is not None for v in values) else None

    equities = [s for s, r in today['rows'].items() if r['kind'] == 'equity']

    def ranked(field, reverse, n):
        scored = [(cumulative(s, field), s) for s in equities]
        scored = [(v, s) for v, s in scored if v is not None and (v > 0 if reverse else v < 0)]
        scored.sort(key=lambda x: (-x[0], x[1]) if reverse else (x[0], x[1]))
        return [dict(symbol=s, name=today['rows'][s]['name'], net_100m_twd=to_100m(v),
                     streak_days=streak(days, s, field), change_pct=pct(today['rows'][s]['change_pct']))
                for v, s in scored[:n]]

    out['foreign_buy'] = ranked('foreign', True, top)
    out['foreign_sell'] = ranked('foreign', False, 5)
    out['trust_buy'] = ranked('trust', True, top)
    out['trust_sell'] = ranked('trust', False, 5)
    out['watchlist'] = [dict(symbol=s, name=today['rows'][s]['name'],
                             foreign_today_100m_twd=to_100m(amount(today['rows'][s], 'foreign')),
                             foreign_window_100m_twd=to_100m(cumulative(s, 'foreign')),
                             foreign_streak_days=streak(days, s, 'foreign'),
                             trust_today_100m_twd=to_100m(amount(today['rows'][s], 'trust')),
                             trust_window_100m_twd=to_100m(cumulative(s, 'trust')),
                             trust_streak_days=streak(days, s, 'trust'))
                        for s in watchlist if s in today['rows']]
    return out


def compounded(days, symbol, n):
    values = [d['rows'].get(symbol, {}).get('change_pct') for d in days[-n:]]
    if len(values) < n or any(v is None for v in values):
        return None
    total = 1.0
    for v in values:
        total *= 1 + v / 100
    return round(100 * (total - 1), 2)


def rotation(days, extras, top=5):
    today = days[-1]
    industry = {s: e['industry'] for s, e in extras.items() if e.get('industry')}

    def shares(day):
        totals, overall = {}, 0.0
        for s, r in day['rows'].items():
            if r['kind'] == 'equity' and s in industry and r.get('turnover_twd'):
                totals[industry[s]] = totals.get(industry[s], 0.0) + r['turnover_twd']
                overall += r['turnover_twd']
        return {k: v / overall for k, v in totals.items()} if overall else {}

    past = days[-SHARE_DAYS - 1:-1]
    out = dict(days_available=len(past))
    if len(past) < SHARE_DAYS // 2:
        out['note'] = f'歷史僅 {len(past)} 個交易日，需至少 {SHARE_DAYS // 2} 日才計算成交占比變化'
        return out
    now, history = shares(today), [shares(d) for d in past]
    flow_window = [d for d in days[-FLOW_DAYS:] if d['has_flows']]
    rows = []
    for name, share in now.items():
        members = [s for s, k in industry.items() if k == name and today['rows'].get(s, {}).get('kind') == 'equity']
        if len(members) < 5:
            continue
        average = statistics.fmean(h.get(name, 0.0) for h in history)
        foreign = sum(amount(d['rows'].get(s, {}), 'foreign') or 0 for d in flow_window for s in members)
        rows.append(dict(industry=name, members=len(members), turnover_share_pct=round(100 * share, 2),
                         share_average_pct=round(100 * average, 2),
                         share_change_pp=round(100 * (share - average), 2),
                         median_return_5d_pct=median(compounded(days, s, FLOW_DAYS) for s in members),
                         foreign_window_100m_twd=to_100m(foreign) if flow_window else None))
    rows.sort(key=lambda r: -r['share_change_pp'])
    out.update(window_days=len(flow_window), gaining=rows[:top], losing=list(reversed(rows[-top:])),
               industries=len(rows))
    return out


def mfi(days, symbol, n=MFI_DAYS):
    rows = [d['rows'].get(symbol) for d in days[-(n + 1):]]
    if len(rows) < n + 1 or any(r is None or None in (r.get('high'), r.get('low'), r.get('close'))
                                or not r.get('volume_shares') for r in rows):
        return None
    typical = [(r['high'] + r['low'] + r['close']) / 3 for r in rows]
    positive = negative = 0.0
    for prev, cur, r in zip(typical, typical[1:], rows[1:]):
        flow = cur * r['volume_shares']
        if cur > prev:
            positive += flow
        elif cur < prev:
            negative += flow
    if positive + negative == 0:
        return None
    return round(100.0 if negative == 0 else 100 - 100 / (1 + positive / negative), 1)


def obv(days, symbol, n=OBV_DAYS):
    """Net up-day minus down-day volume over n days, in days of average volume."""
    rows = [d['rows'].get(symbol) for d in days[-n:]]
    if len(rows) < n or any(r is None or r.get('change_pct') is None or not r.get('volume_shares') for r in rows):
        return None
    net = sum(r['volume_shares'] * ((r['change_pct'] > 0) - (r['change_pct'] < 0)) for r in rows)
    return round(net / statistics.fmean(r['volume_shares'] for r in rows), 2)


def money_flow(days, watchlist, top=10, *, min_turnover=50_000_000, surge=2.0):
    today, past = days[-1], days[-SHARE_DAYS - 1:-1]
    out = dict(days_available=len(days) - 1)
    rows = []
    for s, r in today['rows'].items():
        if r['kind'] != 'equity' or not r.get('turnover_twd'):
            continue
        previous = [d['rows'][s]['turnover_twd'] for d in past if d['rows'].get(s, {}).get('turnover_twd')]
        if len(previous) < SHARE_DAYS // 2:
            continue
        average = statistics.fmean(previous)
        rows.append((s, r, average))
    if not rows:
        out['note'] = f'歷史僅 {len(past)} 個交易日，需至少 {SHARE_DAYS // 2} 日才計算量增倍數'
    surges = [(r['turnover_twd'] / a, s, r) for s, r, a in rows
              if r['turnover_twd'] >= min_turnover and a > 0 and r['turnover_twd'] >= surge * a
              and (r.get('change_pct') or 0) > 0]
    surges.sort(key=lambda x: (-x[0], x[1]))
    out['volume_surge_up'] = [dict(symbol=s, name=r['name'], turnover_ratio=round(x, 2),
                                   turnover_100m_twd=to_100m(r['turnover_twd']), change_pct=pct(r['change_pct']))
                              for x, s, r in surges[:top]]
    liquid = [s for s, r, a in rows if a >= min_turnover]
    scored = [(mfi(days, s), s) for s in liquid]
    scored = [(v, s) for v, s in scored if v is not None]
    out['mfi_high'] = [dict(symbol=s, name=today['rows'][s]['name'], mfi=v)
                       for v, s in sorted((x for x in scored if x[0] >= 80), key=lambda x: (-x[0], x[1]))[:top]]
    out['mfi_low'] = [dict(symbol=s, name=today['rows'][s]['name'], mfi=v)
                      for v, s in sorted((x for x in scored if x[0] <= 20), key=lambda x: (x[0], x[1]))[:5]]
    out['watchlist'] = [dict(symbol=s, name=today['rows'][s]['name'], mfi14=mfi(days, s), obv10_days=obv(days, s))
                        for s in watchlist if s in today['rows']]
    return out


def compute_days(days, extras, watchlist):
    return dict(valuation=valuation(days[-1], extras, watchlist),
                institutions=institutions(days, watchlist),
                rotation=rotation(days, extras),
                money_flow=money_flow(days, watchlist))


def compute(quotes, extras, history, watchlist):
    return compute_days(history + [today_day(quotes, extras)], extras, watchlist)
