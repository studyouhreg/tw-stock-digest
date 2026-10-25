"""Strategies on point-in-time fundamentals. See docs/strategies/research.md.

Every record comes from ``view.facts``, which only returns what was public on
the view's day (see ``tw_stock_digest.fundamentals`` for the release rules).
"""
import statistics

from .base import Strategy, register


def month_number(period):
    year, month = period
    return year * 12 + month


def quarter_number(period):
    year, quarter = period
    return year * 4 + quarter


def consecutive(records, key):
    numbers = [key(r['period']) for r in records]
    return all(b - a == 1 for a, b in zip(numbers, numbers[1:]))


def fresh_month(view, record, max_lag=2):
    """The latest revenue must be for one of the last ``max_lag`` calendar months."""
    today = view.date.year * 12 + view.date.month
    return today - month_number(record['period']) <= max_lag


# --- monthly revenue -----------------------------------------------------------

@register
class RevenueGrowth(Strategy):
    name = 'revenue_yoy'
    label = '月營收年增率'
    description = '最新公布月營收的年增率最高者（去年同月營收須為正）'
    needs_facts = ('revenue',)

    def score(self, view, symbol):
        rec = view.facts('revenue', symbol)
        if not rec or not fresh_month(view, rec[-1]):
            return None
        rev, last = rec[-1]['revenue'], rec[-1]['last_year']
        return rev / last - 1 if rev and last and last > 0 else None


@register
class Revenue3MonthGrowth(Strategy):
    name = 'revenue_3m_yoy'
    label = '近 3 月營收年增率'
    description = '最近 3 個月營收合計 ÷ 去年同期合計 − 1 最高者，比單月更穩定'
    needs_facts = ('revenue',)

    def score(self, view, symbol):
        recs = view.facts('revenue', symbol, 3)
        if len(recs) < 3 or not consecutive(recs, month_number) or not fresh_month(view, recs[-1]):
            return None
        last = [r['last_year'] for r in recs]
        if any(x is None or x <= 0 for x in last):
            return None
        return sum(r['revenue'] for r in recs) / sum(last) - 1


@register
class RevenueSurprise(Strategy):
    name = 'revenue_surprise'
    label = '未預期月營收 SURGE'
    description = '（本月年增金額 − 過去 12 個月年增金額平均）÷ 其標準差，最高者'
    needs_facts = ('revenue',)

    def score(self, view, symbol):
        recs = view.facts('revenue', symbol, 13)
        if len(recs) < 13 or not consecutive(recs, month_number) or not fresh_month(view, recs[-1]):
            return None
        diffs = [r['revenue'] - r['last_year'] for r in recs if r['last_year'] is not None]
        if len(diffs) < 13:
            return None
        history, now = diffs[:-1], diffs[-1]
        sd = statistics.pstdev(history)
        return (now - statistics.fmean(history)) / sd if sd > 0 else None


# --- valuation -----------------------------------------------------------------

class Valuation(Strategy):
    needs_facts = ('valuation',)

    def latest(self, view, symbol):
        rec = view.facts('valuation', symbol)
        # Month-end samples: accept a record up to ~6 weeks old.
        if not rec or (view.date - rec[-1]['date']).days > 45:
            return None
        return rec[-1]


@register
class EarningsYield(Valuation):
    name = 'low_pe'
    label = '低本益比（高 E/P）'
    description = '盈餘殖利率 1 ÷ 本益比最高者；虧損公司（無本益比）排除'

    def score(self, view, symbol):
        rec = self.latest(view, symbol)
        return 1 / rec['pe'] if rec and rec['pe'] and rec['pe'] > 0 else None


@register
class BookToMarket(Valuation):
    name = 'low_pb'
    label = '低股價淨值比'
    description = '股價淨值比最低者（淨值為正）'

    def score(self, view, symbol):
        rec = self.latest(view, symbol)
        return -rec['pb'] if rec and rec['pb'] and rec['pb'] > 0 else None


@register
class DividendYield(Valuation):
    name = 'high_dividend_yield'
    label = '高殖利率'
    description = '官方公布的殖利率最高者（以最近一年股利計算）'

    def score(self, view, symbol):
        rec = self.latest(view, symbol)
        return rec['dividend_yield'] if rec and rec['dividend_yield'] and rec['dividend_yield'] > 0 else None


# --- margin trading ------------------------------------------------------------

class Margin(Strategy):
    rebalance = 'weekly'
    lookback = 20
    needs_facts = ('margin',)

    def change(self, view, symbol):
        """4-week change in margin balance, in days of average volume."""
        recs = view.facts('margin', symbol, 5)
        if len(recs) < 5 or (view.date - recs[-1]['date']).days > 10 or (recs[-1]['date'] - recs[0]['date']).days > 35:
            return None
        if recs[0]['margin'] is None or recs[-1]['margin'] is None:
            return None
        volume = [v for v in view.history('volume', symbol, 20) if v]
        if len(volume) < 15:
            return None
        lots_per_day = sum(volume) / len(volume) / 1000
        return (recs[-1]['margin'] - recs[0]['margin']) / lots_per_day if lots_per_day > 0 else None


@register
class MarginSurge(Margin):
    name = 'margin_surge'
    label = '融資暴增'
    description = '近 4 週融資餘額增加最多（以日均成交量為單位）者；文獻預期之後報酬較低'

    def score(self, view, symbol):
        c = self.change(view, symbol)
        return c if c is not None and c > 0 else None


@register
class MarginRetreat(Margin):
    name = 'margin_retreat'
    label = '融資退場'
    description = '近 4 週融資餘額減少最多（以日均成交量為單位）者'

    def score(self, view, symbol):
        c = self.change(view, symbol)
        return -c if c is not None and c < 0 else None


@register
class ShortInterest(Margin):
    name = 'short_interest'
    label = '高券資比'
    description = '融券餘額 ÷ 融資餘額最高者（融資至少 500 張）'

    def score(self, view, symbol):
        recs = view.facts('margin', symbol)
        if not recs or (view.date - recs[-1]['date']).days > 10:
            return None
        margin, short = recs[-1]['margin'], recs[-1]['short']
        return short / margin if margin and margin >= 500 and short is not None else None


# --- financial statements ------------------------------------------------------

class Statement(Strategy):
    needs_facts = ('statement',)

    def quarters(self, view, symbol, n):
        recs = view.facts('statement', symbol, n)
        if len(recs) < n or not consecutive(recs, quarter_number):
            return None
        # The newest statement must be at most ~2 quarters old.
        if quarter_number((view.date.year, (view.date.month - 1) // 3 + 1)) - quarter_number(recs[-1]['period']) > 3:
            return None
        return recs

    @staticmethod
    def trailing(recs, field):
        values = [r[field] for r in recs[-4:]]
        return None if any(v is None for v in values) else sum(values)


@register
class ReturnOnEquity(Statement):
    name = 'roe'
    label = '高 ROE'
    description = '近 4 季歸屬母公司淨利 ÷ 最新歸屬母公司權益最高者'

    def score(self, view, symbol):
        recs = self.quarters(view, symbol, 4)
        if not recs:
            return None
        income, equity = self.trailing(recs, 'q_net_income_parent'), recs[-1]['equity']
        return income / equity if income is not None and equity and equity > 0 else None


@register
class GrossProfitability(Statement):
    name = 'gross_profitability'
    label = '毛利資產比'
    description = '近 4 季營業毛利 ÷ 最新總資產最高者（Novy-Marx；金融業無毛利，排除）'

    def score(self, view, symbol):
        recs = self.quarters(view, symbol, 4)
        if not recs:
            return None
        gross, assets = self.trailing(recs, 'q_gross_profit'), recs[-1]['assets']
        return gross / assets if gross is not None and assets and assets > 0 else None


@register
class EarningsGrowth(Statement):
    name = 'eps_growth'
    label = 'EPS 年增'
    description = '最新單季 EPS 減去年同季 EPS，再除以股價（價格標準化的盈餘驚喜），最高者'

    def score(self, view, symbol):
        recs = self.quarters(view, symbol, 5)
        close = view.value('close', symbol)
        if not recs or not close:
            return None
        now, before = recs[-1]['q_eps'], recs[0]['q_eps']
        return (now - before) / close if now is not None and before is not None else None
