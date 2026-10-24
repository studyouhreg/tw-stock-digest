import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import sys

from .demo import DEMO_DATE
from .drive import Drive
from .pipeline import run


SKIP_MESSAGES = {
    'stale_or_nontrading_day': '來源尚無指定的預期日期，可能休市或延遲更新；',
    'already_saved': '來源最新交易日已完整保存，無新資料；',
    'older_than_saved': '來源回傳的交易日比已保存的還舊；',
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
                        help='嚴格驗證來源必須是此日期；省略時收集來源最新且尚未保存的交易日（不提供歷史回補）')
    parser.add_argument('--output', type=Path, default=Path('output'))
    parser.add_argument('--repo-dir', type=Path, help='將整理後資料保存到 git 可追蹤目錄，例如 data')
    parser.add_argument('--watchlist', default=os.getenv('WATCHLIST', '0050,2330,2317,2454,6488'))
    parser.add_argument('--demo', action='store_true', help='固定日期的合成資料；不呼叫雲端')
    parser.add_argument('--no-drive', action='store_true', help='略過 Google Drive；仍可使用 --repo-dir')
    parser.add_argument('--analyze', action='store_true', help='啟用一次付費 Claude API 呼叫或重用快取')
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

    try:
        result = run(target, args.output, watchlist, demo=args.demo, archive_root=args.repo_dir,
                     configure_remote=configure_remote, analysis_requested=enabled)
    except Exception:
        status_path = args.output / 'demo' / 'status.json' if args.demo else args.output / 'status.json'
        print(f'Pipeline failed. Collected files and stage/error type are retained in {status_path}.', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    summary = os.getenv('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as handle:
            handle.write(f"## 臺股資料工作：{result['status']}\n\n```json\n{json.dumps(result, ensure_ascii=False, indent=2)}\n```\n")
            if result['status'] == 'skipped':
                handle.write('\n' + SKIP_MESSAGES.get(result.get('reason'), '本次略過。')
                             + '未執行 Claude、未更新 Drive。\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
