"""Build the small JSON file behind the web dashboard (docs/index.html).

One record per saved trading day, taken from that day's context.json, plus the
latest day's key points and watchlist. Days saved before a source existed
simply have no value for it; the page draws gaps.

    python -m tw_stock_digest.dashboard --repo-dir data
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import re
import sys

from .pipeline import json_bytes, write
from .report import headline, pending_groups

WATCHLIST_DAYS = 120


def path(value, *keys):
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def day_record(context):
    """The numbers the charts need for one day; None where the day has no such data."""
    insights = context.get('insights') or {}
    twse = path(context, 'markets', 'TWSE', 'equities') or {}
    flows = path(insights, 'institutions', 'today_100m_twd', 'ALL') or {}
    return dict(date=context['trade_date'],
                taiex=path(insights, 'index', 'taiex', 'close'),
                taiex_pct=path(insights, 'index', 'taiex', 'change_pct'),
                turnover=path(insights, 'index', 'taiex', 'turnover_100m_twd'),
                tpex=path(insights, 'index', 'tpex', 'close'), tpex_pct=path(insights, 'index', 'tpex', 'change_pct'),
                advancers=twse.get('advancers'), decliners=twse.get('decliners'),
                foreign=flows.get('foreign'), trust=flows.get('trust'), dealer=flows.get('dealer'),
                futures_foreign=path(insights, 'futures', 'institutions', 'net_oi', 'foreign'),
                put_call=path(insights, 'futures', 'put_call', 'oi_ratio'),
                margin=path(insights, 'margin', 'market', 'TWSE', 'margin_100m_twd'),
                above_ma20=path(insights, 'technicals', 'breadth', 'above_ma20_pct'))


def watchlist_rows(context, closes):
    """The latest day's watchlist with the figures already computed for the digest."""
    insights = context.get('insights') or {}

    def find(section, symbol):
        return next((w for w in path(insights, section, 'watchlist') or [] if w.get('symbol') == symbol), {})
    rows = []
    for q in context.get('watchlist') or []:
        s = q['symbol']
        value, flow, tech = find('valuation', s), find('institutions', s), find('technicals', s)
        rows.append(dict(symbol=s, name=q['name'], close=q['close'], change_pct=q['change_pct'],
                         pe=value.get('pe'), dividend_yield=value.get('dividend_yield'),
                         foreign_window=flow.get('foreign_window_100m_twd'), trust_window=flow.get('trust_window_100m_twd'),
                         rsi14=tech.get('rsi14'), k9=tech.get('k9'), d9=tech.get('d9'), bias20=tech.get('bias20_pct'),
                         closes=closes.get(s, [])[-WATCHLIST_DAYS:]))
    return rows


def build(archive, now=None):
    daily = Path(archive) / 'daily'
    names = sorted(p.name for p in daily.iterdir() if re.fullmatch(r'\d{4}-\d{2}-\d{2}', p.name)) if daily.is_dir() else []
    days, closes, context, manifest = [], {}, None, {}
    for name in names:
        context = json.loads((daily / name / 'context.json').read_text(encoding='utf-8'))
        days.append(day_record(context))
        for q in context.get('watchlist') or []:
            if q.get('close') is not None:
                closes.setdefault(q['symbol'], []).append([name, q['close']])
    if context is None:
        return None
    try:
        manifest = json.loads((daily / names[-1] / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    now = now or dt.datetime.now(dt.timezone.utc)
    return dict(schema_version=1, generated_at_utc=now.isoformat(timespec='seconds'), latest=names[-1],
                window_days=path(context, 'insights', 'institutions', 'window_days'),
                headline=headline(context), pending=pending_groups(manifest.get('extras') or {}),
                days=days, watchlist=watchlist_rows(context, closes))


def main(argv=None):
    parser = argparse.ArgumentParser(description='產生網頁儀表板用的 dashboard.json')
    parser.add_argument('--repo-dir', type=Path, default=Path('data'))
    args = parser.parse_args(argv)
    data = build(args.repo_dir)
    if data is None:
        print('No saved trading day; dashboard.json not written.', file=sys.stderr)
        return 0
    target = args.repo_dir / 'dashboard.json'
    # The timestamp alone is not a change worth a commit.
    if target.exists():
        old = json.loads(target.read_text(encoding='utf-8'))
        if {**old, 'generated_at_utc': None} == {**data, 'generated_at_utc': None}:
            return 0
    write(target, json_bytes(data))
    return 0


if __name__ == '__main__':
    sys.exit(main())
