import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from test_market import tpex, twse
from tw_stock_digest import insights
from tw_stock_digest.pipeline import run


def day(date, rows, flows=True):
    out = {}
    for symbol, values in rows.items():
        base = dict(market='TWSE', kind='equity', name=symbol, open=10.0, high=10.0, low=10.0, close=10.0,
                    change_pct=0.0, volume_shares=1000.0, turnover_twd=10000.0, foreign=0.0, trust=0.0, dealer=0.0)
        base.update(values)
        out[symbol] = base
    return dict(date=date, rows=out, has_flows=flows)


class ParserTests(unittest.TestCase):
    def test_industry_names_cover_both_exchanges_codes(self):
        rows = [{'公司代號': '2330', '產業別': '24'}, {'公司代號': '9999', '產業別': '99'}]
        self.assertEqual(insights.parse_industry(rows, '公司代號', '產業別'), {'2330': '半導體業', '9999': '未分類'})

    def test_report_dates(self):
        self.assertEqual(insights.report_date({'date': '20261102'}), dt.date(2026, 11, 2))
        self.assertEqual(insights.report_date({'tables': [{'date': '115/11/02'}]}), dt.date(2026, 11, 2))

    def test_flow_and_valuation_parsers(self):
        twse_flows = {'fields': ['證券代號', '證券名稱', '外陸資買賣超股數(不含外資自營商)', '外資自營商買賣超股數',
                                 '投信買賣超股數', '自營商買賣超股數'],
                      'data': [['2330', '台積電', '1,000', '-100', '50', '-20']]}
        self.assertEqual(insights.parse_twse_flows(twse_flows)['2330'], dict(foreign=900, trust=50, dealer=-20))
        row = ['6488', '環球晶'] + ['0'] * 8 + ['300', '0', '0', '-40'] + ['0'] * 8 + ['7', '267']
        self.assertEqual(insights.parse_tpex_flows({'tables': [{'data': [row]}]})['6488'],
                         dict(foreign=300, trust=-40, dealer=7))
        pe = {'tables': [{'fields': ['股票代號', '公司名稱', '本益比', '每股股利', '股利年度', '殖利率(%)', '股價淨值比'],
                          'data': [['6488', '環球晶', '15.20', '8', 114, '3.10', '2.05'],
                                   ['1240', '茂生', 'N/A', '0', 114, '0.00', '1.10']]}]}
        out = insights.parse_valuation(pe)
        self.assertEqual(out['6488'], dict(pe=15.2, pb=2.05, dividend_yield=3.1))
        self.assertIsNone(out['1240']['pe'])


class FetchTests(unittest.TestCase):
    def test_one_failing_source_keeps_the_others_and_marks_incomplete(self):
        def get(url):
            if 'T86' in url:
                raise RuntimeError('blocked')
            if 'BWIBBU' in url:
                return {'stat': 'OK', 'date': '20261102', 'fields': ['證券代號', '本益比', '股價淨值比', '殖利率(%)'],
                        'data': [['2330', '20.0', '5.0', '1.5']]}
            if 'peQryDate' in url:
                return {'tables': [{'date': '115/11/01', 'fields': ['股票代號'], 'data': [['6488']]}]}  # wrong day
            if 'dailyTrade' in url:
                return {'tables': [{'date': '115/11/02', 'data': []}]}
            return [{'公司代號': '2330', '產業別': '24'}] if 'twse' in url else []
        extras, status = insights.fetch_extras('2026-11-02', get=get)
        self.assertEqual(extras['2330'], dict(pe=20.0, pb=5.0, dividend_yield=1.5, industry='半導體業'))
        self.assertEqual(status['twse_flows'], 'failed:RuntimeError')
        self.assertEqual(status['tpex_valuation'], 'date_mismatch')
        self.assertEqual(status['tpex_flows'], 'not_published')
        self.assertEqual(status['tpex_industry'], 'not_published')
        self.assertFalse(insights.extras_complete(status))
        self.assertTrue(insights.extras_complete({'a': 'ok', 'b': 'ok'}))


class CalculationTests(unittest.TestCase):
    def test_streaks_stop_at_sign_change_zero_or_missing_flows(self):
        days = [day('d0', {'A': dict(foreign=5)}, flows=False),
                day('d1', {'A': dict(foreign=-1)}),
                day('d2', {'A': dict(foreign=2)}),
                day('d3', {'A': dict(foreign=3)})]
        self.assertEqual(insights.streak(days, 'A', 'foreign'), 2)
        days[-1]['rows']['A']['foreign'] = -3
        self.assertEqual(insights.streak(days, 'A', 'foreign'), -1)
        days[-1]['rows']['A']['foreign'] = 0
        self.assertEqual(insights.streak(days, 'A', 'foreign'), 0)

    def test_institutional_amounts_and_window(self):
        days = [day(f'd{i}', {'A': dict(foreign=100 * (i + 1), close=10.0), 'B': dict(foreign=-1e7, close=20.0)})
                for i in range(7)]
        out = insights.institutions(days, ['A'])
        self.assertEqual(out['window_days'], 5)
        # A: (300+400+500+600+700) shares x 10 TWD = 25,000 TWD = 0.00025 億
        self.assertEqual(out['watchlist'][0]['foreign_window_100m_twd'], round(25000 / 1e8, 2))
        self.assertEqual(out['watchlist'][0]['foreign_streak_days'], 7)
        self.assertEqual(out['foreign_sell'][0]['symbol'], 'B')
        self.assertEqual(out['foreign_sell'][0]['net_100m_twd'], -10.0)   # 5 x 1e7 x 20 = 1e9
        self.assertEqual(out['today_100m_twd']['ALL']['foreign'], round((700 * 10 - 1e7 * 20) / 1e8, 2))

    def test_mfi_and_obv(self):
        closes = [10, 11, 10, 12, 11, 13, 12, 14, 13, 15, 14, 16, 15, 17, 16]
        days = [day(f'd{i}', {'A': dict(high=c, low=c, close=c, volume_shares=100.0,
                                        change_pct=0.0 if i == 0 else 100 * (c / closes[i - 1] - 1))})
                for i, c in enumerate(closes)]
        up = sum(c for p, c in zip(closes, closes[1:]) if c > p) * 100
        down = sum(c for p, c in zip(closes, closes[1:]) if c < p) * 100
        self.assertAlmostEqual(insights.mfi(days, 'A'), round(100 - 100 / (1 + up / down), 1))
        self.assertEqual(insights.obv(days, 'A'), 0.0)  # 5 up days, 5 down days of equal volume
        self.assertIsNone(insights.mfi(days[:10], 'A'))

    def test_volume_surge_requires_history_up_day_and_size(self):
        history = [day(f'd{i}', {'A': dict(turnover_twd=1e8), 'B': dict(turnover_twd=1e8),
                                 'C': dict(turnover_twd=1e6)}) for i in range(20)]
        today = day('t', {'A': dict(turnover_twd=3e8, change_pct=2.0), 'B': dict(turnover_twd=3e8, change_pct=-2.0),
                          'C': dict(turnover_twd=5e6, change_pct=5.0)})
        out = insights.money_flow(history + [today], [])
        self.assertEqual([x['symbol'] for x in out['volume_surge_up']], ['A'])
        self.assertEqual(out['volume_surge_up'][0]['turnover_ratio'], 3.0)
        self.assertIn('note', insights.money_flow(history[-5:] + [today], []))

    def test_rotation_turnover_share_change(self):
        extras = {s: {'industry': 'X' if s < 'F' else 'Y'} for s in 'ABCDEFGHIJ'}
        history = [day(f'd{i}', {s: dict(turnover_twd=1e8) for s in 'ABCDEFGHIJ'}) for i in range(20)]
        today = day('t', {s: dict(turnover_twd=3e8 if s < 'F' else 1e8, change_pct=1.0) for s in 'ABCDEFGHIJ'})
        out = insights.rotation(history + [today], extras)
        gaining = out['gaining'][0]
        self.assertEqual((gaining['industry'], gaining['turnover_share_pct'], gaining['share_average_pct']),
                         ('X', 75.0, 50.0))
        self.assertEqual(gaining['share_change_pp'], 25.0)
        self.assertEqual(out['losing'][0]['industry'], 'Y')
        self.assertEqual(gaining['median_return_5d_pct'], 1.0)  # only today moved +1%


class PipelineIntegrationTests(unittest.TestCase):
    def test_history_feeds_momentum_and_incomplete_extras_are_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, output = root / 'data', root / 'output'

            def extras_ok(trade_date):
                return ({'2330': dict(industry='半導體業', pe=20.0, pb=5.0, dividend_yield=1.0,
                                      foreign=1000.0, trust=0.0, dealer=0.0)}, {'all': 'ok'})

            def fetch_for(roc):
                return lambda market: [twse(Date=roc)] if market == 'TWSE' else [tpex(Date=roc)]

            run('2026-10-21', output, ['2330'], fetch=fetch_for('1151021'), archive_root=archive, extras=extras_ok)
            self.assertTrue((archive / 'daily/2026-10-21/extras.csv').exists())
            first = json.loads((archive / 'latest.json').read_text())
            self.assertTrue(first['extras_complete'])

            def extras_missing(trade_date):
                return ({}, {'twse_flows': 'not_published'})
            run(None, output, ['2330'], fetch=fetch_for('1151022'), archive_root=archive, extras=extras_missing)
            self.assertFalse(json.loads((archive / 'latest.json').read_text())['extras_complete'])
            # The same day is collected again on the next run once flows exist.
            state = run(None, output, ['2330'], fetch=fetch_for('1151022'), archive_root=archive, extras=extras_ok)
            self.assertEqual(state['status'], 'complete')
            context = json.loads((archive / 'daily/2026-10-22/context.json').read_text())
            self.assertEqual(context['insights']['institutions']['window_days'], 2)
            self.assertEqual(context['insights']['institutions']['watchlist'][0]['foreign_streak_days'], 2)
            self.assertIn('## 法人資金流向', (archive / 'latest.md').read_text())
            state = run(None, output, ['2330'], fetch=fetch_for('1151022'), archive_root=archive, extras=extras_ok)
            self.assertEqual(state['reason'], 'already_saved')
