import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from test_daily import at, roc
from test_market import tpex, twse
from tw_stock_digest.daily import collect_pending
from tw_stock_digest.notify import ALERT_LABEL, DIGEST_LABEL, sync


class FakeIssues:
    def __init__(self):
        self.items, self.comments = [], []

    def list(self, label, state='open'):
        return [dict(i) for i in reversed(self.items)
                if i['label'] == label and (state == 'all' or i['state'] == state)]

    def create(self, title, body, label):
        self.items.append(dict(number=len(self.items) + 1, title=title, body=body, label=label, state='open'))

    def update(self, number, **fields):
        self.items[number - 1].update(fields)

    def comment(self, number, body):
        self.comments.append((number, body))


class NotifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.archive = self.root / 'data'
        self.issues = FakeIssues()

    def collect(self, day, *, flows=True):
        def snapshot(market):
            return [twse(Date=roc(day))] if market == 'TWSE' else [tpex(Date=roc(day))]

        def extras(trade_date):
            if not flows:
                return {}, {'twse_flows': 'not_published'}
            return {'2330': dict(industry='半導體業', pe=20.0, foreign=1000.0, trust=-500.0, dealer=0.0)}, {'all': 'ok'}
        return collect_pending(self.root / 'output', ['2330'], archive_root=self.archive, only=day,
                               snapshot=snapshot, report=None, extras=extras)

    def sync(self, summary, now):
        return sync(self.issues, self.archive, summary, at(now), owner='octocat',
                    file_base='https://github.com/o/r/blob/main/data/daily', run_url='https://github.com/o/r/actions/runs/1')

    def digests(self, state='open'):
        return self.issues.list(DIGEST_LABEL, state)

    def test_new_day_creates_one_issue_and_recollection_edits_it(self):
        day = dt.date(2026, 10, 21)
        self.assertEqual(self.sync(self.collect(day), '2026-10-21T19:30'), ['digest_created:2026-10-21'])
        (issue,) = self.digests()
        self.assertTrue(issue['title'].startswith('臺股盤後 2026-10-21（三）｜上市 1 漲 0 跌｜外資 '))
        for text in ('**今日重點**', 'cc @octocat', '/data/daily/2026-10-21/facts.md', '## 市場概況', '## 法人資金流向'):
            self.assertIn(text, issue['body'])
        self.assertEqual(issue['body'].count('今日重點'), 1)
        self.assertEqual(self.sync(self.collect(day), '2026-10-21T21:30'), ['digest_updated:2026-10-21'])
        self.assertEqual(len(self.digests('all')), 1)
        # A run that collected nothing leaves the issue alone.
        self.assertEqual(self.sync(dict(days=[]), '2026-10-21T23:30'), [])

    def test_newer_day_closes_the_previous_issue(self):
        self.sync(self.collect(dt.date(2026, 10, 21)), '2026-10-21T19:30')
        done = self.sync(self.collect(dt.date(2026, 10, 22)), '2026-10-22T19:30')
        self.assertEqual(done, ['digest_created:2026-10-22', 'digest_closed:2026-10-21'])
        self.assertEqual([i['title'][5:15] for i in self.digests()], ['2026-10-22'])

    def test_waits_for_flows_until_the_next_morning(self):
        day = dt.date(2026, 10, 21)
        summary = self.collect(day, flows=False)
        self.assertEqual(self.sync(summary, '2026-10-21T18:00'), [])
        self.assertEqual(self.sync(self.collect(day, flows=False), '2026-10-22T07:05'), ['digest_created:2026-10-21'])
        self.assertIn('尚未公布', self.digests()[0]['body'])
        self.assertEqual(self.sync(self.collect(day), '2026-10-22T08:20'), ['digest_updated:2026-10-21'])
        self.assertNotIn('尚未公布', self.digests()[0]['body'])

    def test_existing_archive_without_an_issue_gets_one(self):
        self.collect(dt.date(2026, 10, 21))
        self.assertEqual(self.sync(dict(days=[]), '2026-10-22T08:00'), ['digest_created:2026-10-21'])

    def test_alert_opens_once_updates_and_closes_on_recovery(self):
        self.collect(dt.date(2026, 10, 20))
        failed = dict(days=[dict(trade_date='2026-10-21', status='failed', stage='collect',
                                 error_type='RemoteError', reason='source_unreachable')])
        # Not overdue yet: the evening run may still succeed.
        self.assertEqual(self.sync(failed, '2026-10-21T19:30'), ['digest_created:2026-10-20'])
        self.assertEqual(self.sync(failed, '2026-10-22T12:30'), ['alert_created'])
        self.assertEqual(self.sync(failed, '2026-10-22T14:30'), ['alert_updated'])
        (alert,) = self.issues.list(ALERT_LABEL)
        self.assertIn('2026-10-21', alert['title'])
        self.assertIn('source_unreachable', alert['body'])
        self.assertIn('cc @octocat', alert['body'])
        done = self.sync(self.collect(dt.date(2026, 10, 21)), '2026-10-22T19:30')
        self.assertIn('alert_closed', done)
        self.assertEqual(self.issues.list(ALERT_LABEL), [])
        self.assertEqual(self.issues.comments, [(alert['number'], '已恢復：最近的交易日都已存到。')])

    def test_missing_run_summary_raises_an_alert(self):
        self.assertEqual(sync(self.issues, self.archive, None, at('2026-10-22T08:00')), ['alert_created'])
        self.assertIn('沒有產生執行結果', self.issues.list(ALERT_LABEL)[0]['title'])

    def test_main_without_token_sends_nothing(self):
        from unittest.mock import patch
        from tw_stock_digest import notify
        with patch.dict('os.environ', {}, clear=True), patch('sys.stderr'):
            self.assertEqual(notify.main(['--repo-dir', str(self.archive), '--run', str(self.root / 'run.json')]), 0)

    def test_issue_client_requests(self):
        from tw_stock_digest.http import RemoteError
        from tw_stock_digest.notify import Issues
        calls = []

        class Http:
            def json(self, method, url, payload=None, headers=None):
                calls.append((method, url.split('/repos/o/r')[1], payload))
                if url.endswith('/labels'):
                    raise RemoteError('api.github.com', 422)
                if method == 'GET':
                    return [dict(number=1, title='t'), dict(number=2, title='pr', pull_request={})]
                return {}
        client = Issues('o/r', 'secret', Http())
        self.assertEqual([i['number'] for i in client.list('digest', 'all')], [1])
        client.create('title', 'body', 'digest')
        client.update(1, state='closed')
        client.comment(1, 'note')
        self.assertEqual([c[:2] for c in calls], [
            ('GET', '/issues?labels=digest&state=all&per_page=30&sort=created&direction=desc'),
            ('POST', '/labels'), ('POST', '/issues'), ('PATCH', '/issues/1'), ('POST', '/issues/1/comments')])
        self.assertEqual(calls[2][2], dict(title='title', body='body', labels=['digest']))
        self.assertNotIn('secret', json.dumps(calls))
