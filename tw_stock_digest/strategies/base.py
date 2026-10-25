"""Strategy pattern: every strategy answers one question through one interface.

    "Given everything known at today's close, which stocks would you hold?"

The backtester, the daily snapshot screener and the tests depend only on
``Strategy.select(view)``. A ``MarketView`` exposes data up to and including
its own day and nothing later, so look-ahead is impossible by construction.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
import math

LIMIT = 0.095  # Taiwan's daily price limit is 10%; allow for tick rounding.


@dataclass(frozen=True)
class Pick:
    symbol: str
    score: float


class MarketView:
    def __init__(self, panel, index):
        if not 0 <= index < len(panel.dates):
            raise IndexError('view outside panel')
        self._panel, self.i = panel, index
        self.date = panel.dates[index]

    # --- raw access, always ending at today -----------------------------
    def value(self, field, symbol, lag=0):
        j = self.i - lag
        if lag < 0:
            raise ValueError('look-ahead: a view cannot read future days')
        series = self._panel.data[field].get(symbol)
        return series[j] if series is not None and j >= 0 else None

    def history(self, field, symbol, n):
        """The last ``n`` values ending today (fewer near the panel start)."""
        series = self._panel.data[field].get(symbol)
        if series is None:
            return []
        return series[max(0, self.i - n + 1):self.i + 1]

    def available_days(self):
        return self.i + 1

    def flows_complete(self, n):
        flags = self._panel.flows_available[max(0, self.i - n + 1):self.i + 1]
        return len(flags) == n and all(flags)

    def name(self, symbol):
        return self._panel.names.get(symbol, '')

    def facts(self, dataset, symbol, n=1):
        """Up to n most recent fundamentals records already public today, oldest first."""
        store = getattr(self._panel, 'facts', None)
        return store.latest(dataset, symbol, self.i, n) if store is not None else []

    def market(self, symbol):
        return self._panel.markets.get(symbol, '')

    # --- derived helpers shared by strategies ---------------------------
    def returns(self, symbol, n, *, skip=0):
        """Daily returns for the n days ending ``skip`` days ago; None -> 0."""
        values = self.history('ret', symbol, n + skip)
        values = values[:len(values) - skip] if skip else values
        return [r or 0.0 for r in values], sum(r is not None for r in values)

    def compound(self, symbol, n, *, skip=0, min_coverage=0.8):
        values, observed = self.returns(symbol, n, skip=skip)
        if len(values) < n or observed < min_coverage * n:
            return None
        total = 1.0
        for r in values:
            total *= 1 + r
        return total - 1

    def adjusted(self, symbol, n):
        """A total-return index for the last n days, scaled so today = today's close."""
        rets = self.history('ret', symbol, n)
        close = self.value('close', symbol)
        if close is None or len(rets) < n:
            return None
        index = [1.0]
        for r in rets[1:]:
            index.append(index[-1] * (1 + (r or 0.0)))
        scale = close / index[-1]
        return [x * scale for x in index]

    def universe(self, *, min_turnover=20_000_000, window=20):
        """Ordinary shares that traded today and are liquid enough to trade."""
        chosen = []
        panel = self._panel
        for s in panel.symbols:
            if panel.kind(s) != 'equity' or self.value('close', s) is None:
                continue
            turnover = [t for t in self.history('turnover', s, window) if t is not None]
            if turnover and sum(turnover) / min(window, self.available_days()) >= min_turnover:
                chosen.append(s)
        return chosen


class Strategy(ABC):
    """Rank-and-pick strategy. Subclasses implement ``score`` (or ``select``)."""
    name = ''
    label = ''
    description = ''
    rebalance = 'monthly'   # 'daily', 'weekly' or 'monthly'
    lookback = 1            # trading days of history required
    needs_flows = False
    needs_facts = ()        # fundamentals datasets the strategy reads

    def __init__(self, top_n=20, min_turnover=20_000_000):
        self.top_n, self.min_turnover = top_n, min_turnover

    def ready(self, view):
        return view.available_days() >= self.lookback and (
            not self.needs_flows or view.flows_complete(self.lookback))

    def select(self, view):
        if not self.ready(view):
            return []
        scored = []
        for symbol in view.universe(min_turnover=self.min_turnover):
            score = self.score(view, symbol)
            if score is not None and math.isfinite(score):
                scored.append(Pick(symbol, score))
        scored.sort(key=lambda p: (-p.score, p.symbol))
        return scored[:self.top_n]

    @abstractmethod
    def score(self, view, symbol):
        """Higher is better; None excludes the stock today."""


REGISTRY = {}


def register(cls):
    if not cls.name or cls.name in REGISTRY:
        raise ValueError(f'strategy needs a unique name: {cls.name!r}')
    REGISTRY[cls.name] = cls
    return cls


def get(name, **params):
    try:
        return REGISTRY[name](**params)
    except KeyError:
        raise KeyError(f'unknown strategy {name!r}; choose from {sorted(REGISTRY)}') from None
