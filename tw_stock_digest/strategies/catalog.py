"""Concrete strategies. See docs/strategies/research.md for the evidence behind each.

Parameters follow the literature's usual choices and are deliberately not tuned
on the backtest period.
"""
import math
import statistics

from .base import LIMIT, Strategy, register

YEAR, MONTH, WEEK = 250, 21, 5


def log_sum(pairs, min_count):
    total, count = 0.0, 0
    for a, b in pairs:
        if a and b and a > 0 and b > 0:
            total += math.log(a / b)
            count += 1
    return total if count >= min_count else None


# --- history-based strategies ------------------------------------------------

@register
class Momentum12_1(Strategy):
    name = 'momentum_12_1'
    label = '傳統動能 12-1'
    description = '過去 12 個月（跳過最近 1 個月）報酬最高者'
    lookback = YEAR

    def score(self, view, symbol):
        return view.compound(symbol, YEAR - MONTH, skip=MONTH)


@register
class IntradayMomentum(Strategy):
    name = 'intraday_momentum'
    label = '盤中動能 IMOM'
    description = '過去 12 個月盤中報酬（收盤／開盤）累積最高者'
    lookback = YEAR

    def score(self, view, symbol):
        pairs = zip(view.history('close', symbol, YEAR), view.history('open', symbol, YEAR))
        return log_sum(pairs, int(0.8 * YEAR))


@register
class OvernightReversal(Strategy):
    name = 'overnight_reversal'
    label = '隔夜反轉（反向 OMOM）'
    description = '過去 12 個月隔夜報酬（開盤／參考價）累積最低者'
    lookback = YEAR

    def score(self, view, symbol):
        pairs = zip(view.history('open', symbol, YEAR), view.history('ref', symbol, YEAR))
        total = log_sum(pairs, int(0.8 * YEAR))
        return None if total is None else -total


@register
class High52Week(Strategy):
    name = 'high_52w'
    label = '52 週高點'
    description = '還原收盤價最接近過去 250 日還原最高價者'
    lookback = YEAR

    def score(self, view, symbol):
        index = view.adjusted(symbol, YEAR)
        highs, closes = view.history('high', symbol, YEAR), view.history('close', symbol, YEAR)
        if index is None:
            return None
        peaks = [h * x / c for h, c, x in zip(highs, closes, index) if h and c]
        if len(peaks) < 0.8 * YEAR:
            return None
        return index[-1] / max(peaks)


@register
class LowVolatility(Strategy):
    name = 'low_volatility'
    label = '低波動'
    description = '過去 60 日日報酬標準差最低者'
    lookback = 60

    def score(self, view, symbol):
        rets = [r for r in view.history('ret', symbol, 60) if r is not None]
        return -statistics.pstdev(rets) if len(rets) >= 50 else None


@register
class ShortTermReversal(Strategy):
    name = 'short_term_reversal'
    label = '短期反轉'
    description = '過去 5 日報酬最低者，每週換股'
    rebalance = 'weekly'
    lookback = WEEK

    def score(self, view, symbol):
        r = view.compound(symbol, WEEK, min_coverage=1.0)
        return None if r is None else -r


@register
class MovingAverageTrend(Strategy):
    name = 'ma_trend'
    label = '均線趨勢 20/60'
    description = '還原價在 60 日均線之上且 20 日均線 > 60 日均線，依乖離排序，每週換股'
    rebalance = 'weekly'
    lookback = 60

    def score(self, view, symbol):
        index = view.adjusted(symbol, 60)
        if index is None:
            return None
        ma20, ma60 = sum(index[-20:]) / 20, sum(index) / 60
        if index[-1] > ma60 and ma20 > ma60:
            return ma20 / ma60 - 1
        return None


@register
class ForeignFlow(Strategy):
    name = 'foreign_flow'
    label = '外資買超'
    description = '過去 20 日外資買超股數合計 ÷ 成交股數合計最高者，每週換股'
    rebalance = 'weekly'
    lookback = 20
    needs_flows = True

    def score(self, view, symbol):
        flows = view.history('foreign', symbol, 20)
        volume = [v for v in view.history('volume', symbol, 20) if v]
        if len(volume) < 15 or any(f is None for f in flows):
            return None
        net = sum(flows)
        return net / sum(volume) if net > 0 else None


@register
class TrustFlow(Strategy):
    name = 'trust_flow'
    label = '投信連續買超'
    description = '過去 5 日至少 4 日投信買超，依買超占成交量比排序，每週換股'
    rebalance = 'weekly'
    lookback = WEEK
    needs_flows = True

    def score(self, view, symbol):
        flows = view.history('trust', symbol, WEEK)
        volume = [v for v in view.history('volume', symbol, WEEK) if v]
        if len(flows) < WEEK or any(f is None for f in flows) or not volume:
            return None
        if sum(f > 0 for f in flows) < 4:
            return None
        return sum(flows) / sum(volume)


# --- snapshot screens: need only today's quote ------------------------------

class Screen(Strategy):
    """A one-day filter. Held for one day when backtested."""
    rebalance = 'daily'
    lookback = 1

    def gap(self, view, symbol):
        o, ref = view.value('open', symbol), view.value('ref', symbol)
        return o / ref - 1 if o and ref else None


@register
class LimitUp(Screen):
    name = 'limit_up'
    label = '收盤漲停'
    description = '當日漲幅 ≥ 9.5%'

    def score(self, view, symbol):
        r = view.value('ret', symbol)
        return r if r is not None and r >= LIMIT else None


@register
class CloseAtHigh(Screen):
    name = 'close_at_high'
    label = '強勢收高'
    description = '收盤 = 最高價、收紅且未漲停，依漲幅排序'

    def score(self, view, symbol):
        r, c, h, o = (view.value(f, symbol) for f in ('ret', 'close', 'high', 'open'))
        if None in (r, c, h, o) or not (c == h and c > o and 0 < r < LIMIT):
            return None
        return r


@register
class GapUp(Screen):
    name = 'gap_up'
    label = '開高跳空'
    description = '開盤較參考價上漲 ≥ 3%，依跳空幅度排序'

    def score(self, view, symbol):
        g = self.gap(view, symbol)
        return g if g is not None and g >= 0.03 else None


@register
class TurnoverLeaders(Screen):
    name = 'turnover_leaders'
    label = '成交值集中'
    description = '當日成交金額最大者'

    def score(self, view, symbol):
        return view.value('turnover', symbol)
