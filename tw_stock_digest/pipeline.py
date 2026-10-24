"""Orchestrate collection before analysis; retain evidence when a later stage fails."""
import csv
import datetime as dt
import hashlib
import io
import json
from pathlib import Path

from .analysis import analyze, analysis_key, make_prompt
from .demo import fetch_demo
from .http import HttpClient
from .market import SOURCES, normalize, build_context
from .report import factual_report


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_bytes(content)
    temporary.replace(path)


def quotes_csv(quotes):
    columns = ['trade_date', 'market', 'symbol', 'name', 'kind', 'open', 'high', 'low',
               'close', 'change', 'change_pct', 'volume_shares', 'turnover_twd', 'trades', 'quality_flags']
    text = io.StringIO(newline='')
    writer = csv.DictWriter(text, fieldnames=columns)
    writer.writeheader()
    for quote in quotes:
        row = {**quote, 'quality_flags': '|'.join(quote['quality_flags'])}
        # Protect spreadsheet users without modifying the factual JSON.
        if row['name'].startswith(('=', '+', '-', '@', '\t', '\r')):
            row['name'] = "'" + row['name']
        writer.writerow(row)
    return text.getvalue().encode('utf-8-sig')


def can_update_latest(existing, trade_date):
    if existing is None:
        return True
    previous = dt.date.fromisoformat(json.loads(existing)['trade_date'])
    return dt.date.fromisoformat(trade_date) >= previous


def run(target_date, output, watchlist, *, demo=False, drive=None,
        analysis_config=None, fetch=None, analyzer=analyze):
    target_date = dt.date.fromisoformat(target_date).isoformat()
    output = Path(output)
    if demo:
        output = output / 'demo'
        drive, analysis_config, fetch = None, None, fetch_demo
    if fetch is None:
        http = HttpClient()
        fetch = lambda market: http.json('GET', SOURCES[market])
    state = dict(status='running', stage='collect', expected_date=target_date, demo=demo,
                 captured_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                 archive='google_drive' if drive else 'local_only', analysis='disabled', fetched_markets=[])
    try:
        for market in SOURCES:
            (output / 'raw' / f'{market}.json').unlink(missing_ok=True)
        quotes, raw = [], {}
        for market in SOURCES:
            data = fetch(market)
            raw[market] = json_bytes(data)
            write(output / 'raw' / f'{market}.json', raw[market])
            state['fetched_markets'].append(market)
            quotes.extend(normalize(market, data))
        context = build_context(quotes, watchlist)
        trade_date = context['trade_date']
        state['source_date'] = trade_date
        if trade_date < target_date:
            state.update(status='skipped', reason='stale_or_nontrading_day')
            return state
        if trade_date > target_date:
            raise ValueError('Latest snapshot is newer than requested date; historical backfill is unavailable')
        state['stage'] = 'write_collection'
        day = output / 'daily' / trade_date
        analysis_status = 'pending' if analysis_config else 'disabled'
        state['analysis'] = analysis_status
        # Invalidate the published view before overwriting the data it described.
        # Successful paid results live separately in exact-input cache files.
        core = {
            'analysis.json': (json_bytes(dict(status=analysis_status, trade_date=trade_date)), 'application/json'),
            'analysis.md': (f'# Claude 分析狀態 · {trade_date}\n\n本次資料尚無完成的 Claude 分析（{analysis_status}）；請以 facts.md 為準。\n'.encode(), 'text/markdown'),
        }
        core.update({f'raw_{market}.json': (data, 'application/json') for market, data in raw.items()})
        core.update({
            'quotes.csv': (quotes_csv(quotes), 'text/csv'),
            'context.json': (json_bytes(context), 'application/json'),
            'claude-prompt.md': (make_prompt(context).encode(), 'text/markdown'),
            'facts.md': (factual_report(context, demo).encode(), 'text/markdown'),
        })
        manifest = dict(schema_version=1, status='collected', trade_date=trade_date, demo=demo,
                        captured_at_utc=state['captured_at_utc'], sources=SOURCES,
                        row_count=len(quotes),
                        files_sha256={name: hashlib.sha256(data).hexdigest() for name, (data, _) in core.items()})
        core['manifest.json'] = (json_bytes(manifest), 'application/json')
        for name, (content, _) in core.items():
            write(day / name, content)
        state['row_count'] = len(quotes)
        folder = None
        if drive:
            state['stage'] = 'upload_collection'
            folder = drive.folder(drive.root, trade_date)
            for name, (content, mime) in core.items():
                drive.put(folder, name, content, mime)
        report = core['facts.md'][0]
        if analysis_config:
            state['stage'] = 'analyze'
            model, key = analysis_config['model'], analysis_config['key']
            fingerprint = analysis_key(context, model)
            cache_name = f'analysis-cache-{fingerprint}.json'
            cached = drive.get(folder, cache_name) if drive else None
            local_cache = day / cache_name
            if cached is None and local_cache.exists():
                cached = local_cache.read_bytes()
            previous = json.loads(cached) if cached is not None else {}
            if isinstance(previous, dict) and previous.get('input_sha256') == fingerprint and previous.get('text'):
                result = previous
                state['analysis'] = 'cached'
            else:
                result = analyzer(context, model, key)
                result['input_sha256'] = fingerprint
                state['analysis'] = 'generated'
            result['trade_date'] = trade_date
            result['status'] = 'complete'
            cache_content = json_bytes(result)
            write(local_cache, cache_content)
            if drive:
                state['stage'] = 'upload_analysis'
                drive.put(folder, cache_name, cache_content, 'application/json')
            analysis_text = f"# Claude 盤後分析 · {trade_date}\n\n{result['text']}\n"
            for name, content, mime in (
                ('analysis.json', json_bytes(result), 'application/json'),
                ('analysis.md', analysis_text.encode(), 'text/markdown'),
            ):
                write(day / name, content)
                manifest['files_sha256'][name] = hashlib.sha256(content).hexdigest()
                if drive:
                    state['stage'] = 'upload_analysis'
                    drive.put(folder, name, content, mime)
            state['model'] = result['model']
            state['usage'] = result.get('usage', {})
            report += ('\n---\n\n' + analysis_text).encode()
        state['stage'] = 'publish_latest'
        latest = json_bytes(dict(trade_date=trade_date, analysis=state['analysis'], demo=demo))
        local_latest = output / 'latest.json'
        if can_update_latest(local_latest.read_bytes() if local_latest.exists() else None, trade_date):
            write(output / 'latest.md', report)
            write(local_latest, latest)
        if drive and can_update_latest(drive.get(drive.root, 'latest.json'), trade_date):
            drive.put(drive.root, 'latest.md', report, 'text/markdown')
            drive.put(drive.root, 'latest.json', latest, 'application/json')
        manifest.update(status='complete', analysis=state['analysis'])
        if analysis_config:
            manifest['analysis_input_sha256'] = fingerprint
        write(day / 'manifest.json', json_bytes(manifest))
        if drive:
            drive.put(folder, 'manifest.json', json_bytes(manifest), 'application/json')
        state.update(status='complete', stage='done')
        return state
    except Exception as exc:
        state.update(status='failed', error_type=type(exc).__name__)
        raise
    finally:
        write(output / 'status.json', json_bytes(state))
