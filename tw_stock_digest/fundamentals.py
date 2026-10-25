"""Point-in-time fundamentals: monthly revenue, valuation, margin and financial statements.

Each record is attached to the first trading day on which it was public, so a
strategy can never use a number before investors could have seen it:

- monthly revenue: listed firms must report by the 10th of the next month; a
  month's revenue is usable from the first trading day after the 10th;
- financial statements: usable from the first trading day after the latest
  statutory deadline for that quarter across industries (Q1 May 31, Q2 Aug 31,
  Q3 Nov 30, full year Mar 31), so early filers are not favoured by hindsight;
- valuation and margin reports describe their own trading day (published that
  evening), so they are usable at that day's close for orders the next morning.
"""
import bisect
import datetime as dt
from html.parser import HTMLParser
import json
from pathlib import Path
import re

from .panel import num

STATEMENT_DEADLINE = {1: (5, 31), 2: (8, 31), 3: (11, 30), 4: (3, 31)}


class Tables(HTMLParser):
    """Collect every <table> as a list of rows; each row is a list of cell texts."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self._stack = [], []

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self._stack.append(dict(rows=[], row=None, cell=None))
        elif self._stack:
            t = self._stack[-1]
            if tag == 'tr':
                t['row'] = []
            elif tag in ('td', 'th') and t['row'] is not None:
                t['cell'] = []
            elif tag == 'br' and t['cell'] is not None:
                t['cell'].append('')

    def handle_endtag(self, tag):
        if not self._stack:
            return
        t = self._stack[-1]
        if tag in ('td', 'th') and t['cell'] is not None:
            t['row'].append(re.sub(r'\s+', '', ''.join(t['cell'])))
            t['cell'] = None
        elif tag == 'tr' and t['row'] is not None:
            if t['row']:
                t['rows'].append(t['row'])
            t['row'] = None
        elif tag == 'table':
            self.tables.append(self._stack.pop()['rows'])

    def handle_data(self, data):
        if self._stack and self._stack[-1]['cell'] is not None:
            self._stack[-1]['cell'].append(data)


def tables(raw):
    text = raw.decode('cp950', errors='replace') if b'charset=big5' in raw[:2000].lower() or not _utf8(raw) else raw.decode('utf-8')
    parser = Tables()
    parser.feed(text)
    return parser.tables


def _utf8(raw):
    try:
        raw.decode('utf-8')
        return True
    except UnicodeDecodeError:
        return False


# --- parsers -------------------------------------------------------------------

def parse_revenue(raw):
    """{symbol: (revenue, same month last year, published YoY %)}; amounts in thousand TWD."""
    out = {}
    for rows in tables(raw):
        for row in rows:
            if len(row) >= 7 and re.fullmatch(r'\d{4}', row[0]):
                current, last_year = num(row[2]), num(row[4])
                if current is not None:
                    out[row[0]] = (current, last_year, num(row[6]))
    return out


INCOME = {
    'revenue': lambda h: h in ('營業收入', '收益', '收入'),
    'gross_profit': lambda h: h.startswith('營業毛利（毛損）淨額'),
    'gross_profit_raw': lambda h: h == '營業毛利（毛損）',
    'net_income_parent': lambda h: h.startswith('淨利（') and h.endswith('歸屬於母公司業主'),
    'eps': lambda h: h.startswith('基本每股盈餘'),
}
BALANCE = {
    'assets': lambda h: h in ('資產總計', '資產總額'),
    'equity_parent': lambda h: h == '歸屬於母公司業主之權益合計',
    'equity': lambda h: h in ('權益總計', '權益總額'),
}


def parse_statement(raw, fields):
    """{symbol: {field: value}} from a MOPS summary page with one table per industry."""
    out = {}
    for rows in tables(raw):
        header = next((r for r in rows if r and r[0] == '公司代號'), None)
        if header is None:
            continue
        columns = {}
        for field, match in fields.items():
            for i, h in enumerate(header):
                if match(h):
                    columns.setdefault(field, i)
        for row in rows:
            if len(row) == len(header) and re.fullmatch(r'\d{4}', row[0]):
                record = {f: num(row[i]) for f, i in columns.items()}
                if 'gross_profit' not in record and 'gross_profit_raw' in record:
                    record['gross_profit'] = record['gross_profit_raw']
                record.pop('gross_profit_raw', None)
                out[row[0]] = record
    return out


def parse_valuation(data, market):
    if market == 'TWSE':
        if not data or data.get('stat') != 'OK':
            return {}
        fields, rows = data['fields'], data['data']
    else:
        t = (data or {}).get('tables') or [{}]
        fields, rows = t[0].get('fields') or [], t[0].get('data') or []
    i = {f.strip(): n for n, f in enumerate(fields)}
    code = i.get('證券代號', i.get('股票代號'))
    out = {}
    for r in rows:
        out[str(r[code]).strip()] = dict(pe=num(r[i['本益比']]), pb=num(r[i['股價淨值比']]),
                                          dividend_yield=num(r[i['殖利率(%)']]))
    return out


def parse_margin(data, market):
    """{symbol: (margin_balance_lots, short_balance_lots)}."""
    if market == 'TWSE':
        table = next((t for t in (data or {}).get('tables', []) if '彙總' in (t.get('title') or '')), None)
        if not table:
            return {}
        # Columns: code, name, margin buy, sell, repay, previous, today, limit, short ...
        return {r[0].strip(): (num(r[6]), num(r[12])) for r in table['data']}
    t = (data or {}).get('tables') or [{}]
    fields = [f.strip() for f in t[0].get('fields') or []]
    if not fields:
        return {}
    a, b = fields.index('資餘額'), fields.index('券餘額')
    return {r[0].strip(): (num(r[a]), num(r[b])) for r in t[0]['data']}


# --- point-in-time store -------------------------------------------------------

class Facts:
    """facts[dataset][symbol] = (available indices, records), both sorted by availability."""

    def __init__(self):
        self.data = {}

    def add(self, dataset, symbol, index, record):
        idx, recs = self.data.setdefault(dataset, {}).setdefault(symbol, ([], []))
        pos = bisect.bisect_right(idx, index)
        idx.insert(pos, index)
        recs.insert(pos, record)

    def latest(self, dataset, symbol, index, n=1):
        """Up to n most recent records public by trading day ``index``, oldest first."""
        entry = self.data.get(dataset, {}).get(symbol)
        if not entry:
            return []
        pos = bisect.bisect_right(entry[0], index)
        return entry[1][max(0, pos - n):pos]


def first_trading_index(dates, day):
    """Index of the first trading day on or after ``day`` (None if beyond the panel)."""
    pos = bisect.bisect_left(dates, day)
    return pos if pos < len(dates) else None


def revenue_available(year, month):
    following = dt.date(year + (month == 12), month % 12 + 1, 11)
    return following


def statement_available(year, quarter):
    m, d = STATEMENT_DEADLINE[quarter]
    return dt.date(year + (quarter == 4), m, d) + dt.timedelta(days=1)


def load(path):
    return Path(path).read_bytes() if Path(path).exists() else None


def attach(panel, cache, log=None):
    """Parse every cached fundamentals file and attach a ``Facts`` store to the panel."""
    cache, facts, dates = Path(cache), Facts(), panel.dates
    counts = {}

    def put(dataset, symbol, day, record):
        i = first_trading_index(dates, day)
        if i is not None:
            facts.add(dataset, symbol, i, record)
            counts[dataset] = counts.get(dataset, 0) + 1

    # Monthly revenue.
    for path in sorted((cache / 'mops_revenue').glob('*/*.html')):
        year, month = int(path.stem[:4]), int(path.stem[4:])
        for symbol, (rev, last, published) in parse_revenue(path.read_bytes()).items():
            put('revenue', symbol, revenue_available(year, month),
                dict(period=(year, month), revenue=rev, last_year=last, published_yoy=published))
    # Quarterly statements (cumulative within the year).
    statements = {}
    for kind, fields in (('mops_income', INCOME), ('mops_balance', BALANCE)):
        for path in sorted((cache / kind).glob('*/*.html')):
            year, q = int(path.stem[:4]), int(path.stem[5])
            for symbol, rec in parse_statement(path.read_bytes(), fields).items():
                statements.setdefault((symbol, year, q), {}).update(rec)
    for (symbol, year, q), rec in sorted(statements.items()):
        cumulative = {f: rec.get(f) for f in ('revenue', 'gross_profit', 'net_income_parent', 'eps')}
        single = dict(cumulative)
        if q > 1:
            prior = statements.get((symbol, year, q - 1))
            for f in single:
                a, b = cumulative[f], prior.get(f) if prior else None
                single[f] = a - b if a is not None and b is not None else None
        put('statement', symbol, statement_available(year, q),
            dict(period=(year, q), assets=rec.get('assets'),
                 equity=rec.get('equity_parent') if rec.get('equity_parent') is not None else rec.get('equity'),
                 **{f'q_{f}': v for f, v in single.items()}))
    # Valuation and margin: usable at their own day's close.
    for market, prefix in (('TWSE', 'twse'), ('TPEX', 'tpex')):
        for kind, parser, dataset in ((f'{prefix}_valuation', parse_valuation, 'valuation'),
                                      (f'{prefix}_margin', parse_margin, 'margin')):
            for path in sorted((cache / kind).glob('*/*.json')):
                day = dt.date(int(path.stem[:4]), int(path.stem[4:6]), int(path.stem[6:]))
                if day not in panel.index:
                    continue
                for symbol, value in parser(json.loads(path.read_text(encoding='utf-8')), market).items():
                    record = value if isinstance(value, dict) else dict(margin=value[0], short=value[1])
                    record['date'] = day
                    put(dataset, symbol, day, record)
    panel.facts = facts
    if log:
        log('fundamentals: ' + ', '.join(f'{k} {v:,}' for k, v in sorted(counts.items())))
    return facts
