import unittest

from tw_stock_digest.market import normalize, build_context, parse_date


def twse(code='2330', **changes):
    row = dict(Date='1151021', Code=code, Name='測試公司', TradeVolume='1,200',
               TradeValue='120,000', OpeningPrice='98', HighestPrice='101',
               LowestPrice='97', ClosingPrice='100', Change='2', Transaction='20')
    row.update(changes)
    return row


def tpex(code='6488', **changes):
    row = dict(Date='1151021', SecuritiesCompanyCode=code, CompanyName='測試上櫃',
               TradingShares='2,000', TransactionAmount='190,000', Open='96',
               High='97', Low='94', Close='95', Change='-5', TransactionNumber='30',
               Average='95', LatestBidPrice='95', LatesAskPrice='96', Capitals='10000',
               NextReferencePrice='95', NextLimitUp='104.5', NextLimitDown='85.5')
    row.update(changes)
    return row


class MarketTests(unittest.TestCase):
    def test_roc_and_gregorian_dates(self):
        for text in ('1151021', '115/10/21', '2026-10-21', '20261021'):
            self.assertEqual(parse_date(text), '2026-10-21')
        with self.assertRaises(ValueError):
            parse_date('00000000')

    def test_twse_units_leading_zero_and_percentage(self):
        quote = normalize('TWSE', [twse('0050')])[0]
        self.assertEqual(quote['symbol'], '0050')
        self.assertEqual(quote['kind'], 'etf')
        self.assertEqual(quote['volume_shares'], 1200)
        self.assertEqual(quote['change_pct'], 2.0408)
        self.assertEqual(quote['trade_date'], '2026-10-21')

    def test_tpex_mapping_and_warrant_exclusion(self):
        quotes = normalize('TPEX', [tpex(), tpex('700001'), tpex('12345')])
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0]['turnover_twd'], 190000)
        self.assertEqual(quotes[0]['change_pct'], -5)

    def test_suspended_missing_values_are_not_zero(self):
        quote = normalize('TWSE', [twse(ClosingPrice='--', OpeningPrice='--',
                   HighestPrice='--', LowestPrice='--', Change='X', TradeVolume='0')])[0]
        self.assertIsNone(quote['close'])
        self.assertIsNone(quote['change_pct'])
        self.assertIn('missing_close', quote['quality_flags'])

    def test_reject_duplicate_bad_numbers_and_schema_drift(self):
        for rows in ([twse(), twse()], [twse(ClosingPrice='NaN')],
                     [twse(TradeVolume='-1')], [twse(TradeVolume='1.5')],
                     [twse(HighestPrice='80')], [dict(Date='1151021', Code='2330')]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                normalize('TWSE', rows)

    def test_malformed_thousands_grouping_is_not_interpreted_as_money(self):
        for malformed in ('1,2', '12,,000', ',1200,', '1,234,56'):
            with self.subTest(value=malformed), self.assertRaises(ValueError):
                normalize('TWSE', [twse(TradeVolume=malformed)])
        quote = normalize('TWSE', [twse(TradeValue='1,234.50')])[0]
        self.assertEqual(quote['turnover_twd'], 1234.5)

    def test_context_separates_markets_etfs_and_missing_watchlist(self):
        quotes = normalize('TWSE', [twse(), twse('0050')]) + normalize('TPEX', [tpex()])
        ctx = build_context(quotes, ['0050', '6488', '9999'])
        self.assertEqual(ctx['markets']['TWSE']['equities']['advancers'], 1)
        self.assertEqual(ctx['markets']['TWSE']['etf_count'], 1)
        self.assertEqual(ctx['markets']['TPEX']['equities']['decliners'], 1)
        self.assertEqual(ctx['missing_watchlist'], ['9999'])
        self.assertEqual(len(ctx['watchlist']), 2)

    def test_context_rejects_mixed_trading_dates_and_empty_market(self):
        quotes = normalize('TWSE', [twse()]) + normalize('TPEX', [tpex(Date='1151020')])
        with self.assertRaisesRegex(ValueError, 'date'):
            build_context(quotes, [])
        with self.assertRaises(ValueError):
            normalize('TWSE', [])


if __name__ == '__main__':
    unittest.main()
