"""Publish a small, validated, credential-free subset of each daily collection."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import tempfile

FILES = {'quotes.csv', 'context.json', 'facts.md', 'claude-prompt.md', 'analysis.json', 'analysis.md'}
RAW_FILES = {'raw_TWSE.json', 'raw_TPEX.json'}
CACHE = re.compile(r'analysis-cache-([0-9a-f]{64})\.json')


def digest(content):
    return hashlib.sha256(content).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()


def read_file(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Archive input must be a regular file')
    return path.read_bytes()


def check_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Invalid archive date')
    return dt.date.fromisoformat(value).isoformat()


def check_cache(name, content, trade_date):
    match = CACHE.fullmatch(name)
    value = json.loads(content)
    if (not match or not isinstance(value, dict) or value.get('input_sha256') != match[1]
            or value.get('status') != 'complete' or value.get('trade_date') != trade_date
            or not isinstance(value.get('text'), str) or not value['text']):
        raise ValueError('Invalid completed analysis cache')


def checked_snapshot(day, *, source=False):
    if day.is_symlink() or not day.is_dir():
        raise ValueError('Invalid snapshot directory')
    manifest = json.loads(read_file(day / 'manifest.json'))
    if not isinstance(manifest, dict):
        raise ValueError('Invalid snapshot manifest')
    trade_date = check_date(manifest.get('trade_date'))
    if (day.name != trade_date or manifest.get('demo') is not False
            or manifest.get('status') not in ('collected', 'complete')):
        raise ValueError('Snapshot identity or status mismatch')
    hashes = manifest.get('files_sha256')
    if not isinstance(hashes, dict) or not FILES.issubset(hashes):
        raise ValueError('Incomplete snapshot manifest')
    if source and set(hashes) != FILES | RAW_FILES:
        raise ValueError('Unexpected source manifest files')
    content = {}
    for name, expected in hashes.items():
        if name not in FILES and not (source and name in RAW_FILES) and not CACHE.fullmatch(name):
            raise ValueError('Unexpected snapshot path')
        content[name] = read_file(day / name)
        if digest(content[name]) != expected:
            raise ValueError('Snapshot checksum mismatch')
        if CACHE.fullmatch(name):
            check_cache(name, content[name], trade_date)
    if json.loads(content['context.json']).get('trade_date') != trade_date:
        raise ValueError('Context date mismatch')
    if not source and {p.name for p in day.iterdir()} != set(content) | {'manifest.json'}:
        raise ValueError('Unmanifested repository file')
    return manifest, content


def atomic_write(path, content):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_bytes(content)
    temporary.replace(path)


def publish_snapshot(day, archive, report, analysis):
    day, archive = Path(day), Path(archive)
    manifest, source = checked_snapshot(day, source=True)
    trade_date = manifest['trade_date']
    if archive.is_symlink() or (archive / 'daily').is_symlink():
        raise ValueError('Archive directory cannot be a symlink')
    destination = archive / 'daily' / trade_date
    content = {}
    if destination.exists():
        _, previous = checked_snapshot(destination)
        content.update({name: value for name, value in previous.items() if CACHE.fullmatch(name)})
    content.update({name: source[name] for name in FILES})
    for path in day.glob('analysis-cache-*.json'):
        if CACHE.fullmatch(path.name):
            value = read_file(path)
            check_cache(path.name, value, trade_date)
            content[path.name] = value
    archived = {**manifest, 'archive_format': 'github-normalized-v1', 'analysis': analysis,
                'source_files_sha256': manifest['files_sha256'],
                'files_sha256': {name: digest(value) for name, value in sorted(content.items())},
                'raw_storage': 'Actions artifact (14 days) and optional Google Drive; not stored in git'}
    content['manifest.json'] = encoded(archived)
    previous_latest = None
    if (archive / 'latest.json').exists():
        previous_latest = check_date(json.loads(read_file(archive / 'latest.json'))['trade_date'])
    archive.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Validate everything before replacing the date directory as a unit.
    with tempfile.TemporaryDirectory(prefix='.stock-snapshot-', dir=archive.parent) as temporary:
        staged, backup = Path(temporary) / 'new', Path(temporary) / 'previous'
        staged.mkdir()
        for name, value in content.items():
            (staged / name).write_bytes(value)
        if destination.exists():
            destination.replace(backup)
        try:
            staged.replace(destination)
        except Exception:
            if backup.exists():
                backup.replace(destination)
            raise
    if previous_latest is None or trade_date >= previous_latest:
        latest = dict(trade_date=trade_date, analysis=analysis, snapshot_status=manifest['status'],
                      demo=False, report_sha256=digest(report))
        atomic_write(archive / 'latest.md', report)
        atomic_write(archive / 'latest.json', encoded(latest))


def validate_archive(archive):
    archive = Path(archive)
    if archive.is_symlink():
        raise ValueError('Archive directory cannot be a symlink')
    if not archive.exists():
        return
    if not archive.is_dir():
        raise ValueError('Invalid archive directory')
    if {p.name for p in archive.iterdir()} - {'README.md', 'daily', 'latest.json', 'latest.md'}:
        raise ValueError('Unexpected repository archive entry')
    for entry in archive.iterdir():
        if entry.name != 'daily':
            read_file(entry)
    dates = set()
    daily = archive / 'daily'
    if daily.is_symlink():
        raise ValueError('Daily directory cannot be a symlink')
    if daily.exists():
        if daily.is_symlink() or not daily.is_dir():
            raise ValueError('Invalid daily directory')
        for day in daily.iterdir():
            manifest, _ = checked_snapshot(day)
            dates.add(manifest['trade_date'])
    if dates:
        latest = json.loads(read_file(archive / 'latest.json'))
        if (check_date(latest['trade_date']) != max(dates) or latest.get('demo') is not False
                or latest.get('report_sha256') != digest(read_file(archive / 'latest.md'))):
            raise ValueError('Latest report does not match the archive')
    elif (archive / 'latest.json').exists() or (archive / 'latest.md').exists():
        raise ValueError('Latest report has no dated snapshot')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Validate repository data before a git commit')
    parser.add_argument('--check', type=Path, required=True)
    validate_archive(parser.parse_args().check)
