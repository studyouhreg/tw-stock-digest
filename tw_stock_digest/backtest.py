"""Event-driven backtest for any ``Strategy``.

Timeline for a rebalance decided on day t (using t's close):
  - orders execute at the OPEN of day t+1;
  - a stock that does not trade, or opens at the limit in the direction of the
    order, cannot be traded that morning (buys are dropped, sells retried);
  - the portfolio is reset to equal weights over the new picks; only the
    weight changes pay costs (fee both sides, transaction tax on sells).
Between rebalances positions drift with their own returns.
"""
from dataclasses import dataclass, field
import math
import statistics

from .strategies.base import LIMIT, MarketView

FEE = 0.001425
TAX = 0.003
DAYS_PER_YEAR = 250


@dataclass
class Costs:
    fee: float = FEE
    tax: float = TAX

    def buy(self, amount):
        return amount * self.fee

    def sell(self, amount):
        return amount * (self.fee + self.tax)


@dataclass
class Result:
    strategy: str
    dates: list
    equity: list            # portfolio value at each close, starting at 1.0
    turnover: float = 0.0   # sum of traded value / equity
    costs_paid: float = 0.0
    holdings: list = field(default_factory=list)   # number of positions per day
    rebalances: int = 0
    skipped_buys: int = 0
    picks: dict = field(default_factory=dict)       # rebalance date -> symbols


def period_key(date, rebalance):
    if rebalance == 'daily':
        return date
    if rebalance == 'weekly':
        return date.isocalendar()[:2]
    if rebalance == 'monthly':
        return date.year, date.month
    raise ValueError(f'unknown rebalance frequency {rebalance!r}')


def rebalance_days(dates, rebalance):
    """Indices of the last trading day of each period (decisions use that close)."""
    keys = [period_key(d, rebalance) for d in dates]
    return {i for i in range(len(dates) - 1) if keys[i] != keys[i + 1]}


def open_move(data, s, i):
    o, ref = data['open'][s][i], data['ref'][s][i]
    return o / ref - 1 if o and ref else None


def run(panel, strategy, *, start=0, end=None, costs=None):
    """Simulate ``strategy`` from panel index ``start`` to ``end`` (inclusive)."""
    costs = costs or Costs()
    end = len(panel.dates) - 1 if end is None else end
    data = panel.data
    decide = rebalance_days(panel.dates[:end + 1], strategy.rebalance)
    cash, positions, target, pending = 1.0, {}, None, set()
    result = Result(strategy.name, [panel.dates[start]], [1.0], holdings=[0])
    # Decide on the first day too, so the portfolio is invested from the start
    # like the benchmarks instead of waiting for the first period end.
    if start < end:
        target = [p.symbol for p in strategy.select(MarketView(panel, start))]
        result.picks[panel.dates[start].isoformat()] = target
    for i in range(start + 1, end + 1):
        # 1) Overnight: mark holdings to today's open; untraded stocks carry.
        opened = {}
        for s, v in positions.items():
            move = open_move(data, s, i)
            opened[s] = v * (1 + move) if move is not None else v
        # 2a) Retry exits that could not trade on an earlier morning.
        for s in sorted(pending & set(opened)):
            move = open_move(data, s, i)
            if move is not None and move > -LIMIT and (target is None or s not in target):
                value = opened.pop(s)
                cost = costs.sell(value)
                result.costs_paid += cost
                result.turnover += value / (cash + value + sum(opened.values()))
                cash += value - cost
        pending &= set(opened)
        # 2b) Execute yesterday's decision at the open.
        if target is not None:
            equity = cash + sum(opened.values())
            moves = {s: open_move(data, s, i) for s in set(target) | set(opened)}
            # Exits that cannot trade (no trade, or opening locked limit-down) stay held.
            stuck = {s for s in opened if s not in target and (moves[s] is None or moves[s] <= -LIMIT)}
            frozen = {s for s in target if s in opened and moves[s] is None}
            buyable = [s for s in target if s in opened or (moves[s] is not None and moves[s] < LIMIT)]
            result.skipped_buys += len(target) - len(buyable)
            new = {s: opened[s] for s in stuck | frozen}
            rest = [s for s in buyable if s not in frozen]
            budget = equity - sum(new.values())
            weight = budget / len(rest) if rest else 0.0
            paid = 0.0
            for s in rest:
                delta = weight - opened.get(s, 0.0)
                paid += costs.buy(delta) if delta > 0 else costs.sell(-delta)
                result.turnover += abs(delta) / equity
            for s in set(opened) - set(new) - set(rest):
                paid += costs.sell(opened[s])
                result.turnover += opened[s] / equity
            # Costs come out of the new positions, so cash stays at zero when fully invested.
            scale = (budget - paid) / budget if budget > 0 and rest else 1.0
            for s in rest:
                new[s] = weight * scale
            result.costs_paid += paid
            cash = equity - paid - sum(new.values())
            opened, target = new, None
            pending |= stuck
            result.rebalances += 1
        # 3) Intraday: open -> close.
        positions = {}
        for s, v in opened.items():
            o, c = data['open'][s][i], data['close'][s][i]
            positions[s] = v * (c / o) if o and c else v
        result.dates.append(panel.dates[i])
        result.equity.append(cash + sum(positions.values()))
        result.holdings.append(len(positions))
        # 4) Decide after the close; trade tomorrow.
        if i in decide and i < end:
            target = [p.symbol for p in strategy.select(MarketView(panel, i))]
            result.picks[panel.dates[i].isoformat()] = target
    return result


def benchmark_equal_weight(panel, start, end, *, min_turnover=20_000_000):
    """Daily-rebalanced equal weight of the tradable universe, before costs."""
    equity = [1.0]
    for i in range(start + 1, end + 1):
        universe = MarketView(panel, i - 1).universe(min_turnover=min_turnover)
        rets = [panel.data['ret'][s][i] for s in universe if panel.data['ret'][s][i] is not None]
        equity.append(equity[-1] * (1 + (sum(rets) / len(rets) if rets else 0.0)))
    return equity


def benchmark_symbol(panel, symbol, start, end):
    equity = [1.0]
    for i in range(start + 1, end + 1):
        r = panel.data['ret'].get(symbol, [None] * len(panel.dates))[i]
        equity.append(equity[-1] * (1 + (r or 0.0)))
    return equity


def metrics(equity, benchmark=None):
    rets = [b / a - 1 for a, b in zip(equity, equity[1:])]
    years = len(rets) / DAYS_PER_YEAR
    total = equity[-1] / equity[0] - 1
    vol = statistics.pstdev(rets) * math.sqrt(DAYS_PER_YEAR) if len(rets) > 1 else 0.0
    mean = statistics.fmean(rets) * DAYS_PER_YEAR if rets else 0.0
    peak, drawdown = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        drawdown = min(drawdown, v / peak - 1)
    out = dict(total_return=total, cagr=(equity[-1] / equity[0]) ** (1 / years) - 1 if years else 0.0,
               volatility=vol, sharpe=mean / vol if vol else 0.0, max_drawdown=drawdown)
    if benchmark is not None:
        excess = [r - (b / a - 1) for r, a, b in zip(rets, benchmark, benchmark[1:])]
        sd = statistics.pstdev(excess) if len(excess) > 1 else 0.0
        out['excess_annual'] = statistics.fmean(excess) * DAYS_PER_YEAR if excess else 0.0
        out['tracking_error'] = sd * math.sqrt(DAYS_PER_YEAR)
        out['information_ratio'] = out['excess_annual'] / out['tracking_error'] if sd else 0.0
        # t-statistic of the mean daily excess return (no autocorrelation adjustment).
        out['excess_t'] = statistics.fmean(excess) / (sd / math.sqrt(len(excess))) if sd else 0.0
    return out
