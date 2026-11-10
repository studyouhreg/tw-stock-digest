import datetime as dt
from pathlib import Path
import tempfile
import unittest

from test_strategies import make_panel, steady, trading_days
from tw_stock_digest import strategies
from tw_stock_digest.fundamentals import (INCOME, Facts, attach, parse_margin, parse_revenue,
                                          parse_statement, parse_valuation, revenue_available,
                                          statement_available)
from tw_stock_digest.strategies import MarketView


def revenue_html(rows):
    body = ''.join(f'<tr align=right><td align=center>{c}</td><td>{n}</td><td>{a:,}</td><td>1</td>'
                   f'<td>{b:,}</td><td>0</td><td>{100 * (a / b - 1):.2f}</td><td>0</td><td>0</td><td>0</td><td>-</td></tr>'
                   for c, n, a, b in rows)
    html = ('<html><head><meta charset=big5></head><body><table><tr><th>產業別：水泥工業</th></tr>'
            '<tr><td><table><tr><th>公司<br>代號</th><th>公司名稱</th><th>當月營收</th></tr>'
            f'{body}</table></td></tr></table></body></html>')
    return html.encode('cp950')


def statement_html(rows):
    head = ''.join(f'<th>{h}</th>' for h in ('公司代號', '公司名稱', '營業收入', '營業毛利（毛損）淨額',
                                               '淨利（淨損）歸屬於母公司業主', '基本每股盈餘（元）'))
    body = ''.join('<tr class="even">' + ''.join(f'<td>{v}</td>' for v in r) + '</tr>' for r in rows)
    return f'<html><body><table class="hasBorder"><tr>{head}</tr>{body}</table></body></html>'.encode('utf-8')


class ParserTests(unittest.TestCase):
    def test_revenue_columns(self):
        out = parse_revenue(revenue_html([('1101', '台泥', 1200, 1000), ('2330', '台積電', 300, 200)]))
        self.assertEqual(out['1101'], (1200, 1000, 20.0))
        self.assertEqual(out['2330'], (300, 200, 50.0))

    def test_statement_columns_by_name_and_industry_tables(self):
        out = parse_statement(statement_html([('2330', '台積電', '1,000', '600', '400', '15.5'),
                                              ('1101', '台泥', '500', '-', '(10)', '-0.1')]), INCOME)
        self.assertEqual(out['2330'], dict(revenue=1000, gross_profit=600, net_income_parent=400, eps=15.5))
        self.assertIsNone(out['1101']['gross_profit'])

    def test_valuation_and_margin_json(self):
        twse = {'stat': 'OK', 'fields': ['證券代號', '證券名稱', '收盤價', '殖利率(%)', '股利年度', '本益比', '股價淨值比', '財報年/季'],
                'data': [['2330', '台積電', '1000', '1.5', 114, '20.00', '5.00', '115/2'],
                         ['1101', '台泥', '30', '3.0', 114, '-', '0.9', '115/2']]}
        out = parse_valuation(twse, 'TWSE')
        self.assertEqual(out['2330'], dict(pe=20.0, pb=5.0, dividend_yield=1.5))
        self.assertIsNone(out['1101']['pe'])
        tpex = {'tables': [{'fields': ['代號', '名稱', '前資餘額(張)', '資買', '資賣', '現償', '資餘額', '資屬證金',
                                       '資使用率(%)', '資限額', '前券餘額(張)', '券賣', '券買', '券償', '券餘額'],
                            'data': [['6488', '環球晶', '1', '0', '0', '0', '1,200', '0', '0', '0', '5', '0', '0', '0', '30']]}]}
        self.assertEqual(parse_margin(tpex, 'TPEX'), {'6488': (1200, 30)})


class PointInTimeTests(unittest.TestCase):
    def test_release_dates(self):
        self.assertEqual(revenue_available(2025, 12), dt.date(2026, 1, 11))
        self.assertEqual(statement_available(2025, 4), dt.date(2026, 4, 1))
        self.assertEqual(statement_available(2026, 2), dt.date(2026, 9, 1))

    def test_facts_are_hidden_until_public(self):
        facts = Facts()
        facts.add('revenue', '2330', 10, {'v': 1})
        facts.add('revenue', '2330', 30, {'v': 2})
        self.assertEqual(facts.latest('revenue', '2330', 9), [])
        self.assertEqual(facts.latest('revenue', '2330', 10), [{'v': 1}])
        self.assertEqual(facts.latest('revenue', '2330', 29, n=5), [{'v': 1}])
        self.assertEqual(facts.latest('revenue', '2330', 30, n=5), [{'v': 1}, {'v': 2}])

    def test_attach_uses_release_rules_and_single_quarters(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            def write(name, raw):
                path = cache / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            write('mops_revenue/sii/202601.html', revenue_html([('2330', '台積電', 120, 100)]))
            write('mops_income/sii/2026Q1.html', statement_html([('2330', '台積電', '100', '50', '30', '1.0')]))
            write('mops_income/sii/2026Q2.html', statement_html([('2330', '台積電', '250', '120', '80', '2.5')]))
            days = trading_days(200, start=dt.date(2026, 1, 2))
            panel = make_panel({'2330': steady(200)}, 200)
            panel.dates = days
            panel.index = {d: i for i, d in enumerate(days)}
            attach(panel, cache)
            before = days.index(next(d for d in days if d >= dt.date(2026, 2, 11))) - 1
            self.assertEqual(MarketView(panel, before).facts('revenue', '2330'), [])
            self.assertEqual(MarketView(panel, before + 1).facts('revenue', '2330')[0]['revenue'], 120)
            q2 = MarketView(panel, len(days) - 1).facts('statement', '2330', 2)
            self.assertEqual([r['period'] for r in q2], [(2026, 1), (2026, 2)])
            self.assertEqual(q2[1]['q_revenue'], 150)   # 250 cumulative - 100
            self.assertAlmostEqual(q2[1]['q_eps'], 1.5)
            june = next(i for i, d in enumerate(days) if d.month == 6)
            self.assertEqual(len(MarketView(panel, june).facts('statement', '2330', 2)), 1)  # Q2 not public yet


class FundamentalStrategyTests(unittest.TestCase):
    def view_with(self, dataset, records, n=30):
        panel = make_panel({'2330': steady(n), '2317': steady(n), '1101': steady(n)}, n)
        panel.facts = Facts()
        for symbol, recs in records.items():
            for i, rec in recs:
                panel.facts.add(dataset, symbol, i, rec)
        return MarketView(panel, n - 1)

    def test_revenue_growth_and_staleness(self):
        today = trading_days(30)[-1]
        recent = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        view = self.view_with('revenue', {
            '2330': [(5, dict(period=recent, revenue=150, last_year=100))],
            '2317': [(5, dict(period=recent, revenue=110, last_year=100))],
            '1101': [(5, dict(period=(today.year - 1, 1), revenue=900, last_year=100))],  # stale
        })
        picks = strategies.get('revenue_yoy').select(view)
        self.assertEqual([p.symbol for p in picks], ['2330', '2317'])

    def test_valuation_rules(self):
        view = self.view_with('valuation', {
            '2330': [(20, dict(date=trading_days(30)[20], pe=10.0, pb=2.0, dividend_yield=5.0))],
            '2317': [(20, dict(date=trading_days(30)[20], pe=None, pb=0.8, dividend_yield=None))],
        })
        self.assertEqual([p.symbol for p in strategies.get('low_pe').select(view)], ['2330'])
        self.assertEqual([p.symbol for p in strategies.get('low_pb').select(view)], ['2317', '2330'])
        self.assertEqual([p.symbol for p in strategies.get('high_dividend_yield').select(view)], ['2330'])

    def test_roe_needs_four_consecutive_quarters(self):
        today = trading_days(30)[-1]
        q = (today.month - 1) // 3 + 1
        periods = []
        y, k = today.year, q - 1
        for _ in range(4):
            if k == 0:
                y, k = y - 1, 4
            periods.insert(0, (y, k))
            k -= 1
        recs = [(i, dict(period=p, q_net_income_parent=25, equity=500, assets=1000, q_gross_profit=50, q_eps=1))
                for i, p in enumerate(periods)]
        gap = [recs[0], recs[2], recs[3]]
        view = self.view_with('statement', {'2330': recs, '2317': gap})
        picks = strategies.get('roe').select(view)
        self.assertEqual([p.symbol for p in picks], ['2330'])
        self.assertAlmostEqual(picks[0].score, 0.2)
        self.assertAlmostEqual(strategies.get('gross_profitability').select(view)[0].score, 0.2)

    def test_snapshot_rejects_fundamental_strategies(self):
        from tw_stock_digest.panel import screen_snapshot
        from test_market import twse
        from tw_stock_digest.market import normalize
        with self.assertRaisesRegex(ValueError, 'needs history'):
            screen_snapshot(normalize('TWSE', [twse()]), ['revenue_yoy'])


class PlaceboTests(unittest.TestCase):
    def test_random_placebo_is_reproducible_and_centered(self):
        from tw_stock_digest import acceptance, backtest
        bars = {f'{1000 + k}': steady(80, step=0.001 * (k % 5 - 2)) for k in range(40)}
        panel = make_panel(bars, 80)
        bench = backtest.benchmark_equal_weight(panel, 0, 79)
        a = acceptance.placebo(panel, 0, 79, bench, seeds=6)
        b = acceptance.placebo(panel, 0, 79, bench, seeds=6)
        self.assertEqual(a['values'], b['values'])
        self.assertEqual(len(a['values']), 6)
        self.assertLess(abs(a['mean']), 0.5)
