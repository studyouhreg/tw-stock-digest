import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_market import tpex, twse
from tw_stock_digest import backfill
from tw_stock_digest.backfill import saved_fetcher, tpex_rows, twse_rows
from tw_stock_digest.market import normalize
from tw_stock_digest.pipeline import run

DAY = dt.date(2026, 10, 28)
TWSE_FIELDS = ['證券代號', '證券名稱', '成交股數', '成交筆數', '成交金額', '開盤價', '最高價', '最低價',
               '收盤價', '漲跌(+/-)', '漲跌價差', '最後揭示買價', '最後揭示買量', '最後揭示賣價', '最後揭示賣量', '本益比']
TPEX_FIELDS = ['代號', '名稱', '收盤 ', '漲跌', '開盤 ', '最高 ', '最低', '成交股數  ', ' 成交金額(元)', ' 成交筆數 ']


def twse_row(code, close, sign, change):
    return [code, '測試', '1,234,000', '1,000', '2,960,000,000', '2,380.00', '2,405.00', '2,380.00', close,
            sign, change, '', '', '', '', '']


class BackfillConversionTests(unittest.TestCase):
    def test_twse_report_rows_normalize_like_the_snapshot_api(self):
        data = {'stat': 'OK', 'tables': [{'fields': ['指數']}, {'fields': TWSE_FIELDS, 'data': [
            twse_row('2330', '2,400.00', "<p style= color:red>+</p>", '15.00'),
            twse_row('2317', '2,390.00', "<p style= color:green>-</p>", '10.00'),
            twse_row('0056', '2,390.00', '<p>X</p>', '0.00'),
            twse_row('1101', '2,390.00', '<p> </p>', '0.00')]}]}
        quotes = {q['symbol']: q for q in normalize('TWSE', twse_rows(data, DAY))}
        self.assertEqual(quotes['2330']['trade_date'], '2026-10-28')
        self.assertEqual((quotes['2330']['close'], quotes['2330']['change']), (2400.0, 15.0))
        self.assertEqual(quotes['2317']['change'], -10.0)
        self.assertIsNone(quotes['0056']['change'])
        self.assertIn('missing_or_corporate_action_change', quotes['0056']['quality_flags'])
        self.assertEqual(quotes['1101']['change'], 0.0)
        self.assertEqual(quotes['2330']['volume_shares'], 1234000)
        self.assertEqual(quotes['2330']['turnover_twd'], 2960000000.0)

    def test_tpex_report_rows_normalize_like_the_snapshot_api(self):
        data = {'stat': 'ok', 'tables': [{'fields': TPEX_FIELDS, 'data': [
            ['6488', '環球晶', '814.00', '-12.00', '855.00', '857.00', '807.00', '12,000,000', '9,800,000,000', '23,000'],
            ['00679B', '元大美債20年', '26.01', '除息', '25.86', '26.05', '25.82', '1,000', '26,010', '3'],
            ['1240', '茂生農經', '----', '0.00', '----', '----', '----', '0', '0', '0']]}]}
        quotes = {q['symbol']: q for q in normalize('TPEX', tpex_rows(data, DAY))}
        self.assertEqual((quotes['6488']['close'], quotes['6488']['change']), (814.0, -12.0))
        self.assertIsNone(quotes['00679B']['change'])
        self.assertIsNone(quotes['1240']['close'])

    def test_empty_reports_are_rejected(self):
        with self.assertRaises(ValueError):
            twse_rows({'stat': '很抱歉，沒有符合條件的資料!'}, DAY)
        with self.assertRaises(ValueError):
            tpex_rows({'stat': 'ok', 'tables': [{'fields': TPEX_FIELDS, 'data': []}]}, DAY)


class ReuseSavedQuotesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.archive, self.output = Path(self.tmp.name) / 'data', Path(self.tmp.name) / 'output'
        self.rows = {'TWSE': [twse(), twse('1101', Name='=cmd', Change='X', ClosingPrice='--'),
                              twse('0050', TradeVolume='12,345,678', TradeValue='1,234,567,890.5', Change='-0.35')],
                     'TPEX': [tpex()]}
        extras = lambda day: ({'2330': dict(industry='半導體業', pe=20.0, foreign=1000.0, trust=0.0, dealer=0.0)},
                              {name: 'ok' for name in backfill.SOURCE_FIELDS})
        run('2026-10-21', self.output, ['2330'], fetch=self.rows.__getitem__, archive_root=self.archive, extras=extras,
            sources=backfill.URLS, source_note='From the per-date reports.')
        self.folder = self.archive / 'daily/2026-10-21'

    def test_saved_quotes_round_trip_through_the_normalizer(self):
        fetch = saved_fetcher(self.folder)
        for market, rows in self.rows.items():
            self.assertEqual(normalize(market, fetch(market)), normalize(market, rows))

    def test_rebuild_adds_supplementary_data_and_keeps_everything_else(self):
        before = (self.folder / 'quotes.csv').read_bytes()

        def auxiliary(trade_date, watchlist):
            return ({'2330': dict(margin=500.0, margin_change=5.0)},
                    {'twse_index': dict(taiex=dict(close=20000.0, change=100.0, change_pct=0.5), sectors=[])},
                    {'twse_index': 'ok', 'twse_margin': 'ok'})

        def no_network(trade_date):
            raise AssertionError('saved flows and valuation are complete; nothing to download')
        args = ['--date', '2026-10-21', '--repo-dir', str(self.archive), '--output', str(self.output),
                '--watchlist', '2330', '--reuse-quotes']
        with patch.object(backfill, 'fetch_auxiliary', auxiliary), patch.object(backfill, 'fetch_extras', no_network), \
                patch('builtins.print'):
            self.assertEqual(backfill.main(args), 0)
            self.assertEqual(backfill.main(args), 0)  # repeatable
        self.assertEqual((self.folder / 'quotes.csv').read_bytes(), before)
        manifest = json.loads((self.folder / 'manifest.json').read_text())
        self.assertEqual(manifest['sources'], backfill.URLS)
        self.assertEqual(manifest['source_note'], 'From the per-date reports. ' + backfill.REBUILT)
        self.assertEqual(manifest['extras']['twse_margin'], 'ok')
        context = json.loads((self.folder / 'context.json').read_text())
        self.assertEqual(context['insights']['index']['taiex']['close'], 20000.0)
        self.assertEqual(context['insights']['valuation']['watchlist'][0]['pe'], 20.0)
        self.assertIn('2330,半導體業,20.0', (self.folder / 'extras.csv').read_text(encoding='utf-8-sig'))

    def test_rebuild_refuses_a_day_whose_rejected_rows_would_be_forgotten(self):
        manifest = json.loads((self.folder / 'manifest.json').read_text())
        manifest['rejected_rows'] = [['TWSE', '9999', 'invalid_number']]
        (self.folder / 'manifest.json').write_text(json.dumps(manifest))
        with patch('builtins.print') as printed:
            code = backfill.main(['--date', '2026-10-21', '--repo-dir', str(self.archive), '--output', str(self.output),
                                  '--reuse-quotes', '--no-aux'])
        self.assertEqual(code, 1)
        self.assertIn('rejected rows', str(printed.call_args))


if __name__ == '__main__':
    unittest.main()
