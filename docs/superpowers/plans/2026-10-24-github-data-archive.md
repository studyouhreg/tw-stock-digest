# GitHub Data Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Save collected Taiwan stock data directly in the GitHub repository without requiring Drive.

**Architecture:** A repository export module copies an explicit allowlist from validated pipeline checkpoints. The pipeline exports before optional remote work and after completion; the daily workflow commits only data/ with GITHUB_TOKEN.

**Tech Stack:** Existing Python 3.11+ standard library, unittest, git and GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-24-github-data-archive-design.md`

## Global Constraints

- data/daily/YYYY-MM-DD and data/latest.md/json; raw JSON stays in output/artifact or optional Drive.
- Demo cannot alter the archive; older dates cannot replace latest.
- Allowlisted files only, valid hashes before publication, no forced git push.
- No added runtime dependencies; current 19:17 Taipei weekday schedule remains.

## Review Focus

- Failed Drive/Claude work must retain the collected repository snapshot.
- Partial, malformed or tampered source files must not replace existing snapshots.
- Secret/unexpected files must never enter the archive.
- A changed snapshot must invalidate previous published analysis while preserving matching-input caches.
- The workflow must commit data only, never unrelated staged code or output/demo.

### Task 1: Repository archive, workflow and first data

**Files:** create `tw_stock_digest/repo_archive.py`, `tests/test_repo_archive.py`, `scripts/commit_data.sh`, `data/README.md`; modify pipeline, CLI, daily workflow, README and SETUP.

**Interfaces:** `publish_snapshot(day, archive, report, analysis)` validates and exports one date; `validate_archive(archive)` checks stored hashes. `run(..., archive_root=None)` exports checkpoints and reads exact-input cache from the archive. CLI adds `--repo-dir`.

- [x] Write and run failing tests for allowlisting/hashes, demo and stale isolation, failed analysis preservation, cache reuse, older-latest protection and scoped git commits.
- [x] Implement the export module and pipeline/CLI integration; verify all tests pass.
- [x] Update the workflow, setup and data documentation; collect a fresh official snapshot and publish it locally.
- [x] Run the suite, YAML/whitespace checks and one independent final review; fix important findings once.
- [ ] Publish the code and first snapshot to GitHub; verify remote tree and CI, then explain the actual data coverage to the user.
