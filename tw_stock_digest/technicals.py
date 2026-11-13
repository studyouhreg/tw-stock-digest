"""Technical indicators from the saved daily quotes; no extra data source.

Prices are unadjusted closes, so a stock that went ex-dividend inside a window
shows a small artificial drop in its averages. Definitions follow common
Taiwanese charting practice:

- MA bias: close / simple moving average - 1 (5, 20 and 60 days);
- RSI(14): average gain / (average gain + average loss) over 14 changes;
- KD(9): RSV = (close - 9-day low) / (9-day high - 9-day low); K and D are
  2/3 of yesterday's value plus 1/3 of today's input, seeded at 50;
- MACD(12, 26, 9): DIF = EMA12 - EMA26, signal = EMA9 of DIF, histogram = DIF - signal.

Every figure needs a minimum number of saved days and is left empty otherwise.
"""
import statistics

BREADTH_MIN_TURNOVER = 20_000_000
LIST_MIN_TURNOVER = 100_000_000


def series(days, symbol, field='close'):
    """The symbol's values on the days it traded, oldest first."""
    return [d['rows'][symbol][field] for d in days
            if symbol in d['rows'] and d['rows'][symbol].get(field) is not None]


def bias(closes, n):
    if len(closes) < n:
        return None
    return round(100 * (closes[-1] / statistics.fmean(closes[-n:]) - 1), 2)


def rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    changes = [b - a for a, b in zip(closes[-n - 1:], closes[-n:])]
    gain, loss = sum(c for c in changes if c > 0), -sum(c for c in changes if c < 0)
    return round(100 * gain / (gain + loss), 1) if gain + loss else None


def kd(days, symbol, n=9):
    rows = [d['rows'][symbol] for d in days if symbol in d['rows']
            and None not in (d['rows'][symbol].get('high'), d['rows'][symbol].get('low'), d['rows'][symbol].get('close'))]
    if len(rows) < n + 5:  # a few days for the smoothing to move away from its seed
        return None, None
    k = d_value = 50.0
    for i in range(n - 1, len(rows)):
        window = rows[i - n + 1:i + 1]
        high, low = max(r['high'] for r in window), min(r['low'] for r in window)
        rsv = 100 * (rows[i]['close'] - low) / (high - low) if high > low else 50.0
        k = 2 / 3 * k + rsv / 3
        d_value = 2 / 3 * d_value + k / 3
    return round(k, 1), round(d_value, 1)


def ema(values, n):
    out, alpha = [values[0]], 2 / (n + 1)
    for v in values[1:]:
        out.append(out[-1] + alpha * (v - out[-1]))
    return out


def macd(closes, fast=12, slow=26, signal=9):
    """(DIF, histogram) as a percentage of the close, or (None, None) with too little history."""
    if len(closes) < slow + signal:
        return None, None
    dif = [a - b for a, b in zip(ema(closes, fast), ema(closes, slow))]
    histogram = dif[-1] - ema(dif, signal)[-1]
    return round(100 * dif[-1] / closes[-1], 2), round(100 * histogram / closes[-1], 2)


def position(closes, n):
    """Where the close sits in its n-day range: 0 at the low, 100 at the high."""
    if len(closes) < n:
        return None
    high, low = max(closes[-n:]), min(closes[-n:])
    return round(100 * (closes[-1] - low) / (high - low), 1) if high > low else None


def compute(days, watchlist, top=10):
    today = days[-1]
    available = len(days)
    out = dict(days_available=available)
    window = 60 if available >= 60 else 20 if available >= 20 else None
    counted = above20 = above60 = counted60 = highs = lows = 0
    new_highs = []
    for symbol, row in today['rows'].items():
        if row['kind'] != 'equity' or row.get('close') is None or (row.get('turnover_twd') or 0) < BREADTH_MIN_TURNOVER:
            continue
        closes = series(days, symbol)
        if len(closes) < 20:
            continue
        counted += 1
        above20 += closes[-1] > statistics.fmean(closes[-20:])
        if len(closes) >= 60:
            counted60 += 1
            above60 += closes[-1] > statistics.fmean(closes[-60:])
        if window and len(closes) >= window:
            recent = closes[-window:]
            if closes[-1] >= max(recent) and closes[-1] > min(recent):
                highs += 1
                if row['turnover_twd'] >= LIST_MIN_TURNOVER:
                    new_highs.append(dict(symbol=symbol, name=row['name'], close=row['close'],
                                          change_pct=None if row.get('change_pct') is None else round(row['change_pct'], 2),
                                          turnover_100m_twd=round(row['turnover_twd'] / 1e8, 2)))
            elif closes[-1] <= min(recent) and closes[-1] < max(recent):
                lows += 1
    if counted:
        out['breadth'] = dict(counted=counted, above_ma20_pct=round(100 * above20 / counted, 1),
                              above_ma60_pct=round(100 * above60 / counted60, 1) if counted60 else None,
                              window=window, new_highs=highs if window else None, new_lows=lows if window else None)
        new_highs.sort(key=lambda x: (-x['turnover_100m_twd'], x['symbol']))
        out['new_highs'] = new_highs[:top]
    else:
        out['note'] = f'歷史僅 {available} 個交易日，需至少 20 日才計算均線與新高新低'
    rows = []
    for symbol in watchlist:
        if symbol not in today['rows'] or today['rows'][symbol].get('close') is None:
            continue
        closes = series(days, symbol)
        k, d_value = kd(days, symbol)
        dif, histogram = macd(closes)
        rows.append(dict(symbol=symbol, name=today['rows'][symbol]['name'], close=closes[-1],
                         bias5_pct=bias(closes, 5), bias20_pct=bias(closes, 20), bias60_pct=bias(closes, 60),
                         rsi14=rsi(closes), k9=k, d9=d_value, macd_dif_pct=dif, macd_hist_pct=histogram,
                         range60_pct=position(closes, 60)))
    out['watchlist'] = rows
    return out
