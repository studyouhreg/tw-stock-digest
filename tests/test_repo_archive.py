import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from tw_stock_digest.__main__ import main
from tw_stock_digest.demo import DEMO_DATE, fetch_demo
from tw_stock_digest.market import SOURCES
from tw_stock_digest.pipeline import run
from tw_stock_digest.repo_archive import publish_snapshot, validate_archive


class RepositoryArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = self.root / 'output'
        self.archive = self.root / 'data'
        self.config = dict(model='test-model', key='test-key')

    def collect(self, **kwargs):
        options = dict(fetch=fetch_demo, archive_root=self.archive)
        options.update(kwargs)
        return run(DEMO_DATE, self.output, ['0050', '2330'], **options)

    def test_allowlisted_snapshot_hashes_and_latest(self):
        result = self.collect()
        day = self.output / 'daily' / DEMO_DATE
        (day / 'private-key.txt').write_text('must not publish')
        publish_snapshot(day, self.archive, (day / 'facts.md').read_bytes(), 'disabled')
        saved = self.archive / 'daily' / DEMO_DATE
        self.assertEqual(result['repository_archive'], 'saved')
        self.assertEqual((saved / 'quotes.csv').read_bytes(), (day / 'quotes.csv').read_bytes())
        self.assertFalse((saved / 'raw_TPEX.json').exists())
        self.assertFalse((saved / 'private-key.txt').exists())
        manifest = json.loads((saved / 'manifest.json').read_text())
        self.assertIn('raw_TPEX.json', manifest['source_files_sha256'])
        self.assertNotIn('raw_TPEX.json', manifest['files_sha256'])
        for name, digest in manifest['files_sha256'].items():
            self.assertEqual(hashlib.sha256((saved / name).read_bytes()).hexdigest(), digest)
        self.assertEqual(json.loads((self.archive / 'latest.json').read_text())['trade_date'], DEMO_DATE)
        validate_archive(self.archive)

    def test_tampered_input_cannot_replace_saved_snapshot(self):
        self.collect()
        before = {p.relative_to(self.archive): p.read_bytes() for p in self.archive.rglob('*') if p.is_file()}
        day = self.output / 'daily' / DEMO_DATE
        (day / 'quotes.csv').write_text('corrupted')
        with self.assertRaises(ValueError):
            publish_snapshot(day, self.archive, b'bad replacement', 'disabled')
        after = {p.relative_to(self.archive): p.read_bytes() for p in self.archive.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_failed_remote_work_keeps_collected_facts(self):
        for service in ('analysis', 'drive'):
            with self.subTest(service=service):
                if service == 'analysis':
                    options = dict(analysis_config=self.config, analyzer=Mock(side_effect=RuntimeError('failed')))
                else:
                    drive = Mock()
                    drive.folder.side_effect = RuntimeError('failed')
                    options = dict(drive=drive)
                with self.assertRaises(RuntimeError):
                    self.collect(**options)
                day = self.archive / 'daily' / DEMO_DATE
                self.assertTrue((day / 'facts.md').exists())
                marker = json.loads((day / 'analysis.json').read_text())
                self.assertEqual(marker['status'], 'pending' if service == 'analysis' else 'disabled')
                self.assertEqual(json.loads((day / 'manifest.json').read_text())['status'], 'collected')
                validate_archive(self.archive)

    def invoke_cli(self, env, configure):
        markets = {url: market for market, url in SOURCES.items()}
        args = ['tw_stock_digest', '--date', DEMO_DATE, '--output', str(self.output),
                '--repo-dir', str(self.archive)]
        with patch('sys.argv', args), patch.dict(os.environ, env, clear=True), \
                patch('tw_stock_digest.pipeline.HttpClient.json',
                      side_effect=lambda method, url: fetch_demo(markets[url])), \
                patch('tw_stock_digest.__main__.Drive.from_env', side_effect=configure), \
                patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO):
            return main()

    def test_cli_configuration_failures_preserve_collection(self):
        for service in ('drive', 'analysis'):
            with self.subTest(service=service):
                self.output = self.root / service / 'output'
                self.archive = self.root / service / 'data'
                env = {'ENABLE_ANALYSIS': 'true' if service == 'analysis' else 'false'}
                code = self.invoke_cli(env, RuntimeError('private OAuth failure details'))
                self.assertEqual(code, 1)
                day = self.archive / 'daily' / DEMO_DATE
                self.assertTrue((day / 'quotes.csv').exists())
                marker = json.loads((day / 'analysis.json').read_text())
                self.assertEqual(marker['status'], 'pending' if service == 'analysis' else 'disabled')
                state = json.loads((self.output / 'status.json').read_text())
                self.assertEqual(state['stage'], 'configuration')
                self.assertNotIn('private OAuth', json.dumps(state))
                validate_archive(self.archive)

    def test_cli_initializes_optional_services_after_archiving(self):
        drive = Mock(root='root')
        drive.folder.return_value = 'day'
        drive.get.return_value = None
        def configure():
            self.assertTrue((self.archive / 'daily' / DEMO_DATE / 'quotes.csv').exists())
            return drive
        self.assertEqual(self.invoke_cli({'ENABLE_ANALYSIS': 'false'}, configure), 0)
        self.assertTrue(drive.put.called)
        state = json.loads((self.output / 'status.json').read_text())
        self.assertEqual(state['archive'], 'google_drive')
        self.assertEqual(state['status'], 'complete')
        validate_archive(self.archive)

    def test_archive_cache_reused_from_fresh_runner(self):
        analyzer = Mock(return_value=dict(text='saved analysis', model='test-model', usage={}))
        self.collect(analysis_config=self.config, analyzer=analyzer)
        self.output = self.root / 'fresh-runner'
        second = self.collect(analysis_config=self.config, analyzer=analyzer)
        self.assertEqual(second['analysis'], 'cached')
        self.assertEqual(analyzer.call_count, 1)
        self.collect()
        day = self.archive / 'daily' / DEMO_DATE
        self.assertNotIn('saved analysis', (day / 'analysis.md').read_text())
        self.assertEqual(len(list(day.glob('analysis-cache-*.json'))), 1)
        validate_archive(self.archive)

    def test_demo_and_stale_runs_do_not_create_repository_data(self):
        self.collect(demo=True)
        self.assertFalse(self.archive.exists())
        tomorrow = (dt.date.fromisoformat(DEMO_DATE) + dt.timedelta(days=1)).isoformat()
        result = run(tomorrow, self.output, [], fetch=fetch_demo, archive_root=self.archive)
        self.assertEqual(result['status'], 'skipped')
        self.assertFalse(self.archive.exists())

    def test_older_run_does_not_replace_latest(self):
        newer = '2026-10-22'
        def fetch(market):
            return [{**row, 'Date': '1151022'} for row in fetch_demo(market)]
        run(newer, self.output, [], fetch=fetch, archive_root=self.archive)
        before = (self.archive / 'latest.md').read_bytes()
        self.collect()
        self.assertTrue((self.archive / 'daily' / DEMO_DATE / 'quotes.csv').exists())
        self.assertEqual((self.archive / 'latest.md').read_bytes(), before)
        self.assertEqual(json.loads((self.archive / 'latest.json').read_text())['trade_date'], newer)

    def test_archive_validator_rejects_modified_and_unexpected_files(self):
        self.collect()
        (self.archive / 'token.json').write_text('secret')
        with self.assertRaises(ValueError):
            validate_archive(self.archive)
        (self.archive / 'token.json').unlink()
        (self.archive / 'daily' / DEMO_DATE / 'facts.md').write_text('changed')
        with self.assertRaises(ValueError):
            validate_archive(self.archive)

    def test_archive_root_files_cannot_be_directories_or_symlinks(self):
        for kind in ('directory', 'symlink'):
            with self.subTest(kind=kind):
                archive = self.root / kind
                archive.mkdir()
                readme = archive / 'README.md'
                if kind == 'directory':
                    readme.mkdir()
                    (readme / 'private-key.txt').write_text('must never be committed')
                else:
                    private = self.root / 'private-key.txt'
                    private.write_text('must never be committed')
                    readme.symlink_to(private)
                with self.assertRaises(ValueError):
                    validate_archive(archive)

    def test_git_publication_only_commits_data(self):
        project = Path(__file__).resolve().parents[1]
        remote = self.root / 'remote.git'
        checkout = self.root / 'checkout'
        checkout.mkdir()
        def git(*args, cwd=checkout):
            return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout
        git('init', '--bare', str(remote))
        git('init', '-b', 'main')
        git('config', 'user.name', 'Test User')
        git('config', 'user.email', 'test@example.invalid')
        git('config', 'core.autocrlf', 'true')
        attributes = project / '.gitattributes'
        if attributes.exists():
            (checkout / '.gitattributes').write_bytes(attributes.read_bytes())
            git('add', '.gitattributes')
        (checkout / 'README.md').write_text('test')
        git('add', 'README.md')
        git('commit', '-m', 'initial')
        git('remote', 'add', 'origin', str(remote))
        git('push', '-u', 'origin', 'main')
        self.archive = checkout / 'data'
        self.collect()
        (checkout / 'private.txt').write_text('not part of this publication')
        git('add', 'private.txt')
        env = {**os.environ, 'PYTHONPATH': str(project), 'GITHUB_REF_NAME': 'main'}
        subprocess.run(['bash', str(project / 'scripts/commit_data.sh')], cwd=checkout,
                       env=env, check=True, capture_output=True, text=True)
        changed = git('diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD').splitlines()
        self.assertTrue(changed)
        self.assertTrue(all(name.startswith('data/') for name in changed))
        self.assertEqual(git('diff', '--cached', '--name-only').strip(), 'private.txt')
        self.assertEqual(git('rev-parse', 'HEAD').strip(), git('rev-parse', 'refs/heads/main', cwd=remote).strip())
        stored_csv = subprocess.check_output(['git', 'show', f'refs/heads/main:data/daily/{DEMO_DATE}/quotes.csv'], cwd=remote)
        self.assertEqual(stored_csv, (self.archive / 'daily' / DEMO_DATE / 'quotes.csv').read_bytes())
