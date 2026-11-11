"""Find the trading days that still need collecting, and collect each one.

A day's quotes come from an exchange's "latest snapshot" API when that
snapshot describes the day, and from the exchange's per-date report otherwise.
TWSE only refreshes its snapshot the next morning but publishes the per-date
report the same afternoon, so an evening run already has the whole day. The
same path recovers days an earlier run missed, oldest first, so their numbers
are in place before a newer day's momentum windows are computed.
"""
import datetime as dt
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from . import backfill
from .http import HttpClient, RemoteError, persistent_json
from .insights import fetch_extras
from .market import SOURCES, parse_date
from .pipeline import already_saved, json_bytes, run, saved_latest, write

TAIPEI = ZoneInfo('Asia/Taipei')
WINDOW_DAYS = 7                    # how far back a missed day is still recovered
REPORTS_READY = dt.time(14, 30)    # today's per-date reports are not asked for earlier
REPORT_NOTES = {
    'TWSE': 'TWSE quotes from the per-date report (MI_INDEX); ex-rights changes from the reference price table.',
    'TPEX': 'TPEx quotes from the per-date report; volume and turnover exclude odd lots.',
}


def current_time():
    return dt.datetime.now(TAIPEI)


def saved_dates(root):
    daily = Path(root) / 'daily'
    if not daily.is_dir():
        return set()
    return {p.name for p in daily.iterdir() if re.fullmatch(r'\d{4}-\d{2}-\d{2}', p.name) and p.is_dir()}


def pending_days(saved, latest, now, snapshot_days=(), *, analysis_wanted=False):
    """Days to collect now, oldest first.

    Weekdays of the last week that are not saved (today only once reports can
    exist), any day a snapshot reports that is newer than the latest saved
    one, and the latest saved day itself while it is still incomplete.
    """
    today = now.date()
    days = set()
    for offset in range(WINDOW_DAYS + 1):
        day = today - dt.timedelta(days=offset)
        if day.weekday() < 5 and not (day == today and now.time() < REPORTS_READY):
            days.add(day)
    newest = dt.date.fromisoformat(latest['trade_date']) if latest else None
    days.update(d for d in snapshot_days if d <= today and (newest is None or d >= newest))
    pending = []
    for day in sorted(days):
        iso = day.isoformat()
        if iso not in saved:
            pending.append(day)
        elif latest and latest['trade_date'] == iso and not already_saved(latest, iso, analysis_wanted):
            pending.append(day)
    return pending


HOLIDAYS = 'https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule'


def closed_days(rows):
    """Scheduled market closures from TWSE's holiday schedule.

    The schedule also lists the first and last trading days around long
    holidays; those are named 交易日 and are not closures.
    """
    return {dt.date.fromisoformat(parse_date(r['Date'])) for r in rows if '交易日' not in r['Name']}


def live_sources(http=None):
    http = http or HttpClient(timeout=60)

    def snapshot(market):
        # TPEx often cuts its ~5 MB response off mid-stream; retry that too.
        return persistent_json(http, SOURCES[market], attempts=6, pause=10.0)

    def report(market, day):
        return backfill.fetcher(day, http=http)(market)

    def holidays():
        return closed_days(persistent_json(http, HOLIDAYS))
    return snapshot, report, holidays


def collect_pending(output, watchlist, *, archive_root=None, snapshot=None, report=None, holidays=None,
                    extras=None, only=None, now=None, analysis_requested=False, configure_remote=None):
    """Collect every pending day. Returns a summary that is also written to run.json.

    ``snapshot(market)`` returns an exchange's latest-snapshot rows and
    ``report(market, day)`` its per-date rows in the same shape (raising
    ``backfill.NoData`` when the exchange has nothing for that day).
    ``holidays()`` returns scheduled closures, so past holidays are not asked
    for again on every run. ``only`` collects exactly that day, saved or not.
    """
    output = Path(output)
    now = now or current_time()
    if snapshot is None:
        snapshot, report, holidays = live_sources()
        extras = extras or fetch_extras
    snapshots, notes = {}, {}
    for market in SOURCES:
        try:
            rows = snapshot(market)
            snapshots[market] = (dt.date.fromisoformat(parse_date(rows[0]['Date'])), rows)
        except (RemoteError, ValueError, KeyError, IndexError, TypeError) as exc:
            # Not fatal: the per-date report can still supply the day.
            notes[market] = f'snapshot_unavailable:{type(exc).__name__}'
    root = archive_root if archive_root is not None else output
    latest = saved_latest(archive_root, output)
    days = [only] if only else pending_days(saved_dates(root), latest, now, [s[0] for s in snapshots.values()],
                                            analysis_wanted=analysis_requested)
    if holidays is not None and not only and any(day != now.date() for day in days):
        try:
            closed = holidays()
        except (RemoteError, ValueError, KeyError, TypeError):
            closed = set()  # ask the exchange about each day instead
        days = [day for day in days if day not in closed]
    results = []
    for day in days:
        iso, rows, used = day.isoformat(), {}, {}
        try:
            for market in SOURCES:
                held = snapshots.get(market)
                if held and held[0] == day:
                    rows[market], used[market] = held[1], SOURCES[market]
                else:
                    rows[market], used[market] = report(market, day), backfill.URLS[market].format(d=day)
        except backfill.NoData:
            results.append(dict(trade_date=iso, status='skipped',
                                reason='not_published' if day == now.date() else 'no_trading'))
            continue
        except RemoteError:
            results.append(dict(trade_date=iso, status='failed', stage='collect', error_type='RemoteError',
                                reason='source_unreachable'))
            continue
        note = ' '.join(REPORT_NOTES[m] for m in SOURCES if used[m] != SOURCES[m]) or None
        try:
            state = run(iso, output, watchlist, fetch=rows.__getitem__, archive_root=archive_root,
                        sources=used, source_note=note, extras=extras,
                        configure_remote=configure_remote, analysis_requested=analysis_requested)
        except Exception:
            # run() already recorded the stage and error type; later days still get their turn.
            state = json.loads((output / 'status.json').read_text(encoding='utf-8'))
        results.append({'trade_date': iso, **state})
    summary = dict(checked_at=now.isoformat(), latest_saved=latest['trade_date'] if latest else None,
                   snapshot_dates={m: s[0].isoformat() for m, s in snapshots.items()},
                   snapshot_notes=notes, days=results)
    write(output / 'run.json', json_bytes(summary))
    return summary


def exit_code(summary):
    """Failures inside the pipeline are errors; an unreachable source is retried by the next run."""
    return 1 if any(d['status'] == 'failed' and d.get('reason') != 'source_unreachable'
                    for d in summary['days']) else 0
