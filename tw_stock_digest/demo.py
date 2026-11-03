"""Small, explicitly synthetic data; never a fallback for failed live requests."""
DEMO_DATE = '2026-10-21'


def fetch_demo(market):
    if market == 'TWSE':
        return [dict(Date='1151021', Code=code, Name=name, TradeVolume='1200000',
                     TradeValue=str(value), OpeningPrice=str(close - 1), HighestPrice=str(close + 2),
                     LowestPrice=str(close - 2), ClosingPrice=str(close), Change='1', Transaction='1200')
                for code, name, close, value in [('0050', '示範ETF', 50, 60000000),
                                                ('2330', '示範上市公司', 100, 120000000)]]
    return [dict(Date='1151021', SecuritiesCompanyCode='6488', CompanyName='示範上櫃公司',
                 TradingShares='100000', TransactionAmount='9500000', Open='96', High='97',
                 Low='94', Close='95', Change='-5', TransactionNumber='200')]


def demo_extras(trade_date):
    """Synthetic flows, valuation and industries for the demo stocks."""
    return ({'0050': dict(pe=18.5, pb=2.9, dividend_yield=2.1, foreign=-500000, trust=300000, dealer=0),
             '2330': dict(industry='半導體業', pe=21.0, pb=5.2, dividend_yield=1.4,
                          foreign=1200000, trust=200000, dealer=-50000),
             '6488': dict(industry='半導體業', pe=15.0, pb=2.0, dividend_yield=3.5,
                          foreign=-80000, trust=10000, dealer=0)},
            {'extras': 'demo'})
