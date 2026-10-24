#!/usr/bin/env bash
set -euo pipefail

python -m tw_stock_digest.repo_archive --check data
if [[ -z "$(git status --porcelain -- data)" ]]; then
  exit 0
fi

git add -- data
git -c user.name='github-actions[bot]' \
    -c user.email='41898282+github-actions[bot]@users.noreply.github.com' \
    commit --only -m 'data: update Taiwan stock snapshots [skip ci]' -- data
git push origin "HEAD:${GITHUB_REF_NAME:-main}"
