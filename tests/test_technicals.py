import unittest

from test_insights import day
from tw_stock_digest import supplement, technicals


def history(closes, symbol='2330', **fields):
    return [day(f'd{i:03d}', {symbol: dict(open=c, high=c + 1, low=c - 1, close=c, turnover_twd=2e8, **fields)})
            for i, c in enumerate(closes)]


class IndicatorTests(unittest.TestCase):
    def test_bias_rsi_and_range_position(self):
        closes = [100.0] * 19 + [110.0]
        self.assertEqual(technicals.bias(closes, 20), 9.45)   # 110 / 100.5 - 1
        self.assertIsNone(technicals.bias(closes, 60))
        self.assertEqual(technicals.rsi([float(x) for x in range(1, 17)]), 100.0)
        self.assertEqual(technicals.rsi([10.0, 11.0] * 7 + [10.0, 12.0]), 53.3)  # gains 8, losses 7 over 14 changes
        self.assertIsNone(technicals.rsi([10.0] * 15))            # flat: undefined, not 50
        self.assertIsNone(technicals.rsi([1.0] * 14))
        self.assertEqual(technicals.position([1.0, 3.0, 2.0], 3), 50.0)

    def test_kd_follows_the_close_within_its_range(self):
        rising = history([float(x) for x in range(100, 120)])
        k, d = technicals.kd(rising, '2330')
        self.assertGreater(k, d)
        self.assertGreater(k, 85)
        falling = history([float(x) for x in range(120, 100, -1)])
        k, d = technicals.kd(falling, '2330')
        self.assertLess(k, d)
        self.assertLess(k, 15)
        self.assertEqual(technicals.kd(rising[:10], '2330'), (None, None))

    def test_macd_sign_and_warm_up(self):
        flat_then_up = [100.0] * 30 + [100.0 + 2 * i for i in range(1, 11)]
        dif, histogram = technicals.macd(flat_then_up)
        self.assertGreater(dif, 0)
        self.assertGreater(histogram, 0)
        self.assertEqual(technicals.macd([100.0] * 40), (0.0, 0.0))
        self.assertEqual(technicals.macd([100.0] * 34), (None, None))

    def test_breadth_and_new_highs_need_history_and_liquidity(self):
        days = history([100.0] * 19 + [105.0])
        for d, close in zip(days, [100.0] * 19 + [90.0]):
            d['rows']['1101'] = dict(d['rows']['2330'], name='1101', close=close, high=close + 1, low=close - 1)
            d['rows']['9999'] = dict(d['rows']['2330'], name='9999', close=close + 50, turnover_twd=1e6)  # illiquid
        out = technicals.compute(days, ['2330', '0000'])
        self.assertEqual(out['breadth'], dict(counted=2, above_ma20_pct=50.0, above_ma60_pct=None, window=20,
                                              new_highs=1, new_lows=1))
        self.assertEqual([x['symbol'] for x in out['new_highs']], ['2330'])
        self.assertEqual([w['symbol'] for w in out['watchlist']], ['2330'])
        self.assertEqual((out['watchlist'][0]['bias20_pct'], out['watchlist'][0]['bias60_pct']), (4.74, None))
        short = technicals.compute(days[-5:], ['2330'])
        self.assertNotIn('breadth', short)
        self.assertIn('需至少 20 日', short['note'])


class SupplementTests(unittest.TestCase):
    def days(self):
        days = history([100.0] * 6, margin=1000.0, margin_change=10.0, short=50.0, sbl=20.0, sbl_change=1.0,
                       daytrade=400.0, big400_pct=60.0, big1000_pct=50.0, holders=1000)
        for d in days:
            d['rows']['2330'].update(volume_shares=1000.0, turnover_twd=6e8, kind='equity')
            d['rows']['1101'] = dict(d['rows']['2330'], name='1101', margin_change=-30.0, daytrade=900.0, big400_pct=40.0)
            d['tdcc_date'] = '2026-10-30'
        days[-1]['tdcc_date'] = '2026-11-06'
        days[-1]['rows']['2330'].update(big400_pct=61.5, holders=990)
        return days

    def test_margin_ranks_by_amount_and_sums_the_window(self):
        out = supplement.summarize({'twse_margin': dict(margin_twd=5e11, margin_change_twd=-2e9, short_lots=100.0,
                                                        short_lots_change=-5.0, margin_lots_change=-10.0)},
                                   self.days(), ['2330'])
        margin = out['margin']
        self.assertEqual(margin['market']['TWSE']['margin_change_100m_twd'], -20.0)
        self.assertEqual([x['symbol'] for x in margin['increases']], ['2330'])
        self.assertEqual([(x['symbol'], x['margin_change_lots'], x['margin_change_100m_twd']) for x in margin['decreases']],
                         [('1101', -30, -0.03)])
        self.assertEqual(margin['watchlist'], [dict(symbol='2330', name='2330', margin_lots=1000, margin_change_lots=10,
                                                    margin_window_change_lots=50, window_days=5, short_lots=50,
                                                    short_margin_ratio_pct=5.0, sbl_lots=20, sbl_change_lots=1)])

    def test_day_trading_share_and_large_holder_change(self):
        out = supplement.summarize({'tdcc': dict(date='2026-11-06')}, self.days(), ['2330'])
        self.assertEqual([(x['symbol'], x['daytrade_pct']) for x in out['daytrade']['highest']],
                         [('1101', 90.0), ('2330', 40.0)])
        holders = out['holders']
        self.assertEqual((holders['date'], holders['previous_date']), ('2026-11-06', '2026-10-30'))
        self.assertEqual(holders['watchlist'][0], dict(symbol='2330', name='2330', big400_pct=61.5, big400_change_pp=1.5,
                                                       big1000_pct=50.0, big1000_change_pp=0.0, holders=990,
                                                       holders_change=-10))
        self.assertEqual([x['symbol'] for x in holders['increases']], ['2330'])
        self.assertEqual(holders['decreases'], [])

    def test_sections_without_data_are_left_out(self):
        self.assertEqual(sorted(supplement.summarize({}, history([100.0]), ['2330'])), [])
        flags = supplement.summarize({'twse_notice': [dict(symbol='2330', name='x', reason='r')], 'tpex_punish': [
            dict(symbol='6488', name='y', start='2026-11-10', end='2026-11-16', measure='m')]}, history([100.0]),
            ['2330', '6488', '0050'])['flags']
        self.assertEqual((flags['attention_count'], flags['disposal_count'], flags['watchlist']), (1, 1, ['2330', '6488']))


if __name__ == '__main__':
    unittest.main()
