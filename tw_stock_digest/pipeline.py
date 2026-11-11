"""Orchestrate collection before analysis; retain evidence when a later stage fails."""
import csv
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .analysis import analyze, analysis_key, make_prompt
from .demo import DEMO_DATE, demo_extras, fetch_demo
from .insights import (compute as compute_insights, extras_complete, extras_csv, fetch_extras, keep_previous,
                       load_history, read_saved_extras)
from .http import HttpClient, persistent_json
from .market import SOURCES, normalize, build_context
from .report import factual_report
from .repo_archive import publish_snapshot


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


TAIPEI = ZoneInfo('Asia/Taipei')
FINISHED_ANALYSIS = ('generated', 'cached')


def saved_latest(archive_root, output):
    """Return the most recent fully saved snapshot record, or None."""
    path = Path(archive_root) / 'latest.json' if archive_root is not None else Path(output) / 'latest.json'
    if not path.exists():
        return None
    record = json.loads(path.read_bytes())
    if not isinstance(record, dict) or (archive_root is not None and record.get('demo') is not False):
        raise ValueError('Invalid saved latest record')
    dt.date.fromisoformat(record['trade_date'])
    return record


def already_saved(record, trade_date, analysis_wanted):
    """True when a rerun for this source date has nothing left to do."""
    if record is None or record['trade_date'] != trade_date:
        return False
    # Only the repository record distinguishes a partly finished collection.
    if record.get('snapshot_status', 'complete') != 'complete':
        return False
    # Flows and valuation were not published yet when the day was saved: retry.
    if record.get('extras_complete') is False:
        return False
    return not analysis_wanted or record.get('analysis') in FINISHED_ANALYSIS


class Skip(Exception):
    """End this run without an error; the fields explain why in the status."""

    def __init__(self, reason, **fields):
        super().__init__(reason)
        self.fields = dict(reason=reason, **fields)


def collect_quotes(fetch, output, state):
    """Fetch and validate both markets, keeping each raw response as it arrives."""
    for market in SOURCES:
        (output / 'raw' / f'{market}.json').unlink(missing_ok=True)
    quotes, raw, rejected = [], {}, []
    for market in SOURCES:
        data = fetch(market)
        raw[market] = json_bytes(data)
        write(output / 'raw' / f'{market}.json', raw[market])
        state['fetched_markets'].append(market)
        quotes.extend(normalize(market, data, rejected))
    if rejected:
        state['rejected_rows'] = [list(item) for item in rejected]
    # TPEx publishes its snapshot the same evening, TWSE only the next
    # morning, so for part of each day the two describe different trading
    # days. That is not an error: skip and let a later run retry. (The
    # scheduled collector in ``daily`` avoids this by reading TWSE's per-date
    # report when its snapshot lags.)
    market_dates = {q['market']: q['trade_date'] for q in quotes}
    if len(set(market_dates.values())) > 1:
        raise Skip('markets_out_of_sync', market_dates=market_dates)
    return quotes, raw, rejected


def check_target(trade_date, target_date, archive_root, output, state, analysis_wanted):
    """Decide whether this source day should be collected now."""
    if target_date is not None:
        if trade_date < target_date:
            raise Skip('stale_or_nontrading_day')
        if trade_date > target_date:
            raise ValueError('Latest snapshot is newer than requested date; historical backfill is unavailable')
        return
    if trade_date > dt.datetime.now(TAIPEI).date().isoformat():
        raise ValueError('Source trade date is in the future')
    saved = saved_latest(archive_root, output)
    if saved is not None:
        state['saved_date'] = saved['trade_date']
    if saved is not None and trade_date < saved['trade_date']:
        raise Skip('older_than_saved')
    if already_saved(saved, trade_date, analysis_wanted):
        raise Skip('already_saved')


def enrich(context, quotes, extras, root, state, watchlist):
    """Add flows, valuation and the computed insight sections to the context."""
    trade_date = context['trade_date']
    extra_rows, extra_status = extras(trade_date)
    # A retry must not lose a source that an earlier save of this day already had.
    kept = keep_previous(extra_rows, extra_status, *read_saved_extras(Path(root) / 'daily' / trade_date),
                         {q['symbol']: q['market'] for q in quotes})
    if kept:
        state['extras_kept_from_saved'] = kept
    state['extras'] = extra_status
    state['extras_complete'] = extras_complete(extra_status)
    history = load_history(root, trade_date)
    context['insights'] = compute_insights(quotes, extra_rows, history, watchlist)
    context['limitations'][0] = (
        f'動能指標使用本專案已保存的最近 {len(history)} 個交易日；沒有新聞、財報、月營收或融資融券。')
    return extra_rows, extra_status


def snapshot_files(context, quotes, raw, extra_rows, extra_status, state, demo, sources, source_note):
    """Everything written for the day before any optional service runs."""
    trade_date, analysis_status = context['trade_date'], state['analysis']
    # Invalidate the published view before overwriting the data it described.
    # Successful paid results live separately in exact-input cache files.
    core = {
        'analysis.json': (json_bytes(dict(status=analysis_status, trade_date=trade_date)), 'application/json'),
        'analysis.md': (f'# Claude 分析狀態 · {trade_date}\n\n本次資料尚無完成的 Claude 分析（{analysis_status}）；請以 facts.md 為準。\n'.encode(), 'text/markdown'),
    }
    core.update({f'raw_{market}.json': (data, 'application/json') for market, data in raw.items()})
    core.update({
        'quotes.csv': (quotes_csv(quotes), 'text/csv'),
        'extras.csv': (extras_csv(extra_rows), 'text/csv'),
        'context.json': (json_bytes(context), 'application/json'),
        'claude-prompt.md': (make_prompt(context).encode(), 'text/markdown'),
        'facts.md': (factual_report(context, demo).encode(), 'text/markdown'),
    })
    manifest = dict(schema_version=1, status='collected', trade_date=trade_date, demo=demo,
                    captured_at_utc=state['captured_at_utc'], sources=sources or SOURCES,
                    row_count=len(quotes),
                    files_sha256={name: hashlib.sha256(data).hexdigest() for name, (data, _) in core.items()})
    manifest['extras'] = extra_status
    if source_note:
        manifest['source_note'] = source_note
    if state.get('rejected_rows'):
        manifest['rejected_rows'] = state['rejected_rows']
    core['manifest.json'] = (json_bytes(manifest), 'application/json')
    return core, manifest


def run_analysis(context, config, analyzer, day, drive, folder, archive_root, state):
    """Reuse a cached result for exactly this input, or pay for one new analysis."""
    trade_date, model, key = context['trade_date'], config['model'], config['key']
    fingerprint = analysis_key(context, model)
    cache_name = f'analysis-cache-{fingerprint}.json'
    cached = drive.get(folder, cache_name) if drive else None
    local_cache = day / cache_name
    if cached is None and local_cache.exists():
        cached = local_cache.read_bytes()
    if cached is None and archive_root is not None:
        repository_cache = Path(archive_root) / 'daily' / trade_date / cache_name
        if repository_cache.exists():
            cached = repository_cache.read_bytes()
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
    return result, fingerprint


def run(target_date, output, watchlist, *, demo=False, drive=None,
        analysis_config=None, fetch=None, analyzer=analyze, archive_root=None,
        configure_remote=None, analysis_requested=False, sources=None, source_note=None,
        extras=None):
    """Collect one snapshot.

    ``sources`` and ``source_note`` describe a non-default origin (per-date
    reports) in the manifest and context; they do not change how data is fetched.
    ``extras(trade_date) -> (by symbol, status)`` supplies institutional flows,
    valuation and industries; a source that fails leaves its section empty
    and marks the day incomplete so a later scheduled run collects it again.

    target_date=None accepts whatever trading day ``fetch`` reports, as long as
    it is newer than what is already saved. A string target_date is strict:
    the source must report exactly that day.
    """
    if target_date is not None:
        target_date = dt.date.fromisoformat(target_date).isoformat()
    output = Path(output)
    if demo:
        target_date = target_date or DEMO_DATE
        output = output / 'demo'
        drive, analysis_config, fetch, extras = None, None, fetch_demo, demo_extras
        archive_root = None
        configure_remote, analysis_requested = None, False
    if fetch is None:
        http = HttpClient()
        # TPEx often cuts its ~5 MB response off mid-stream; the truncated body
        # fails JSON parsing, which plain retries do not cover. Retry it too.
        fetch = lambda market: persistent_json(http, SOURCES[market], attempts=6, pause=10.0)
        extras = extras or fetch_extras
    # A caller-supplied fetch (tests, backfill) gets extras only if it asks for them.
    extras = extras or (lambda trade_date: ({}, {'extras': 'not_requested'}))
    root = archive_root if archive_root is not None else output
    state = dict(status='running', stage='collect', expected_date=target_date, demo=demo,
                 captured_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                 archive='google_drive' if drive else 'local_only', analysis='disabled', fetched_markets=[])
    try:
        quotes, raw, rejected = collect_quotes(fetch, output, state)
        context = build_context(quotes, watchlist)
        if sources:
            for market, info in context['markets'].items():
                info['source'] = sources[market]
        if source_note:
            context['source_note'] = source_note
        if rejected:
            context['rejected_rows'] = [list(item) for item in rejected]
            context['limitations'].append(
                '因數值無法解析或開高低收不一致而排除：' + '、'.join(f'{m} {s}' for m, s, _ in rejected))
        trade_date = context['trade_date']
        state['source_date'] = trade_date
        check_target(trade_date, target_date, archive_root, output, state,
                     bool(analysis_config or analysis_requested))
        state['stage'] = 'collect_extras'
        extra_rows, extra_status = enrich(context, quotes, extras, root, state, watchlist)
        state['stage'] = 'write_collection'
        day = output / 'daily' / trade_date
        state['analysis'] = analysis_status = 'pending' if analysis_config or analysis_requested else 'disabled'
        core, manifest = snapshot_files(context, quotes, raw, extra_rows, extra_status, state, demo,
                                        sources, source_note)
        for name, (content, _) in core.items():
            write(day / name, content)
        state['row_count'] = len(quotes)
        report = core['facts.md'][0]
        if archive_root is not None:
            state['stage'] = 'archive_collection'
            publish_snapshot(day, archive_root, report, analysis_status)
            state['repository_archive'] = 'saved'
        # OAuth refresh and optional API configuration may fail. Keep today's
        # public collection before attempting either service's initialization.
        if configure_remote is not None:
            state['stage'] = 'configuration'
            drive, analysis_config = configure_remote()
            state['archive'] = 'google_drive' if drive else 'local_only'
        folder = None
        if drive:
            state['stage'] = 'upload_collection'
            folder = drive.folder(drive.root, trade_date)
            for name, (content, mime) in core.items():
                drive.put(folder, name, content, mime)
        fingerprint = None
        if analysis_config:
            state['stage'] = 'analyze'
            result, fingerprint = run_analysis(context, analysis_config, analyzer, day, drive, folder,
                                               archive_root, state)
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
        latest = json_bytes(dict(trade_date=trade_date, analysis=state['analysis'], demo=demo,
                                 extras_complete=state['extras_complete']))
        local_latest = output / 'latest.json'
        if can_update_latest(local_latest.read_bytes() if local_latest.exists() else None, trade_date):
            write(output / 'latest.md', report)
            write(local_latest, latest)
        if drive and can_update_latest(drive.get(drive.root, 'latest.json'), trade_date):
            drive.put(drive.root, 'latest.md', report, 'text/markdown')
            drive.put(drive.root, 'latest.json', latest, 'application/json')
        manifest.update(status='complete', analysis=state['analysis'])
        if fingerprint:
            manifest['analysis_input_sha256'] = fingerprint
        write(day / 'manifest.json', json_bytes(manifest))
        if archive_root is not None:
            state['stage'] = 'archive_completed'
            publish_snapshot(day, archive_root, report, state['analysis'])
        if drive:
            drive.put(folder, 'manifest.json', json_bytes(manifest), 'application/json')
        state.update(status='complete', stage='done')
        return state
    except Skip as skip:
        state.update(status='skipped', **skip.fields)
        return state
    except Exception as exc:
        state.update(status='failed', error_type=type(exc).__name__)
        raise
    finally:
        write(output / 'status.json', json_bytes(state))
