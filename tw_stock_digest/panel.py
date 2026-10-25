"""Turn cached historical reports into an aligned, look-ahead-safe price panel.

Daily return is ``close / reference - 1``. On ordinary days the reference is
the previous close (official close minus official change). On ex-rights or
ex-dividend days the exchanges publish no change, so the reference comes from
their ex-rights tables; the result approximates a total return. Days whose
reference cannot be established keep ``ret = None`` and are never invented.
"""
import datetime as dt
import json
from pathlib import Path
import re

from .history import daily_path

EQUITY = re.compile(r'[1-9]\d{3}')
FUND = re.compile(r'00[0-9A-Z]{2,4}')
INDICES = ('發行量加權股價報酬指數', '臺灣50報酬指數')
FIELDS = ('open', 'high', 'low', 'close', 'volume', 'turnover', 'ref', 'ret',
          'foreign', 'trust', 'dealer')


def num(text):
    text = str(text).replace(',', '').strip()
    if not text or text.strip('-') == '' or text.startswith(('-.', '--')):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def roc_date(text):
    digits = re.sub(r'\D', '', str(text))
    year, month, day = int(digits[:-4]), int(digits[-4:-2]), int(digits[-2:])
    return dt.date(year + 1911 if year < 1911 else year, month, day)


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).exists() else None


def ex_reference(cache, dataset):
    """{(date, symbol): reference price} from monthly ex-rights files."""
    refs = {}
    for path in sorted((Path(cache) / dataset).glob('*.json')):
        data = load(path)
        if dataset == 'twse_exright':
            rows = data.get('data') or []
            date_col, sym_col, ref_col = 0, 1, 4
        else:
            tables = data.get('tables') or [{}]
            rows = tables[0].get('data') or []
            date_col, sym_col, ref_col = 0, 1, 4
        for row in rows:
            ref = num(row[ref_col])
            if ref and ref > 0:
                refs[(roc_date(row[date_col]), str(row[sym_col]).strip())] = ref
    return refs


def twse_indices(data):
    """Official total-return index closes published in the same daily report."""
    out = {}
    for table in data.get('tables', []):
        if '報酬指數' in (table.get('title') or ''):
            for row in table.get('data') or []:
                if row[0] in INDICES:
                    out[row[0]] = num(row[1])
    return out


def twse_rows(data):
    if not data or data.get('stat') != 'OK':
        return None
    for table in data.get('tables', []):
        fields = table.get('fields') or []
        if '證券代號' in fields and '收盤價' in fields:
            c = {name: fields.index(name) for name in fields}
            out = []
            for r in table['data']:
                sign = re.sub(r'<[^>]+>', '', r[c['漲跌(+/-)']]).strip()
                change = num(r[c['漲跌價差']])
                if sign == 'X':
                    change = 'X'
                elif change is not None and sign == '-':
                    change = -change
                out.append(dict(symbol=r[c['證券代號']].strip(), name=r[c['證券名稱']].strip(),
                                open=num(r[c['開盤價']]), high=num(r[c['最高價']]), low=num(r[c['最低價']]),
                                close=num(r[c['收盤價']]), change=change,
                                volume=num(r[c['成交股數']]), turnover=num(r[c['成交金額']])))
            return out
    raise ValueError('TWSE daily quote table not found')


def tpex_rows(data):
    if not data or str(data.get('stat', '')).lower() != 'ok':
        return None
    tables = data.get('tables') or []
    if not tables or not tables[0].get('data'):
        return None
    fields = [f.strip() for f in tables[0]['fields']]
    c = {name: fields.index(name) for name in fields}
    out = []
    for r in tables[0]['data']:
        raw = r[c['漲跌']].strip()
        change = num(raw) if raw[:1] in '+-0123456789' else ('X' if '除' in raw else None)
        out.append(dict(symbol=r[c['代號']].strip(), name=r[c['名稱']].strip(),
                        open=num(r[c['開盤']]), high=num(r[c['最高']]), low=num(r[c['最低']]),
                        close=num(r[c['收盤']]), change=change,
                        volume=num(r[c['成交股數']]), turnover=num(r[c['成交金額(元)']])))
    return out


def twse_flows(data):
    if not data or data.get('stat') != 'OK':
        return {}
    f = data['fields']
    i = {name: f.index(name) for name in f}
    out = {}
    for r in data['data']:
        out[r[0].strip()] = dict(
            foreign=(num(r[i['外陸資買賣超股數(不含外資自營商)']]) or 0) + (num(r[i['外資自營商買賣超股數']]) or 0),
            trust=num(r[i['投信買賣超股數']]) or 0, dealer=num(r[i['自營商買賣超股數']]) or 0)
    return out


def tpex_flows(data):
    tables = (data or {}).get('tables') or []
    if not tables or not tables[0].get('data'):
        return {}
    # Column groups: foreign ex-dealer, foreign dealer, foreign total, trust,
    # dealer proprietary, dealer hedge, dealer total, then the grand total.
    return {r[0].strip(): dict(foreign=num(r[10]) or 0, trust=num(r[13]) or 0, dealer=num(r[22]) or 0)
            for r in tables[0]['data']}


class Panel:
    """Column store: ``self.data[field][symbol]`` is a list aligned with ``dates``."""

    def __init__(self, dates, data, names, markets, flows_available, indices=None):
        self.dates, self.data, self.names, self.markets = dates, data, names, markets
        self.indices = indices or {}
        self.flows_available = flows_available
        self.index = {d: i for i, d in enumerate(dates)}
        self.symbols = sorted(names)

    def kind(self, symbol):
        return 'equity' if EQUITY.fullmatch(symbol) else 'etf' if FUND.fullmatch(symbol) else 'other'


def build(cache, start=None, end=None, *, symbols=None, log=None):
    cache = Path(cache)
    refs = {**ex_reference(cache, 'twse_exright'), **ex_reference(cache, 'tpex_exright')}
    days = sorted(dt.date(int(p.stem[:4]), int(p.stem[4:6]), int(p.stem[6:]))
                  for p in (cache / 'twse_price').glob('*/*.json'))
    days = [d for d in days if (start is None or d >= start) and (end is None or d <= end)]
    records, flows_seen = [], set()
    for day in days:
        twse_raw = load(daily_path(cache, 'twse_price', day))
        markets = {'TWSE': twse_rows(twse_raw),
                   'TPEX': tpex_rows(load(daily_path(cache, 'tpex_price', day)))}
        if markets['TWSE'] is None:
            continue  # holiday or typhoon closure
        if markets['TPEX'] is None:
            raise ValueError(f'{day}: TWSE traded but TPEx data is missing')
        flows = {}
        for name, parser in (('twse_inst', twse_flows), ('tpex_inst', tpex_flows)):
            raw = load(daily_path(cache, name, day))
            if raw is not None:
                flows_seen.add((name, day))
                flows.update(parser(raw))
        records.append((day, markets, flows, twse_indices(twse_raw)))
    dates = [r[0] for r in records]
    n = len(dates)
    data = {f: {} for f in FIELDS}
    names, market_of = {}, {}
    indices = {name: [r[3].get(name) for r in records] for name in INDICES}
    for i, (day, markets, flows, _) in enumerate(records):
        for market, rows in markets.items():
            for row in rows:
                s = row['symbol']
                if not (EQUITY.fullmatch(s) or FUND.fullmatch(s)) or (symbols and s not in symbols):
                    continue
                if s not in names:
                    for f in FIELDS:
                        data[f][s] = [None] * n
                names[s], market_of[s] = row['name'], market
                for f in ('open', 'high', 'low', 'close', 'volume', 'turnover'):
                    data[f][s][i] = row[f]
                close, change = row['close'], row['change']
                if change == 'X':
                    ref = refs.get((day, s))
                elif close is not None and isinstance(change, float):
                    ref = round(close - change, 4)
                else:
                    ref = None
                data['ref'][s][i] = ref
                if close is not None and ref and ref > 0:
                    data['ret'][s][i] = close / ref - 1
                if s in flows:
                    for f in ('foreign', 'trust', 'dealer'):
                        data[f][s][i] = flows[s][f]
    flows_available = [all((name, d) in flows_seen for name in ('twse_inst', 'tpex_inst')) for d in dates]
    if log:
        log(f'panel: {n} trading days, {len(names)} symbols')
    return Panel(dates, data, names, market_of, flows_available, indices)


def from_quotes(quotes):
    """A one-day panel from the daily pipeline's normalized quotes (``market.normalize``)."""
    if not quotes:
        raise ValueError('no quotes')
    dates = sorted({dt.date.fromisoformat(q['trade_date']) for q in quotes})
    if len(dates) != 1:
        raise ValueError('quotes must share one trade date')
    data = {f: {} for f in FIELDS}
    names, markets = {}, {}
    for q in quotes:
        s = q['symbol']
        names[s], markets[s] = q['name'], q['market']
        close, change = q['close'], q['change']
        ref = round(close - change, 4) if close is not None and change is not None else None
        values = dict(open=q['open'], high=q['high'], low=q['low'], close=close,
                      volume=q['volume_shares'], turnover=q['turnover_twd'], ref=ref,
                      ret=close / ref - 1 if close is not None and ref and ref > 0 else None,
                      foreign=None, trust=None, dealer=None)
        for f in FIELDS:
            data[f][s] = [values[f]]
    return Panel(dates, data, names, markets, [False])


def screen_snapshot(quotes, names, **params):
    """Run snapshot screens on one day of pipeline quotes: {name: [Pick, ...]}."""
    from .strategies import MarketView, Screen, get
    view = MarketView(from_quotes(quotes), 0)
    out = {}
    for name in names:
        strategy = get(name, **params)
        if not isinstance(strategy, Screen):
            raise ValueError(f'{name} needs history; it cannot run on a single snapshot')
        out[name] = strategy.select(view)
    return out
