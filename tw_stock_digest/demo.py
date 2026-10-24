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
