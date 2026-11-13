import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from test_market import tpex, twse
from tw_stock_digest import auxiliary as aux
from tw_stock_digest.analysis import LIMIT, context_text, make_prompt
from tw_stock_digest.http import RemoteError
from tw_stock_digest.insights import core_complete, extras_complete, read_saved_extras
from tw_stock_digest.pipeline import run
from tw_stock_digest.report import headline

DAY = dt.date(2026, 11, 11)
UP = "<p style ='color:red'>+</p>"
DOWN = "<p style ='color:green'>-</p>"


def twse_index(date='20261111'):
    fields = ['指數', '收盤指數', '漲跌(+/-)', '漲跌點數', '漲跌百分比(%)', '特殊處理註記']
    return dict(stat='OK', date=date, tables=[
        dict(title='115年11月11日 價格指數(臺灣證券交易所)', fields=fields, data=[
            ['寶島股價指數', '48,733.09', UP, '1,693.72', '3.60', ''],
            ['發行量加權股價指數', '44,257.15', UP, '1,545.23', '3.62', ''],
            ['電子工業類指數', '2,745.45', UP, '104.48', '3.96', ''],
            ['半導體類指數', '1,502.40', UP, '64.65', '4.50', ''],
            ['鋼鐵類指數', '100.00', DOWN, '1.00', '-0.99', ''],
            ['未含金融指數', '--', '', '--', '--', '']]),
        dict(title='價格指數(跨市場)', fields=fields, data=[['臺灣生技指數', '4,008.42', UP, '60.58', '1.53', '']])])


def twse_turnover():
    return dict(stat='OK', date='20261111', fields=['日期', '成交股數', '成交金額', '成交筆數', '發行量加權股價指數', '漲跌點數'],
                data=[['115/11/10', '9,000,000,000', '700,000,000,000', '3,000,000', '42,711.92', '10.00'],
                      ['115/11/11', '11,518,562,054', '876,295,459,349', '3,928,796', '44,257.15', '1,545.23']])


def tpex_index():
    return dict(stat='ok', tables=[dict(title='日成交量值指數', fields=['日期', '成交張數', '金額（仟元）', '筆數', '櫃買指數', '漲/跌'],
                                        data=[['115/11/10', '1,041,134', '208,091,965', '937,825', 332.97, 11.0],
                                              ['115/11/11', '959,650', '201,347,671', '875,656', 344.11, 11.14]])])


FUTURES = '''日期,商品名稱,身份別,多方交易口數,多方交易契約金額(千元),空方交易口數,空方交易契約金額(千元),多空交易口數淨額,多空交易契約金額淨額(千元),多方未平倉口數,多方未平倉契約金額(千元),空方未平倉口數,空方未平倉契約金額(千元),多空未平倉口數淨額,多空未平倉契約金額淨額(千元)
2026/11/10,臺股期貨,自營商,1,1,1,1,10,1,1,1,1,1,85,1
2026/11/10,臺股期貨,投信,1,1,1,1,20,1,1,1,1,1,98519,1
2026/11/10,臺股期貨,外資及陸資,1,1,1,1,30,1,1,1,1,1,-103473,1
2026/11/11,臺股期貨,自營商,1,1,1,1,-92,1,1,1,1,1,33,1
2026/11/11,臺股期貨,投信,1,1,1,1,3351,1,1,1,1,1,101870,1
2026/11/11,臺股期貨,外資及陸資,1,1,1,1,-779,1,1,1,1,1,-104271,1
2026/11/11,小型臺指期貨,外資及陸資,1,1,1,1,5,1,1,1,1,1,999,1
'''
PCR = '''日期,賣權成交量,買權成交量,買賣權成交量比率%,賣權未平倉量,買權未平倉量,買賣權未平倉量比率%
2026/11/11,433737,405048,107.08,57877,88811,65.17,
2026/11/10,245968,207818,118.36,102261,153672,66.54,
'''
LARGE = '''日期,商品(契約),商品名稱(契約名稱),到期月份(週別),交易人類別,前五大交易人買方,前五大交易人賣方,前十大交易人買方,前十大交易人賣方,全市場未沖銷部位數
2026/11/11,TX     ,臺股期貨,202611  ,0,1,1,1,1,1
2026/11/11,TX     ,臺股期貨,999999  ,0,60000,27688,80000,65232,142413
2026/11/11,TX     ,臺股期貨,999999  ,1,50000,20000,60000,47384,142413
2026/11/11,BRF    ,布蘭特原油期貨,999999  ,0,51,40,55,50,55
'''


def twse_margin():
    stock = ['代號', '名稱', '買進', '賣出', '現金償還', '前日餘額', '今日餘額', '次一營業日限額',
             '買進', '賣出', '現券償還', '前日餘額', '今日餘額', '次一營業日限額', '資券互抵', '註記']
    return dict(stat='OK', date='20261111', tables=[
        dict(title='115年11月11日 信用交易統計', fields=['項目', '買進', '賣出', '現金(券)償還', '前日餘額', '今日餘額'],
             data=[['融資(交易單位)', '365,295', '363,238', '12,289', '9,096,064', '9,085,832'],
                   ['融券(交易單位)', '30,904', '39,835', '11,043', '211,131', '209,019'],
                   ['融資金額(仟元)', '27,383,665', '22,419,063', '485,241', '536,678,732', '541,158,093']]),
        dict(title='115年11月11日 融資融券彙總 (全部)', fields=stock,
             data=[['2330', '台積電', '1', '1', '1', '34,560', '32,523', '1', '1', '1', '1', '91', '93', '1', '0', ' '],
                   ['064019', '某權證', '1', '1', '1', '5', '6', '1', '1', '1', '1', '0', '0', '1', '0', ' ']])])


def tpex_margin():
    fields = ['代號', '名稱', '前資餘額(張)', '資買', '資賣', '現償', '資餘額', '資屬證金', '資使用率(%)', '資限額',
              '前券餘額(張)', '券賣', '券買', '券償', '券餘額', '券屬證金', '券使用率(%)', '券限額', '資券相抵(張)', '備註']
    return dict(stat='ok', date='20261111', tables=[dict(fields=fields, data=[
        ['6488', '環球晶', '4,120', '1', '1', '0', '4,000', '0', '1.0', '1', '85', '0', '0', '0', '90', '0', '0.0', '1', '0', '']],
        summary=[['', '合計(張)', '2,128,450', '1', '1', '1', '2,147,926', '', '', '', '32,757', '1', '1', '1', '36,982', '', '', '', '', ''],
                 ['', '融資金(仟元)', '169,308,825', '1', '1', '1', '171,802,429', '', '', '', '', '', '', '', '', '', '', '', '', '']])])


def lending(date='20261111', rows=True):
    fields = ['代號', '名稱', '前日餘額', '賣出', '買進', '現券', '今日餘額', '次一營業日限額',
              '前日餘額', '當日賣出', '當日還券', '當日調整', '當日餘額', '次一營業日可限額', '備註']
    data = [['2330', '台積電', '0', '0', '0', '0', '0', '1', '11,700,000', '500,000', '200,000', '0', '12,000,000', '1', '']]
    return dict(stat='OK', date=date, fields=fields, data=data if rows else [])


def daytrade():
    return dict(stat='OK', date='20261111', tables=[
        dict(title='115年11月11日 當日沖銷交易統計資訊',
             fields=['當日沖銷交易總成交股數', '當日沖銷交易總成交股數占市場比重%', '當日沖銷交易總買進成交金額',
                     '當日沖銷交易總買進成交金額占市場比重%', '當日沖銷交易總賣出成交金額', '當日沖銷交易總賣出成交金額占市場比重%'],
             data=[['2,929,068,000', '25.42%', '400,327,087,690', '45.68', '401,935,365,890', '45.86']]),
        dict(title='115年11月11日 當日沖銷交易標的及成交量值',
             fields=['證券代號', '證券名稱', '暫停現股賣出後現款買進當沖註記', '當日沖銷交易成交股數',
                     '當日沖銷交易買進成交金額', '當日沖銷交易賣出成交金額'],
             data=[['2330', '台積電', '', '7,183,000', '1', '1']])])


def twse_notice(rows=True):
    data = [[1, '059570', '某權證', '1', '跌幅達37.77%', '115.11.11', '3.00', '-----'],
            [2, '2468', '華經', '1', "週轉率為81.81%<font color='#FF0000'>﹝第十款﹞</font>。", '115.11.11', '68.30', '50.22']]
    return dict(stat='OK', fields=['編號', '證券代號', '證券名稱', '累計次數', '注意交易資訊', '日期', '收盤價', '本益比'],
                data=data if rows else [])


def tpex_punish():
    fields = ['編號', '公布日期', '證券代號', '證券名稱', '累計', '處置起訖時間', '處置原因', '處置措施', '處置內容', '收盤價', '本益比', ' ']
    link = '(../../mainboard/listed/company-detail.html?code=4542)'
    return dict(stat='ok', tables=[dict(title='上櫃處置有價證券', fields=fields, data=[
        [1, '115/11/09', '4542', '科嶠' + link, 3, '115/11/10~115/11/16', '原因', '再次處置', '內容', '1', '1', ''],
        [1, '115/10/22', '4542', '科嶠' + link, 2, '115/10/23~115/11/03', '原因', '再次處置', '內容', '1', '1', ''],
        [1, '115/11/06', '', '            ', '', '', '', '', '', '', '', ''],
        [1, '115/11/11', '8099', '大世科' + link, 1, '115/11/12~115/11/18', '原因', '第一次處置', '內容', '1', '1', ''],
        [1, '115/11/04', '24552', '全新二' + link, 1, '115/11/05~115/11/11', '原因', '第一次處置', '內容', '1', '1', ''],
        [1, '115/11/12', '3374', '精材' + link, 1, '115/11/13~115/11/19', '原因', '第一次處置', '內容', '1', '1', '']])])


def mops(rows):
    return dict(code=200, result=dict(data=[[d, t, s, n, subject, {}] for d, t, s, n, subject in rows]))


TDCC = '''資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%
20261106,2330,11,10,100,1.00
20261106,2330,12,10,100,0.50
20261106,2330,13,10,100,0.75
20261106,2330,14,10,100,1.50
20261106,2330,15,10,100,84.24
20261106,2330,16,0,0,0.00
20261106,2330,17,3259221,25930380458,100.00
20261106,912398,15,1,1,100.00
'''


class ParserTests(unittest.TestCase):
    def test_indices_skip_combined_sectors_and_unpriced_rows(self):
        payload, symbols = aux.twse_index(twse_index(), DAY)
        self.assertEqual(payload['taiex'], dict(close=44257.15, change=1545.23, change_pct=3.62))
        self.assertEqual([s['name'] for s in payload['sectors']], ['半導體類', '鋼鐵類'])  # 電子工業 is a combined index
        self.assertEqual(symbols, {})
        with self.assertRaises(aux.NotPublished):
            aux.twse_index(twse_index(date='20261110'), DAY)
        self.assertEqual(aux.twse_turnover(twse_turnover(), DAY)[0]['turnover_twd'], 876295459349.0)
        with self.assertRaises(aux.NotPublished):
            aux.twse_turnover(twse_turnover(), dt.date(2026, 11, 12))
        self.assertEqual(aux.tpex_index(tpex_index(), DAY)[0],
                         dict(close=344.11, change=11.14, change_pct=3.35, turnover_twd=201347671000.0))

    def test_futures_positions_and_changes(self):
        payload, _ = aux.taifex_futures(FUTURES, DAY)
        self.assertEqual(payload['net_oi'], dict(dealer=33, trust=101870, foreign=-104271))
        self.assertEqual(payload['net_oi_change'], dict(dealer=-52, trust=3351, foreign=-798))
        self.assertEqual(payload['net_traded']['foreign'], -779)
        with self.assertRaises(aux.NotPublished):
            aux.taifex_futures(FUTURES, dt.date(2026, 11, 12))
        ratio, _ = aux.taifex_pcr(PCR, DAY)
        self.assertEqual((ratio['oi_ratio'], ratio['volume_ratio'], ratio['oi_ratio_change']), (65.17, 107.08, -1.37))
        large, _ = aux.taifex_large(LARGE, DAY)
        self.assertEqual(large, dict(top5_net=32312, top10_net=14768, market_oi=142413, top10_specific_net=12616))

    def test_margin_lending_and_day_trading(self):
        payload, symbols = aux.twse_margin(twse_margin(), DAY)
        self.assertEqual((payload['margin_twd'], payload['margin_change_twd']), (541158093000.0, 4479361000.0))
        self.assertEqual((payload['margin_lots_change'], payload['short_lots'], payload['short_lots_change']),
                         (-10232.0, 209019.0, -2112.0))
        self.assertEqual(symbols['2330'], dict(margin=32523.0, margin_change=-2037.0, short=93.0, short_change=2.0))
        payload, symbols = aux.tpex_margin(tpex_margin(), DAY)
        self.assertEqual((payload['margin_twd'], payload['margin_lots_change'], payload['short_lots_change']),
                         (171802429000.0, 19476.0, 4225.0))
        self.assertEqual(symbols['6488'], dict(margin=4000.0, margin_change=-120.0, short=90.0, short_change=5.0))
        payload, symbols = aux.lending(lending(), DAY)
        self.assertEqual(symbols['2330'], dict(sbl=12000.0, sbl_change=300.0))  # shares -> lots
        self.assertEqual(payload, dict(sbl_lots=12000, sbl_lots_change=300))
        with self.assertRaises(aux.NotPublished):  # the evening table exists before it is filled
            aux.lending(lending(rows=False), DAY)
        payload, symbols = aux.daytrade(daytrade(), DAY)
        self.assertEqual(payload, dict(volume_share_pct=25.42, buy_value_share_pct=45.68, sell_value_share_pct=45.86))
        self.assertEqual(symbols, {'2330': dict(daytrade=7183000.0)})

    def test_attention_list_is_only_final_once_the_day_is_over(self):
        listed, _ = aux.notice(twse_notice(), DAY, False)
        self.assertEqual(listed, [dict(symbol='2468', name='華經', reason='週轉率為81.81%﹝第十款﹞。')])  # warrants are left out
        with self.assertRaises(aux.NotPublished):
            aux.notice(twse_notice(rows=False), DAY, False)
        self.assertEqual(aux.notice(twse_notice(rows=False), DAY, True)[0], [])

    def test_disposals_in_force_on_the_day(self):
        listed, _ = aux.punish(tpex_punish(), DAY)
        # Ended periods, later announcements, convertible bonds and blank rows are left out.
        self.assertEqual(listed, [dict(symbol='4542', name='科嶠', start='2026-11-10', end='2026-11-16', measure='再次處置'),
                                  dict(symbol='8099', name='大世科', start='2026-11-12', end='2026-11-18', measure='第一次處置')])
        self.assertEqual(aux.punish(dict(stat='OK', fields=[], data=[]), DAY)[0], [])

    def test_announcements_cover_the_weekend_before_a_monday(self):
        monday = dt.date(2026, 11, 9)
        self.assertEqual(aux.material_days(monday), [dt.date(2026, 11, 7), dt.date(2026, 11, 8), monday])
        self.assertEqual(aux.material_days(DAY), [DAY])
        lists = [mops([('115/11/06', '18:00:25', '2317', '鴻海', '週五盤後公告')]),
                 mops([('115/11/08', '09:00:00', '9999', '其他', '不在自選')]),
                 mops([('115/11/08', '17:30:03', '2330', '台積電', '說明\r\n媒體報導'),
                       ('115/11/09', '14:15:06', '2330', '台積電', '董事會決議'),
                       ('115/11/10', '08:00:00', '2330', '台積電', '隔日公告')])]
        payload, _ = aux.material(lists, monday, {'2330', '2317'})
        self.assertEqual(payload['count'], 2)  # the day's own list, without what came after the day
        self.assertEqual(payload['since'], '2026-11-06 18:00')
        self.assertEqual([(i['date'], i['time'], i['symbol'], i['subject']) for i in payload['items']],
                         [('2026-11-06', '18:00', '2317', '週五盤後公告'), ('2026-11-08', '17:30', '2330', '說明媒體報導'),
                          ('2026-11-09', '14:15', '2330', '董事會決議')])
        with self.assertRaises(aux.NotPublished):
            aux.material([{'message': 'blocked'}], monday, {'2330'})

    def test_revenue_exright_and_holders(self):
        rows = [{'資料年月': '11510', '公司代號': '2330', '公司名稱': '台積電', '營業收入-當月營收': '586890000',
                 '營業收入-上月比較增減(%)': '14.661', '營業收入-去年同月增減(%)': '59.712', '累計營業收入-前期比較增減(%)': '43.29'},
                {'資料年月': '11510', '公司代號': '1101', '公司名稱': '台泥', '營業收入-當月營收': '1',
                 '營業收入-上月比較增減(%)': '1', '營業收入-去年同月增減(%)': '-10', '累計營業收入-前期比較增減(%)': '1'}]
        payload, _ = aux.revenue(rows, {'2330'})
        self.assertEqual((payload['month'], payload['reported'], payload['median_yoy_pct']), ('2026-10', 2, 24.86))
        self.assertEqual(payload['items'], [dict(symbol='2330', name='台積電', month='2026-10', revenue_100m_twd=5868.9,
                                                 mom_pct=14.66, yoy_pct=59.71, ytd_yoy_pct=43.29)])
        rows = [dict(Date='1151113', Code='00401A', Name='主動ETF', Exdividend='息', CashDividend=''),
                dict(Date='1151116', Code='2330', Name='台積電', Exdividend='除息', CashDividend='6.0'),
                dict(Date='1151111', Code='1101', Name='今天', Exdividend='息', CashDividend='1'),
                dict(Date='1151125', Code='1102', Name='太遠', Exdividend='息', CashDividend='1')]
        payload, _ = aux.exright(rows, DAY, {'2330'})
        self.assertEqual((payload['count'], payload['next_date'], [x['symbol'] for x in payload['next']]),
                         (2, '2026-11-13', ['00401A']))
        self.assertEqual(payload['watchlist'], [dict(symbol='2330', name='台積電', date='2026-11-16', kind='息', cash=6.0)])
        payload, symbols = aux.tdcc(TDCC, DAY)
        self.assertEqual(payload, dict(date='2026-11-06'))
        self.assertEqual(symbols, {'2330': dict(big400_pct=86.99, big1000_pct=84.24, holders=3259221)})
        with self.assertRaises(aux.Unavailable):  # the newest week must not describe an earlier day
            aux.tdcc(TDCC, dt.date(2026, 11, 5))


class FetchTests(unittest.TestCase):
    def responses(self):
        return {'MI_INDEX': twse_index(), 'FMTQIK': twse_turnover(), 'tradingIndex': tpex_index(),
                'MI_MARGN': twse_margin(), 'margin/balance': tpex_margin(), 'TWT93U': lending(rows=False),
                'margin/sbl': lending(), 'TWTB4U': daytrade(), 'intraday/stat': dict(stat='ok', date='20261111', tables=[]),
                'announcement/notice': twse_notice(), 'announcement/punish': dict(stat='OK', fields=[], data=[]),
                'bulletin/attention': dict(stat='ok', tables=[dict(fields=['證券代號'], data=[])]),
                'bulletin/disposal': tpex_punish(), 't187ap05_L': [], 'mopsfin_t187ap05_O': [],
                'TWT48U_ALL': [], 'tpex_exright_prepost': []}

    def fetch(self, *, today=DAY, broken=(), **overrides):
        responses, self.asked = {**self.responses(), **overrides}, []

        def get(url):
            self.asked.append(url)
            for part, data in responses.items():
                if part in url:
                    if part in broken:
                        raise RemoteError('example.test')
                    return data
            raise AssertionError(url)

        def post(url, form):
            self.asked.append(url)
            return {'futContractsDateDown': FUTURES, 'pcRatioDown': PCR, 'largeTraderFutDown': LARGE}[url.rsplit('/', 1)[1]]
        return aux.fetch_auxiliary(DAY.isoformat(), ['2330', '6488'], get=get, post=post, text=lambda url: TDCC,
                                   post_json=lambda url, payload: mops([]), today=today)

    def test_each_source_reports_its_own_status(self):
        symbols, payloads, status = self.fetch()
        self.assertEqual({k for k, v in status.items() if v != 'ok'},
                         {'twse_sbl', 'tpex_daytrade', 'tpex_notice', 'twse_revenue', 'tpex_revenue'})
        self.assertEqual({status[k] for k in ('twse_sbl', 'tpex_daytrade', 'tpex_notice', 'twse_revenue')}, {'not_published'})
        self.assertEqual(set(status), set(aux.URLS))
        self.assertEqual(symbols['2330'], dict(margin=32523.0, margin_change=-2037.0, short=93.0, short_change=2.0,
                                               sbl=12000.0, sbl_change=300.0, daytrade=7183000.0,
                                               big400_pct=86.99, big1000_pct=84.24, holders=3259221))
        self.assertNotIn('064019', symbols)  # warrants never reach extras.csv
        self.assertEqual(payloads['twse_exright']['count'], 0)
        self.assertNotIn('twse_sbl', payloads)
        self.assertTrue(core_complete(status))
        self.assertFalse(extras_complete(status))

    def test_lists_that_describe_the_present_are_skipped_for_old_days(self):
        _, payloads, status = self.fetch(today=DAY + dt.timedelta(days=10))
        self.assertEqual({k for k, v in status.items() if v == 'unavailable'}, set(aux.AS_OF_NOW))
        self.assertFalse(any('t187ap05' in url or 'TWT48U' in url for url in self.asked))
        self.assertEqual(status['tpex_notice'], 'ok')  # an empty list is final once the day is over
        self.assertTrue(extras_complete({k: v for k, v in status.items() if k not in ('twse_sbl', 'tpex_daytrade')}))

    def test_a_host_that_is_down_is_not_waited_for_source_by_source(self):
        broken = ('MI_INDEX', 'FMTQIK', 'MI_MARGN', 'TWT93U', 'TWTB4U', 'announcement/notice', 'announcement/punish')
        _, _, status = self.fetch(broken=broken)
        self.assertEqual((status['twse_index'], status['twse_turnover']), ('failed:RemoteError', 'failed:RemoteError'))
        self.assertEqual({status[k] for k in ('twse_margin', 'twse_sbl', 'twse_daytrade', 'twse_notice', 'twse_punish')},
                         {'failed:HostDown'})
        self.assertEqual(sum('twse.com.tw/rwd' in url for url in self.asked), 2)
        self.assertEqual((status['tpex_index'], status['taifex_futures']), ('ok', 'ok'))
        self.assertFalse(core_complete(status))

    def test_time_budget_leaves_the_rest_for_the_next_run(self):
        ticks = iter(range(0, 10_000, 100))
        _, _, status = aux.fetch_auxiliary(DAY.isoformat(), [], get=lambda url: twse_index(), post=lambda u, f: FUTURES,
                                           text=lambda url: TDCC, post_json=lambda u, p: mops([]), today=DAY,
                                           clock=lambda: next(ticks), budget=250)
        self.assertEqual(status['twse_index'], 'ok')
        self.assertEqual(status['tdcc'], 'failed:TimeBudget')


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.archive, self.output = Path(self.tmp.name) / 'data', Path(self.tmp.name) / 'output'

    def collect(self, auxiliary):
        fetch = lambda market: [twse(Date='1151111')] if market == 'TWSE' else [tpex(Date='1151111')]
        extras = lambda day: ({'2330': dict(industry='半導體業', pe=20.0, foreign=1000.0, trust=0.0, dealer=0.0)}, {'all': 'ok'})
        return run('2026-11-11', self.output, ['2330', '6488'], fetch=fetch, archive_root=self.archive,
                   extras=extras, auxiliary=auxiliary)

    def evening(self, trade_date, watchlist):
        return ({'2330': dict(daytrade=600.0, big400_pct=86.99, big1000_pct=84.24, holders=3259221),
                 '7777': dict(big400_pct=10.0)},  # not among the day's quotes: not saved
                {'twse_index': aux.twse_index(twse_index(), DAY)[0], 'twse_turnover': aux.twse_turnover(twse_turnover(), DAY)[0],
                 'taifex_futures': aux.taifex_futures(FUTURES, DAY)[0], 'tdcc': dict(date='2026-11-06')},
                {'twse_index': 'ok', 'twse_turnover': 'ok', 'taifex_futures': 'ok', 'tdcc': 'ok',
                 'twse_daytrade': 'ok', 'twse_margin': 'not_published'})

    def late(self, trade_date, watchlist):
        return ({'2330': dict(margin=32523.0, margin_change=-2037.0, short=93.0, short_change=2.0)},
                {'twse_margin': aux.twse_margin(twse_margin(), DAY)[0]},
                {'twse_index': 'failed:RemoteError', 'twse_turnover': 'failed:HostDown', 'taifex_futures': 'not_published',
                 'tdcc': 'failed:RemoteError', 'twse_daytrade': 'failed:HostDown', 'twse_margin': 'ok'})

    def test_late_sources_are_added_without_losing_the_evening_ones(self):
        state = self.collect(self.evening)
        self.assertEqual((state['core_complete'], state['extras_complete']), (True, False))
        latest = json.loads((self.archive / 'latest.json').read_text())
        self.assertEqual((latest['core_complete'], latest['extras_complete']), (True, False))
        state = self.collect(self.late)
        self.assertEqual(sorted(state['extras_kept_from_saved']),
                         ['taifex_futures', 'tdcc', 'twse_daytrade', 'twse_index', 'twse_turnover'])
        self.assertTrue(state['extras_complete'])
        folder = self.archive / 'daily/2026-11-11'
        rows, status = read_saved_extras(folder)
        self.assertNotIn('7777', rows)
        self.assertEqual((rows['2330']['margin'], rows['2330']['daytrade'], rows['2330']['big400_pct'], rows['2330']['pe']),
                         (32523.0, 600.0, 86.99, 20.0))
        context = json.loads((folder / 'context.json').read_text())
        self.assertEqual(sorted(context['auxiliary']), ['taifex_futures', 'tdcc', 'twse_index', 'twse_margin', 'twse_turnover'])
        self.assertEqual(json.loads((folder / 'manifest.json').read_text())['tdcc_date'], '2026-11-06')
        insights = context['insights']
        self.assertEqual(insights['index']['taiex']['turnover_100m_twd'], 8762.95)
        self.assertEqual(insights['futures']['institutions']['net_oi']['foreign'], -104271)
        self.assertEqual(insights['margin']['market']['TWSE']['margin_change_100m_twd'], 44.79)
        self.assertEqual(insights['margin']['watchlist'][0]['margin_change_lots'], -2037)
        self.assertEqual(insights['holders']['watchlist'][0]['big400_pct'], 86.99)
        facts = (folder / 'facts.md').read_text()
        for title in ('## 大盤與類股', '## 期貨與選擇權籌碼', '## 融資融券與借券', '## 技術面', '## 大戶持股（集保）'):
            self.assertIn(title, facts)
        self.assertLess(facts.index('## 大盤與類股'), facts.index('## 市場概況'))
        head = headline(context)
        self.assertIn('加權 44,257（+3.62%）', head['title'])
        self.assertTrue(head['points'][0].startswith('大盤：加權指數 44,257.15（+1,545.23，+3.62%），成交金額 8,763 億'))
        self.assertTrue(any(p.startswith('期貨：外資臺股期貨淨空單 104,271 口（較前一交易日增加 798 口）') for p in head['points']))

    def test_demo_shows_every_section(self):
        state = run(None, self.output, ['0050', '2330', '6488'], demo=True)
        self.assertTrue(state['core_complete'])
        facts = (self.output / 'demo/latest.md').read_text()
        for title in ('大盤與類股', '期貨與選擇權籌碼', '融資融券與借券', '當沖', '注意與處置股', '自選股重大訊息', '月營收'):
            self.assertIn(f'## {title}', facts)


class PromptTests(unittest.TestCase):
    def context(self, filler=0):
        insights = dict(institutions=dict(note='x'), holders=dict(note='h' * filler), daytrade=dict(note='d' * filler),
                        technicals=dict(note='t'))
        return dict(trade_date='2026-11-11', watchlist=[], insights=insights, auxiliary=dict(raw='z' * 500))

    def test_prompt_has_the_computed_sections_but_not_the_raw_payloads(self):
        text = json.loads(context_text(self.context()))
        self.assertNotIn('auxiliary', text)
        self.assertEqual(sorted(text['insights']), ['daytrade', 'holders', 'institutions', 'technicals'])
        self.assertNotIn('omitted_for_length', text)

    def test_optional_sections_are_dropped_before_the_prompt_is_too_long(self):
        text = context_text(self.context(filler=LIMIT // 2 + 10))
        self.assertLessEqual(len(text), LIMIT)
        data = json.loads(text)
        self.assertEqual(data['omitted_for_length'], ['holders'])
        self.assertIn('daytrade', data['insights'])

    def test_an_oversized_context_does_not_fail_the_collection(self):
        context = dict(trade_date='2026-11-11', watchlist=['x' * (LIMIT + 1)], insights={})
        with self.assertRaises(ValueError):
            context_text(context)
        self.assertIn('未產生分析輸入', make_prompt(context))


if __name__ == '__main__':
    unittest.main()
