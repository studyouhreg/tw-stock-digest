import datetime as dt
import json
from pathlib import Path
import re
import tempfile
import unittest

from test_market import tpex, twse
from tw_stock_digest import dashboard
from tw_stock_digest.pipeline import run
from tw_stock_digest.repo_archive import validate_archive

NOW = dt.datetime(2026, 11, 12, 12, 0, tzinfo=dt.timezone.utc)


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.archive, self.output = Path(self.tmp.name) / 'data', Path(self.tmp.name) / 'output'

    def collect(self, roc_date, close, auxiliary=None):
        fetch = lambda market: ([twse(Date=roc_date, ClosingPrice=str(close), HighestPrice=str(close + 1))]
                                if market == 'TWSE' else [tpex(Date=roc_date)])
        extras = lambda day: ({'2330': dict(industry='半導體業', pe=20.0, foreign=1_000_000.0, trust=0.0, dealer=0.0)},
                              {'all': 'ok'})
        run(None, self.output, ['2330'], fetch=fetch, archive_root=self.archive, extras=extras, auxiliary=auxiliary)

    def auxiliary(self, trade_date, watchlist):
        return ({}, {'twse_index': dict(taiex=dict(close=20000.0, change=100.0, change_pct=0.5), sectors=[]),
                     'twse_turnover': dict(turnover_twd=3.5e11),
                     'taifex_futures': dict(net_oi=dict(foreign=-30000), net_oi_change=None, net_traded={})},
                {'twse_index': 'ok', 'twse_turnover': 'ok', 'taifex_futures': 'ok', 'twse_margin': 'not_published'})

    def test_one_record_per_day_with_gaps_where_a_day_has_no_such_data(self):
        self.collect('1151110', 100)
        self.collect('1151111', 101, self.auxiliary)
        data = dashboard.build(self.archive, NOW)
        self.assertEqual((data['latest'], data['generated_at_utc']), ('2026-11-11', '2026-11-12T12:00:00+00:00'))
        first, last = data['days']
        self.assertEqual((first['date'], first['taiex'], first['futures_foreign'], first['advancers']),
                         ('2026-11-10', None, None, 1))
        self.assertEqual(first['foreign'], 1.0)  # 1,000,000 shares x 100 = 1 億
        self.assertEqual((last['taiex'], last['taiex_pct'], last['turnover'], last['futures_foreign'], last['margin']),
                         (20000.0, 0.5, 3500.0, -30000, None))
        self.assertEqual(data['pending'], ['融資融券'])
        self.assertIn('加權 20,000（+0.50%）', data['headline']['title'])
        (stock,) = data['watchlist']
        self.assertEqual((stock['symbol'], stock['close'], stock['pe'], stock['closes']),
                         ('2330', 101.0, 20.0, [['2026-11-10', 100.0], ['2026-11-11', 101.0]]))

    def test_file_is_only_rewritten_when_its_content_changes_and_passes_validation(self):
        self.assertEqual(dashboard.main(['--repo-dir', str(self.archive)]), 0)  # nothing saved yet
        self.assertFalse((self.archive / 'dashboard.json').exists())
        self.collect('1151110', 100)
        dashboard.main(['--repo-dir', str(self.archive)])
        target = self.archive / 'dashboard.json'
        first = target.read_bytes()
        dashboard.main(['--repo-dir', str(self.archive)])
        self.assertEqual(target.read_bytes(), first)
        validate_archive(self.archive)
        self.collect('1151111', 101)
        with self.assertRaises(ValueError):  # stale dashboard data must not be committed
            validate_archive(self.archive)
        dashboard.main(['--repo-dir', str(self.archive)])
        validate_archive(self.archive)
        self.assertEqual(json.loads(target.read_text())['latest'], '2026-11-11')

    def test_page_reads_every_field_the_builder_writes(self):
        page = (Path(__file__).parent.parent / 'docs/index.html').read_text(encoding='utf-8')
        self.collect('1151110', 100)
        data = dashboard.build(self.archive, NOW)
        used = set(re.findall(r"key: '(\w+)'", page)) | set(re.findall(r"\['([a-z_0-9]+)', '[^']+'(?:, \d)?\]", page))
        self.assertIn('futures_foreign', used)
        self.assertEqual(used - set(data['days'][0]), set())
        for name in ('headline', 'pending', 'watchlist', 'window_days', 'generated_at_utc', 'latest'):
            self.assertIn('data.' + name, page)
        self.assertNotIn('innerHTML', page)  # names and subjects come from outside: text only


if __name__ == '__main__':
    unittest.main()
