import datetime as dt
import unittest

from tw_stock_digest.backfill import tpex_rows, twse_rows
from tw_stock_digest.market import normalize

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
