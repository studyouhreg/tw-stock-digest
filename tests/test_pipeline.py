import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_market import twse, tpex
from tw_stock_digest.pipeline import run


class MemoryDrive:
    root = 'root'
    def __init__(self):
        self.files = {}
    def folder(self, parent, name):
        return parent + '/' + name
    def put(self, parent, name, content, mime):
        self.files[(parent, name)] = content
        return parent + '/' + name
    def get(self, parent, name):
        return self.files.get((parent, name))


def fetch(market):
    return [twse()] if market == 'TWSE' else [tpex()]


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.output = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_demo_ignores_cloud_and_paid_configuration(self):
        class Forbidden:
            def __getattr__(self, name):
                raise AssertionError('Demo contacted cloud')
        result = run('2026-10-21', self.output, ['0050'], demo=True, drive=Forbidden(),
                     analysis_config={'model': 'model', 'key': 'secret'})
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['analysis'], 'disabled')
        self.assertTrue((self.output / 'demo/daily/2026-10-21/quotes.csv').exists())
        text = (self.output / 'demo/daily/2026-10-21/facts.md').read_text()
        self.assertIn('合成示範', text)

    def test_demo_cannot_overwrite_existing_live_output(self):
        run('2026-10-21', self.output, ['2330'], fetch=fetch)
        before = {p.relative_to(self.output): p.read_bytes() for p in self.output.rglob('*') if p.is_file()}
        run('2026-10-21', self.output, ['2330'], demo=True)
        for path, data in before.items():
            self.assertEqual((self.output / path).read_bytes(), data, str(path))
        self.assertTrue((self.output / 'demo/latest.md').exists())

    def test_stale_data_is_skipped_without_drive_or_claude(self):
        drive = MemoryDrive()
        result = run('2026-10-22', self.output, [], fetch=fetch, drive=drive)
        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'stale_or_nontrading_day')
        self.assertEqual(result['source_date'], '2026-10-21')
        self.assertEqual(drive.files, {})
        self.assertTrue((self.output / 'raw/TWSE.json').exists())

    def test_mismatched_market_dates_fail_visibly(self):
        def mixed(market):
            return [twse()] if market == 'TWSE' else [tpex(Date='1151020')]
        with self.assertRaises(ValueError):
            run('2026-10-21', self.output, [], fetch=mixed)
        self.assertEqual(json.loads((self.output / 'status.json').read_text())['status'], 'failed')

    def test_unavailable_market_keeps_first_market_raw(self):
        def partial(market):
            if market == 'TPEX':
                raise RuntimeError('external failure containing a secret')
            return [twse()]
        with self.assertRaises(RuntimeError):
            run('2026-10-21', self.output, [], fetch=partial)
        self.assertTrue((self.output / 'raw/TWSE.json').exists())
        self.assertNotIn('secret', (self.output / 'status.json').read_text())

    def test_partial_retry_does_not_retain_previous_run_raw(self):
        run('2026-10-21', self.output, [], fetch=fetch)
        def partial(market):
            if market == 'TPEX':
                raise RuntimeError('offline')
            return [twse(Date='1151022')]
        with self.assertRaises(RuntimeError):
            run('2026-10-22', self.output, [], fetch=partial)
        self.assertFalse((self.output / 'raw/TPEX.json').exists())
        state = json.loads((self.output / 'status.json').read_text())
        self.assertEqual(state['fetched_markets'], ['TWSE'])

    def test_analysis_failure_preserves_collected_data(self):
        drive = MemoryDrive()
        def fail(*args):
            raise RuntimeError('paid request failed')
        with self.assertRaises(RuntimeError):
            run('2026-10-21', self.output, [], fetch=fetch, drive=drive,
                 analysis_config={'model': 'model', 'key': 'key'}, analyzer=fail)
        self.assertIn(('root/2026-10-21', 'quotes.csv'), drive.files)
        self.assertNotIn(('root', 'latest.md'), drive.files)

    def test_rerun_reuses_cached_analysis_and_updates_same_files(self):
        drive = MemoryDrive()
        calls = []
        def analyzer(context, model, key):
            calls.append(context['trade_date'])
            return {'text': '測試分析', 'model': model, 'usage': {'input_tokens': 20, 'output_tokens': 5}}
        args = dict(fetch=fetch, drive=drive, analysis_config={'model': 'model', 'key': 'key'}, analyzer=analyzer)
        run('2026-10-21', self.output / 'first', [], **args)
        count = len(drive.files)
        result = run('2026-10-21', self.output / 'second', [], **args)
        self.assertEqual(calls, ['2026-10-21'])
        self.assertEqual(result['analysis'], 'cached')
        self.assertEqual(len(drive.files), count)
        self.assertIn('測試分析', drive.files[('root', 'latest.md')].decode())

    def test_changed_collection_invalidates_published_analysis_when_disabled(self):
        drive = MemoryDrive()
        def analyzer(context, model, key):
            return {'text': 'OBSOLETE_CLOSE_100', 'model': model, 'usage': {}}
        run('2026-10-21', self.output, [], fetch=fetch, drive=drive,
            analysis_config={'model': 'model', 'key': 'key'}, analyzer=analyzer)
        def changed(market):
            return [twse(ClosingPrice='99')] if market == 'TWSE' else [tpex()]
        run('2026-10-21', self.output, [], fetch=changed, drive=drive)
        local = (self.output / 'daily/2026-10-21/analysis.md').read_text()
        remote = drive.files[('root/2026-10-21', 'analysis.md')].decode()
        self.assertNotIn('OBSOLETE_CLOSE_100', local)
        self.assertNotIn('OBSOLETE_CLOSE_100', remote)
        self.assertEqual(json.loads(drive.files[('root/2026-10-21', 'analysis.json')])['status'], 'disabled')

    def test_changed_collection_failed_analysis_does_not_publish_old_analysis(self):
        drive = MemoryDrive()
        def analyzer(context, model, key):
            return {'text': 'OBSOLETE_CLOSE_100', 'model': model, 'usage': {}}
        config = {'model': 'model', 'key': 'key'}
        run('2026-10-21', self.output, [], fetch=fetch, drive=drive,
            analysis_config=config, analyzer=analyzer)
        def changed(market):
            return [twse(ClosingPrice='99')] if market == 'TWSE' else [tpex()]
        def failed(*args):
            raise RuntimeError('API unavailable')
        with self.assertRaises(RuntimeError):
            run('2026-10-21', self.output, [], fetch=changed, drive=drive,
                analysis_config=config, analyzer=failed)
        self.assertNotIn('OBSOLETE_CLOSE_100', drive.files[('root/2026-10-21', 'analysis.md')].decode())
        self.assertNotIn('OBSOLETE_CLOSE_100', (self.output / 'daily/2026-10-21/analysis.md').read_text())
        # A successful paid result remains available in its separate exact-input cache.
        result = run('2026-10-21', self.output, [], fetch=fetch, drive=drive,
                     analysis_config=config, analyzer=failed)
        self.assertEqual(result['analysis'], 'cached')

    def test_manual_older_run_does_not_roll_latest_back(self):
        drive = MemoryDrive()
        drive.files[('root', 'latest.json')] = b'{"trade_date":"2026-10-22"}'
        drive.files[('root', 'latest.md')] = b'newer-report'
        run('2026-10-21', self.output, [], fetch=fetch, drive=drive)
        self.assertEqual(drive.files[('root', 'latest.md')], b'newer-report')

    def test_cli_demo_and_invalid_date(self):
        done = subprocess.run([sys.executable, '-m', 'tw_stock_digest', '--demo', '--output', str(self.output)],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('complete', done.stdout)
        bad = subprocess.run([sys.executable, '-m', 'tw_stock_digest', '--date', '../../not-a-date'],
                             capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0)
