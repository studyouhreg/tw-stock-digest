"""Push the digest and collection problems to the owner through GitHub issues.

GitHub notifies watchers and anyone mentioned in an issue by e-mail and in
the GitHub mobile app, so a daily issue is a push channel that needs no extra
account or secret: the workflow's own token is enough.

- Digest: one issue for the newest saved trading day, titled with the day's
  headline and mentioning the repository owner. Re-collecting the same day
  edits that issue silently; a newer day closes the older issue.
- Alert: one open issue while a trading day stays uncollected past noon of
  the following day, closed with a comment once nothing is missing.

    python -m tw_stock_digest.notify --repo-dir data --run output/run.json
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from .http import HttpClient, RemoteError
from .report import headline

API = 'https://api.github.com'
TAIPEI = ZoneInfo('Asia/Taipei')
DIGEST_LABEL, ALERT_LABEL = 'digest', 'digest-alert'
LABELS = {DIGEST_LABEL: ('1f6feb', '每日盤後摘要'), ALERT_LABEL: ('d73a4a', '資料收集問題')}
TITLE_PREFIX = '臺股盤後 '
FINISHED_ANALYSIS = ('generated', 'cached')


class Issues:
    """The few GitHub issue calls this module needs."""

    def __init__(self, repo, token, http=None):
        self.repo, self.http = repo, http or HttpClient()
        self.headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                        'X-GitHub-Api-Version': '2022-11-28'}

    def call(self, method, path, payload=None):
        return self.http.json(method, f'{API}/repos/{self.repo}{path}', payload=payload, headers=self.headers)

    def list(self, label, state='open'):
        found = self.call('GET', f'/issues?labels={label}&state={state}&per_page=30&sort=created&direction=desc')
        return [issue for issue in found if 'pull_request' not in issue]

    def create(self, title, body, label):
        color, description = LABELS[label]
        try:
            self.call('POST', '/labels', dict(name=label, color=color, description=description))
        except RemoteError as error:
            if error.status != 422:  # 422: the label already exists
                raise
        return self.call('POST', '/issues', dict(title=title, body=body, labels=[label]))

    def update(self, number, **fields):
        return self.call('PATCH', f'/issues/{number}', fields)

    def comment(self, number, body):
        return self.call('POST', f'/issues/{number}/comments', dict(body=body))


def digest_ready(latest, now):
    """Publish once flows and valuation are in, or at 07:00 the next day regardless."""
    if latest.get('extras_complete', True):
        return True
    cutoff = dt.datetime.combine(dt.date.fromisoformat(latest['trade_date']) + dt.timedelta(days=1),
                                 dt.time(7, 0), TAIPEI)
    return now >= cutoff


def digest_issue(archive, day, *, owner, file_base):
    """(title, body) for the day's digest issue, from the saved snapshot."""
    folder = Path(archive) / 'daily' / day
    context = json.loads((folder / 'context.json').read_text(encoding='utf-8'))
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    facts = (folder / 'facts.md').read_text(encoding='utf-8')
    head = headline(context)
    lines = ['**今日重點**', ''] + ['- ' + point for point in head['points']]
    links = [f'[完整摘要]({file_base}/{day}/facts.md)', f'[行情 CSV]({file_base}/{day}/quotes.csv)']
    lines += ['', '｜'.join(links) + (f'｜cc @{owner}' if owner else '')]
    if not all(v in ('ok', 'demo', 'not_requested', 'unavailable') for v in (manifest.get('extras') or {}).values()):
        lines += ['', '> 部分法人或估值資料在收集時尚未公布，相關段落可能空白。']
    if manifest.get('analysis') in FINISHED_ANALYSIS:
        analysis = (folder / 'analysis.md').read_text(encoding='utf-8')
        lines += ['', '---', '', analysis.split('\n', 1)[1].strip() if analysis.startswith('# ') else analysis.strip()]
    # The key points are already above; continue with the tables.
    marker = '\n## 市場概況'
    rest = facts[facts.index(marker):].strip() if marker in facts else facts.strip()
    lines += ['', '---', '', rest]
    return head['title'], '\n'.join(lines) + '\n'


def alert_issue(failed, run_url, owner=''):
    days = [d['trade_date'] for d in failed if d.get('trade_date')]
    title = (f"⚠️ 臺股摘要：{len(days)} 個交易日尚未存到（{'、'.join(days)}）" if days
             else '⚠️ 臺股摘要：收集程式沒有產生執行結果')
    lines = ['排程每次執行都會自動重試並補回最近 7 天內漏掉的交易日；這則 issue 會在全部補齊後自動關閉。', '',
             '| 交易日 | 階段 | 錯誤 |', '|---|---|---|']
    for d in failed:
        lines.append(f"| {d.get('trade_date') or '—'} | {d.get('stage') or '—'} | "
                     f"{d.get('reason') or d.get('error_type') or '—'} |")
    if run_url:
        lines += ['', f'最近一次執行紀錄：{run_url}']
    lines += ['', '若持續失敗：到 Actions 手動執行「Taiwan stock digest」（取消勾選 demo），或查看該次執行的 artifact。']
    if owner:
        lines += ['', f'cc @{owner}']
    return title, '\n'.join(lines) + '\n'


def overdue(day, now):
    """A trading day should be saved by noon of the next calendar day."""
    return now >= dt.datetime.combine(dt.date.fromisoformat(day) + dt.timedelta(days=1), dt.time(12, 0), TAIPEI)


def sync(issues, archive, summary, now, *, owner='', file_base='', run_url=''):
    """Bring the repository's digest and alert issues in line with the archive. Returns what was done."""
    done = []
    days = (summary or {}).get('days') or []
    latest_path = Path(archive) / 'latest.json'
    if latest_path.exists():
        latest = json.loads(latest_path.read_text(encoding='utf-8'))
        day = latest['trade_date']
        known = issues.list(DIGEST_LABEL, state='all')
        current = next((i for i in known if i['title'].startswith(TITLE_PREFIX + day)), None)
        collected_now = any(d['trade_date'] == day and d['status'] == 'complete' for d in days)
        if digest_ready(latest, now) and (collected_now or current is None):
            title, body = digest_issue(archive, day, owner=owner, file_base=file_base)
            if current is None:
                issues.create(title, body, DIGEST_LABEL)
                done.append(f'digest_created:{day}')
            else:
                issues.update(current['number'], title=title, body=body)
                done.append(f'digest_updated:{day}')
            for issue in known:
                older = issue['title'][len(TITLE_PREFIX):len(TITLE_PREFIX) + 10]
                if issue['state'] == 'open' and issue['title'].startswith(TITLE_PREFIX) and older < day:
                    issues.update(issue['number'], state='closed')
                    done.append(f'digest_closed:{older}')
    failed = [d for d in days if d['status'] == 'failed' and overdue(d['trade_date'], now)]
    if summary is None:
        failed = [dict(trade_date=None, stage='collector', error_type='no run summary')]
    alerts = issues.list(ALERT_LABEL, state='open')
    if failed:
        title, body = alert_issue(failed, run_url, owner)
        if alerts:
            issues.update(alerts[0]['number'], title=title, body=body)
            done.append('alert_updated')
        else:
            issues.create(title, body, ALERT_LABEL)
            done.append('alert_created')
    elif alerts and not any(d['status'] == 'failed' for d in days):
        for issue in alerts:
            issues.comment(issue['number'], '已恢復：最近的交易日都已存到。')
            issues.update(issue['number'], state='closed')
        done.append('alert_closed')
    return done


def main(argv=None):
    parser = argparse.ArgumentParser(description='用 GitHub issue 推播每日摘要與收集問題')
    parser.add_argument('--repo-dir', type=Path, default=Path('data'))
    parser.add_argument('--run', type=Path, default=Path('output/run.json'))
    args = parser.parse_args(argv)
    token, repo = os.getenv('GITHUB_TOKEN', '').strip(), os.getenv('GITHUB_REPOSITORY', '').strip()
    if not token or not repo:
        print('GITHUB_TOKEN and GITHUB_REPOSITORY are required; nothing sent.', file=sys.stderr)
        return 0
    server = os.getenv('GITHUB_SERVER_URL', 'https://github.com')
    branch = os.getenv('GITHUB_REF_NAME', 'main')
    run_id = os.getenv('GITHUB_RUN_ID', '')
    summary = json.loads(args.run.read_text(encoding='utf-8')) if args.run.exists() else None
    try:
        done = sync(Issues(repo, token), args.repo_dir, summary, dt.datetime.now(TAIPEI),
                    owner=os.getenv('GITHUB_REPOSITORY_OWNER', ''),
                    file_base=f'{server}/{repo}/blob/{branch}/{args.repo_dir.as_posix()}/daily',
                    run_url=f'{server}/{repo}/actions/runs/{run_id}' if run_id else '')
    except RemoteError as error:
        # The next run tries again; a notification problem must not fail the collection.
        print(f'Notification failed: {error}', file=sys.stderr)
        return 0
    print(json.dumps(dict(notifications=done), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
