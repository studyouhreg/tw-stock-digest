import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from test_market import twse, tpex
from tw_stock_digest import backtest, strategies
from tw_stock_digest.market import normalize
from tw_stock_digest.panel import FIELDS, Panel, build, from_quotes, screen_snapshot
from tw_stock_digest.strategies import MarketView, Strategy


def trading_days(n, start=dt.date(2026, 1, 5)):
    days, d = [], start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    return days


def make_panel(bars, n, flows=None):
    """bars: {symbol: [dict(open=, close=, ...) or None per day]}"""
    dates = trading_days(n)
    data = {f: {} for f in FIELDS}
    for s, rows in bars.items():
        for f in FIELDS:
            data[f][s] = [None] * n
        prev = None
        for i, row in enumerate(rows):
            if row is None:
                continue
            row = dict(row)
            row.setdefault('ref', prev)
            row.setdefault('high', max(row['open'], row['close']))
            row.setdefault('low', min(row['open'], row['close']))
            row.setdefault('volume', 1_000_000)
            row.setdefault('turnover', 100_000_000)
            for f, v in row.items():
                data[f][s][i] = v
            if row['ref']:
                data['ret'][s][i] = row['close'] / row['ref'] - 1
            prev = row['close']
    return Panel(dates, data, {s: s for s in bars}, {s: 'TWSE' for s in bars},
                 flows if flows is not None else [False] * n)


def steady(n, start=100.0, step=0.01, gap=0.0):
    rows, close = [], start
    for _ in range(n):
        open_ = close * (1 + gap)
        close = close * (1 + step)
        rows.append(dict(open=open_, close=close))
    rows[0]['ref'] = start
    return rows


class Always(Strategy):
    name = 'always_test'
    rebalance = 'monthly'

    def __init__(self, symbols, **kw):
        super().__init__(**kw)
        self.symbols = symbols

    def select(self, view):
        return [strategies.Pick(s, 1.0) for s in self.symbols]

    def score(self, view, symbol):
        return 1.0


class RegistryTests(unittest.TestCase):
    def test_every_strategy_is_documented_and_constructible(self):
        self.assertGreaterEqual(len(strategies.REGISTRY), 13)
        for name, cls in strategies.REGISTRY.items():
            s = strategies.get(name)
            self.assertEqual(s.name, name)
            self.assertTrue(s.label and s.description, name)
            self.assertIn(s.rebalance, ('daily', 'weekly', 'monthly'))
        with self.assertRaisesRegex(KeyError, 'unknown strategy'):
            strategies.get('nope')

    def test_duplicate_registration_is_rejected(self):
        with self.assertRaises(ValueError):
            strategies.register(type('Dup', (Always,), {'name': 'high_52w'}))


class ViewTests(unittest.TestCase):
    def test_view_cannot_see_the_future(self):
        panel = make_panel({'2330': steady(10)}, 10)
        view = MarketView(panel, 4)
        self.assertEqual(len(view.history('close', '2330', 100)), 5)
        self.assertEqual(view.history('close', '2330', 1)[0], panel.data['close']['2330'][4])
        with self.assertRaisesRegex(ValueError, 'look-ahead'):
            view.value('close', '2330', lag=-1)

    def test_strategy_decisions_ignore_future_changes(self):
        base = make_panel({'1101': steady(300, step=0.002), '2330': steady(300, step=0.001)}, 300)
        changed = make_panel({'1101': steady(300, step=0.002), '2330': steady(300, step=0.001)}, 300)
        for i in range(261, 300):  # rewrite the future only
            changed.data['close']['1101'][i] *= 0.5
        for name in ('momentum_12_1', 'high_52w', 'low_volatility', 'intraday_momentum'):
            a = strategies.get(name).select(MarketView(base, 260))
            b = strategies.get(name).select(MarketView(changed, 260))
            self.assertEqual(a, b, name)

    def test_universe_requires_liquidity_and_ordinary_shares(self):
        bars = {'2330': steady(5), '0050': steady(5), '9999': steady(5)}
        for row in bars['9999']:
            row['turnover'] = 1_000
        view = MarketView(make_panel(bars, 5), 4)
        self.assertEqual(view.universe(), ['2330'])


class StrategyRuleTests(unittest.TestCase):
    def test_momentum_picks_the_stronger_stock(self):
        panel = make_panel({'1101': steady(260, step=0.002), '2330': steady(260, step=0.001)}, 260)
        picks = strategies.get('momentum_12_1', top_n=1).select(MarketView(panel, 259))
        self.assertEqual([p.symbol for p in picks], ['1101'])

    def test_intraday_and_overnight_split(self):
        # 1101 gains overnight then fades intraday; 2330 opens flat and rises intraday.
        n = 260
        a, b = [], []
        ca = cb = 100.0
        for _ in range(n):
            a.append(dict(open=ca * 1.01, close=ca * 1.01 * 0.995)); ca = a[-1]['close']
            b.append(dict(open=cb, close=cb * 1.004)); cb = b[-1]['close']
        a[0]['ref'] = b[0]['ref'] = 100.0
        view = MarketView(make_panel({'1101': a, '2330': b}, n), n - 1)
        self.assertEqual(strategies.get('intraday_momentum', top_n=1).select(view)[0].symbol, '2330')
        self.assertEqual(strategies.get('overnight_reversal', top_n=1).select(view)[0].symbol, '2330')

    def test_trust_flow_needs_four_buying_days_and_flow_data(self):
        n = 6
        panel = make_panel({'2330': steady(n), '2317': steady(n)}, n, flows=[True] * n)
        panel.data['trust']['2330'] = [0, 10, 10, 10, 10, -5]
        panel.data['trust']['2317'] = [0, 10, -1, 10, -1, 10]
        view = MarketView(panel, n - 1)
        self.assertEqual([p.symbol for p in strategies.get('trust_flow').select(view)], ['2330'])
        panel.flows_available[3] = False
        self.assertEqual(strategies.get('trust_flow').select(view), [])

    def test_screens_on_pipeline_snapshot(self):
        quotes = normalize('TWSE', [
            twse('2330', ClosingPrice='110', HighestPrice='110', Change='10', OpeningPrice='103'),
            twse('2317', ClosingPrice='104', HighestPrice='104', Change='4', OpeningPrice='101'),
            twse('2454', ClosingPrice='101', Change='1', OpeningPrice='99', HighestPrice='105'),
        ]) + normalize('TPEX', [tpex()])
        result = screen_snapshot(quotes, ['limit_up', 'close_at_high', 'gap_up', 'turnover_leaders'],
                                 min_turnover=0)
        self.assertEqual([p.symbol for p in result['limit_up']], ['2330'])
        self.assertEqual([p.symbol for p in result['close_at_high']], ['2317'])
        self.assertEqual([p.symbol for p in result['gap_up']], ['2330'])
        with self.assertRaisesRegex(ValueError, 'needs history'):
            screen_snapshot(quotes, ['momentum_12_1'])

    def test_snapshot_panel_matches_reference_price_definition(self):
        panel = from_quotes(normalize('TWSE', [twse('2330', ClosingPrice='110', HighestPrice='110', Change='10')]))
        self.assertAlmostEqual(panel.data['ret']['2330'][0], 0.1)


class BacktestTests(unittest.TestCase):
    def test_buy_and_hold_arithmetic_with_costs(self):
        n = 30
        panel = make_panel({'2330': steady(n, step=0.01)}, n)
        costs = backtest.Costs(fee=0.001, tax=0.0)
        result = backtest.run(panel, Always(['2330']), costs=costs)
        # Decided on the first close, bought at the next open with no gap:
        # (1 - fee), then close/open compounding every day after.
        self.assertAlmostEqual(result.equity[0], 1.0)
        self.assertAlmostEqual(result.equity[-1], (1 - 0.001) * 1.01 ** (n - 1), places=9)
        # The month-end rebalance to the same single stock trades nothing.
        self.assertEqual(result.rebalances, 2)
        self.assertAlmostEqual(result.costs_paid, 0.001)

    def test_round_trip_costs_fee_and_tax(self):
        n = 70
        rows = [dict(open=100.0, close=100.0, ref=100.0) for _ in range(n)]
        panel = make_panel({'2330': rows, '2317': [dict(r) for r in rows]}, n)

        class Switch(Always):
            def select(self, view):
                return [strategies.Pick('2330' if view.date.month == 1 else '2317', 1.0)]

        result = backtest.run(panel, Switch([]), costs=backtest.Costs(fee=0.001425, tax=0.003))
        buy = 1 - 0.001425
        sell_and_buy = buy - buy * (0.001425 + 0.003) - buy * 0.001425
        self.assertAlmostEqual(result.equity[-1], sell_and_buy, places=6)

    def test_limit_up_open_cannot_be_bought_and_untraded_stock_cannot_be_sold(self):
        n = 30
        rows = steady(n, step=0.0)
        decide = max(i for i, d in enumerate(trading_days(n)) if d.month == 1)
        rows[decide + 1] = dict(open=110.0, close=110.0, ref=100.0)  # opens at limit up
        panel = make_panel({'2330': rows}, n)
        result = backtest.run(panel, Always(['2330']), start=decide)
        self.assertEqual(result.skipped_buys, 1)
        self.assertEqual(result.holdings[-1], 0)

        held = steady(70, step=0.0)
        days = trading_days(70)
        feb_end = max(i for i, d in enumerate(days) if d.month == 2)
        held[feb_end + 1] = None  # suspended the morning we try to sell
        panel = make_panel({'2330': held, '2317': steady(70, step=0.0)}, 70)

        class Leave(Always):
            def select(self, view):
                return [strategies.Pick('2330' if view.date.month == 1 else '2317', 1.0)]

        result = backtest.run(panel, Leave([]))
        self.assertEqual(result.holdings[feb_end + 1], 2)  # still holding the suspended stock
        self.assertEqual(result.holdings[feb_end + 2], 1)  # sold once it traded again

    def test_metrics(self):
        m = backtest.metrics([1.0, 1.1, 0.99, 1.2], benchmark=[1.0, 1.0, 1.0, 1.0])
        self.assertAlmostEqual(m['total_return'], 0.2)
        self.assertAlmostEqual(m['max_drawdown'], 0.99 / 1.1 - 1)
        self.assertGreater(m['excess_annual'], 0)


class PanelBuildTests(unittest.TestCase):
    def test_ex_dividend_day_uses_official_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            fields = ['證券代號', '證券名稱', '成交股數', '成交筆數', '成交金額', '開盤價', '最高價', '最低價',
                      '收盤價', '漲跌(+/-)', '漲跌價差', '最後揭示買價', '最後揭示買量', '最後揭示賣價',
                      '最後揭示賣量', '本益比']
            def twse_day(day, close, sign, change):
                row = ['2330', '台積電', '1,000', '1', '50,000,000', str(close), str(close), str(close),
                       str(close), sign, change, '', '', '', '', '']
                return {'stat': 'OK', 'tables': [{'fields': fields, 'data': [row]}]}
            tpex_fields = ['代號', '名稱', '收盤 ', '漲跌', '開盤 ', '最高 ', '最低', '成交股數  ',
                           ' 成交金額(元)', ' 成交筆數 ']
            def tpex_day(day, close, change):
                row = ['6488', '環球晶', str(close), change, str(close), str(close), str(close),
                       '1,000', '30,000,000', '1']
                return {'stat': 'ok', 'tables': [{'fields': tpex_fields, 'data': [row]}]}
            days = [dt.date(2026, 7, 1), dt.date(2026, 7, 2)]
            files = {
                'twse_price/2026/20260701.json': twse_day(days[0], 100, '<p> </p>', '0.00'),
                'twse_price/2026/20260702.json': twse_day(days[1], 96, '<p>X</p>', '0.00'),
                'tpex_price/2026/20260701.json': tpex_day(days[0], 50, '0.00'),
                'tpex_price/2026/20260702.json': tpex_day(days[1], 49, '除息'),
                'twse_exright/202607.json': {'stat': 'OK', 'data': [
                    ['115年07月02日', '2330', '台積電', '100.00', '95.00']]},
                'tpex_exright/202607.json': {'stat': 'ok', 'tables': [{'data': [
                    ['115/07/02', '6488', '環球晶', '50.00', '48.00']]}]},
            }
            for name, value in files.items():
                path = cache / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value, ensure_ascii=False))
            panel = build(cache)
            self.assertEqual(panel.dates, days)
            self.assertAlmostEqual(panel.data['ret']['2330'][1], 96 / 95 - 1)
            self.assertAlmostEqual(panel.data['ret']['6488'][1], 49 / 48 - 1)
            self.assertEqual(panel.data['ret']['2330'][0], 0.0)  # official change 0 vs previous close
            self.assertEqual(panel.data['ref']['2330'][0], 100.0)
