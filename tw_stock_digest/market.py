"""Normalize official latest-day snapshots; do not invent missing observations."""
import datetime as dt
import re
from decimal import Decimal, InvalidOperation

SOURCES = {
    'TWSE': 'https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL',
    'TPEX': 'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes',
}
FIELDS = {
    'TWSE': dict(symbol='Code', name='Name', open='OpeningPrice', high='HighestPrice',
                 low='LowestPrice', close='ClosingPrice', change='Change',
                 volume_shares='TradeVolume', turnover_twd='TradeValue', trades='Transaction'),
    'TPEX': dict(symbol='SecuritiesCompanyCode', name='CompanyName', open='Open',
                 high='High', low='Low', close='Close', change='Change',
                 volume_shares='TradingShares', turnover_twd='TransactionAmount',
                 trades='TransactionNumber'),
}
NULLS = {'', '--', '---', '----', '---.--', 'X', 'N/A', '除權', '除息', '除權息'}


def parse_date(value):
    value = str(value).strip()
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return dt.date.fromisoformat(value).isoformat()
    digits = value.replace('/', '')
    if not re.fullmatch(r'\d{7,8}', digits):
        raise ValueError('Invalid source date')
    offset = 3 if len(digits) == 7 else 4
    year = int(digits[:offset]) + (1911 if offset == 3 else 0)
    return dt.date(year, int(digits[offset:offset + 2]), int(digits[offset + 2:])).isoformat()


def number(value, *, integer=False, nonnegative=False):
    if value is None:
        return None
    text = str(value).strip().replace('−', '-')
    if text.upper() in NULLS:
        return None
    if not re.fullmatch(r'[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?', text):
        raise ValueError('Unexpected numeric syntax or thousands grouping')
    try:
        n = Decimal(text.replace(',', ''))
    except InvalidOperation:
        raise ValueError('Unexpected numeric field') from None
    if not n.is_finite() or (nonnegative and n < 0) or (integer and n != n.to_integral_value()):
        raise ValueError('Invalid numeric field')
    return int(n) if integer else float(n)


def normalize(market, rows):
    if market not in FIELDS or not isinstance(rows, list) or not rows:
        raise ValueError('Empty or invalid market response')
    mapping = FIELDS[market]
    quotes, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or mapping['symbol'] not in row:
            raise ValueError(f'{market}: missing symbol schema')
        symbol = str(row[mapping['symbol']]).strip()
        if re.fullmatch(r'[1-9]\d{3}', symbol):
            kind = 'equity'
        elif re.fullmatch(r'00[0-9A-Z]{2,4}', symbol):
            kind = 'etf'
        else:
            continue
        if not set(mapping.values()).union({'Date'}).issubset(row):
            raise ValueError(f'{market}: missing expected fields')
        if symbol in seen:
            raise ValueError(f'{market}: duplicate symbol {symbol}')
        seen.add(symbol)
        q = dict(market=market, trade_date=parse_date(row['Date']), symbol=symbol,
                 name=str(row[mapping['name']]), kind=kind, quality_flags=[])
        for field in ('open', 'high', 'low', 'close', 'change', 'volume_shares', 'turnover_twd', 'trades'):
            q[field] = number(row[mapping[field]], integer=field in ('volume_shares', 'trades'),
                              nonnegative=field != 'change')
        if q['close'] is None:
            q['quality_flags'].append('missing_close')
        if q['change'] is None:
            q['quality_flags'].append('missing_or_corporate_action_change')
        if q['high'] is not None and q['low'] is not None:
            if q['high'] < q['low'] or any(q[f] is not None and not q['low'] <= q[f] <= q['high']
                                         for f in ('open', 'close')):
                raise ValueError(f'{market}: inconsistent OHLC for {symbol}')
        reference = q['close'] - q['change'] if q['close'] is not None and q['change'] is not None else None
        q['change_pct'] = round(100 * q['change'] / reference, 4) if reference and reference > 0 else None
        quotes.append(q)
    if not quotes:
        raise ValueError(f'{market}: no supported securities')
    return sorted(quotes, key=lambda q: q['symbol'])


def build_context(quotes, watchlist):
    dates = {q['trade_date'] for q in quotes}
    if len(dates) != 1:
        raise ValueError('Inconsistent or empty source date')
    markets = {}
    for market in sorted({q['market'] for q in quotes}):
        rows = [q for q in quotes if q['market'] == market]
        equity = [q for q in rows if q['kind'] == 'equity']
        changes = [q['change'] for q in equity if q['close'] is not None and q['change'] is not None]
        markets[market] = dict(
            source=SOURCES[market], included_count=len(rows), etf_count=sum(q['kind'] == 'etf' for q in rows),
            equities=dict(count=len(equity), advancers=sum(c > 0 for c in changes),
                          decliners=sum(c < 0 for c in changes), unchanged=sum(c == 0 for c in changes),
                          unknown_change_count=len(equity) - len(changes),
                          turnover_twd=round(sum(q['turnover_twd'] or 0 for q in equity), 2),
                          missing_turnover_count=sum(q['turnover_twd'] is None for q in equity)),
            top_turnover=sorted((q for q in equity if q['turnover_twd'] is not None),
                                key=lambda q: (-q['turnover_twd'], q['symbol']))[:5])
    selected = [q for q in quotes if q['symbol'] in watchlist]
    found = {q['symbol'] for q in selected}
    return dict(trade_date=next(iter(dates)), markets=markets, watchlist=selected,
                missing_watchlist=[s for s in watchlist if s not in found],
                limitations=[
                    '僅當日快照：沒有新聞、財報、法人買賣超或歷史序列。',
                    '摘要涵蓋四碼股票類證券與 00 開頭 ETF；權證及其他代碼未納入。',
                    '市場統計僅為上述涵蓋範圍，並非加權指數或官方市場總成交值。',
                    'change_pct 由官方價差與推算參考價換算，並非含息報酬；除權息需另查。',
                ])
