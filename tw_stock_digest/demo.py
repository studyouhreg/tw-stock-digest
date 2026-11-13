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


def demo_auxiliary(trade_date, watchlist):
    """Synthetic supplementary data so the demo shows every section."""
    payloads = {
        'twse_index': dict(taiex=dict(close=20000.0, change=150.0, change_pct=0.76),
                           sectors=[dict(name='半導體類', close=500.0, change_pct=1.5),
                                    dict(name='金融保險類', close=2000.0, change_pct=-0.4),
                                    dict(name='航運類', close=180.0, change_pct=0.2)]),
        'twse_turnover': dict(turnover_twd=3.5e11, volume_shares=6e9, trades=2e6),
        'tpex_index': dict(close=250.0, change=-1.0, change_pct=-0.4, turnover_twd=9e10),
        'taifex_futures': dict(net_oi=dict(foreign=-30000, trust=12000, dealer=500),
                               net_oi_change=dict(foreign=-1500, trust=300, dealer=-200),
                               net_traded=dict(foreign=-1200, trust=250, dealer=-150)),
        'taifex_pcr': dict(volume_ratio=95.0, oi_ratio=110.0, put_oi=120000.0, call_oi=109000.0, oi_ratio_change=2.5),
        'twse_margin': dict(margin_twd=3.2e11, margin_change_twd=1.5e9, margin_lots=7000000.0,
                            margin_lots_change=12000.0, short_lots=250000.0, short_lots_change=-3000.0),
        'twse_notice': [], 'twse_punish': [],
        'twse_material': dict(count=120, items=[dict(symbol='2330', name='示範上市公司', date=trade_date, time='17:30',
                                                     subject='示範：公告董事會決議')]),
        'twse_revenue': dict(month=trade_date[:7], reported=900, median_yoy_pct=4.2, items=[
            dict(symbol='2330', name='示範上市公司', month=trade_date[:7], revenue_100m_twd=2500.0, mom_pct=3.1,
                 yoy_pct=25.4, ytd_yoy_pct=30.2)]),
    }
    symbols = {'2330': dict(margin=30000.0, margin_change=500.0, short=800.0, short_change=-20.0,
                            sbl=12000.0, sbl_change=300.0, daytrade=8000000.0),
               '6488': dict(margin=4000.0, margin_change=-120.0, short=90.0, short_change=5.0,
                            sbl=600.0, sbl_change=-10.0, daytrade=900000.0)}
    return symbols, payloads, {name: 'demo' for name in payloads}
