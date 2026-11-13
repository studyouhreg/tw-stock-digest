import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import sys

from .daily import collect_patiently, collect_pending, exit_code, live_sources
from .demo import DEMO_DATE
from .drive import Drive
from .pipeline import run
from .report import pending_groups


SKIP_MESSAGES = {
    'stale_or_nontrading_day': '來源尚無指定的預期日期，可能休市或延遲更新；',
    'already_saved': '來源最新交易日已完整保存，無新資料；',
    'older_than_saved': '來源回傳的交易日比已保存的還舊；',
    'markets_out_of_sync': '證交所與櫃買中心的最新快照不是同一個交易日（證交所隔天早上才更新），等下次排程；',
    'not_published': '今天的逐日報表還沒公布，等下次排程；',
    'no_trading': '交易所沒有這一天的資料（休市）；',
    'source_unreachable': '來源暫時連不上，下次排程會再試；',
}


def iso_date(value):
    try:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError()
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError('date must be YYYY-MM-DD') from None


def main():
    parser = argparse.ArgumentParser(description='臺股盤後收集 → GitHub／可選 Drive → 可選 Claude 摘要')
    parser.add_argument('--date', type=iso_date,
                        help='只收集（或補回）這個交易日；省略時收集所有尚未保存的交易日')
    parser.add_argument('--output', type=Path, default=Path('output'))
    parser.add_argument('--repo-dir', type=Path, help='將整理後資料保存到 git 可追蹤目錄，例如 data')
    parser.add_argument('--watchlist', default=os.getenv('WATCHLIST', '0050,2330,2317,2454,6488'))
    parser.add_argument('--demo', action='store_true', help='固定日期的合成資料；不呼叫雲端')
    parser.add_argument('--no-drive', action='store_true', help='略過 Google Drive；仍可使用 --repo-dir')
    parser.add_argument('--analyze', action='store_true', help='啟用一次付費 Claude API 呼叫或重用快取')
    parser.add_argument('--wait', action='store_true',
                        help='平日下午啟動時等到傍晚報表公布再收集（排程用；GitHub 排程啟動時間不固定）')
    args = parser.parse_args()
    watchlist = list(dict.fromkeys(s.strip().upper() for s in args.watchlist.split(',') if s.strip()))
    if len(watchlist) > 50 or any(not re.fullmatch(r'[0-9A-Z]{4,6}', s) for s in watchlist):
        parser.error('watchlist must contain at most 50 stock/ETF codes')
    # Without --date, accept the newest snapshot the source reports that is not yet saved.
    target = args.date or (DEMO_DATE if args.demo else None)
    if args.demo and target != DEMO_DATE:
        parser.error(f'demo uses synthetic data dated {DEMO_DATE}; omit --date')
    enabled = args.analyze or os.getenv('ENABLE_ANALYSIS', 'false').lower() == 'true'

    def configure_remote():
        config = None
        if enabled:
            model = os.getenv('CLAUDE_MODEL', 'claude-haiku-5-5').strip()
            key = os.getenv('ANTHROPIC_API_KEY', '').strip()
            if not key or not model:
                raise ValueError('Claude configuration missing')
            config = dict(model=model, key=key)
        drive = None if args.no_drive else Drive.from_env()
        return drive, config

    if args.demo:
        try:
            result = run(target, args.output, watchlist, demo=True)
        except Exception:
            print(f"Pipeline failed. Stage and error type are in {args.output / 'demo' / 'status.json'}.",
                  file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=False))
        return 0
    def collect():
        return collect_pending(args.output, watchlist, archive_root=args.repo_dir,
                               only=dt.date.fromisoformat(target) if target else None,
                               analysis_requested=enabled, configure_remote=configure_remote)
    summary = collect_patiently(collect, closed=lambda: live_sources()[2]()) if args.wait and not target else collect()
    print(json.dumps(summary, ensure_ascii=False))
    for day in summary['days']:
        if day['status'] == 'failed':
            print(f"{day['trade_date']} failed at stage {day.get('stage')} ({day.get('error_type')}). "
                  f"Collected files are retained in {args.output}.", file=sys.stderr)
    step_summary = os.getenv('GITHUB_STEP_SUMMARY')
    if step_summary:
        with open(step_summary, 'a', encoding='utf-8') as handle:
            handle.write(summary_markdown(summary))
    return exit_code(summary)


def summary_markdown(summary):
    lines = ['## 臺股資料工作', '', f"來源快照日期：{summary['snapshot_dates'] or '無法取得'}；"
             f"執行前最新已存：{summary['latest_saved'] or '無'}", '']
    if not summary['days']:
        lines.append('沒有待收集的交易日。')
    else:
        lines += ['| 交易日 | 結果 | 說明 |', '|---|---|---|']
        for day in summary['days']:
            if day['status'] == 'complete':
                pending = pending_groups(day.get('extras') or {})
                detail = f"{day.get('row_count')} 檔；" + (
                    '法人、估值與輔助資料完整' if not pending else '尚未齊全，下次排程補：' + '、'.join(pending))
            elif day['status'] == 'skipped':
                detail = SKIP_MESSAGES.get(day.get('reason'), '略過')
            else:
                detail = SKIP_MESSAGES.get(day.get('reason')) or f"階段 {day.get('stage')}：{day.get('error_type')}"
            lines.append(f"| {day['trade_date']} | {day['status']} | {detail} |")
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    sys.exit(main())
