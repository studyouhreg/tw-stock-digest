# TW Stock Digest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Deliver a runnable Taiwan stock daily collection, Drive archive and optional Claude analysis project.

**Architecture:** Python CLI with separate market normalization, HTTP, Drive and analysis modules. GitHub Actions orchestrates the CLI; generated data lives outside git.

**Tech Stack:** Python 3.11+, unittest, urllib, GitHub Actions, Drive v3, Anthropic Messages API.

**Spec:** `docs/superpowers/specs/2026-10-22-tw-stock-digest-design.md`

## Global Constraints

- Cron `17 11 * * 1-5`, timezone Asia/Taipei for application dates.
- Default watchlist `0050,2330,2317,2454,6488`; configurable.
- No key required for demo or collection. Demo never contacts Drive or Claude.
- Do not call Claude for stale, inconsistent or invalid data; no automatic paid retries.
- Preserve null values, sources, timestamps and analysis token usage.
- Google OAuth `drive.file` scope; output and secrets excluded from git.

## Review Focus

- Same-date reruns must update files and reuse matching paid analysis.
- A pre-close or holiday run must not mislabel previous data as today's.
- One unavailable market must fail visibly; partial data is not a complete report.
- Invalid API responses must not become fabricated financial data or leaked secrets.
- Historical/manual runs must not move the latest report backward.

### Task 1: Collect and normalize

**Files:** `tw_stock_digest/http.py`, `market.py`, `report.py`, `tests/test_market.py`, `tests/test_http.py`.

**Interfaces:** `normalize(market, rows) -> list[dict]`; `build_context(quotes, watchlist) -> dict`; `HttpClient.request(method, url, ...) -> bytes`; `HttpClient.json(...) -> object`.

- [x] Write tests asserting ROC 1151021 → 2026-10-21, preserved 0050, null price, comma volume, correct change percentage, warrant exclusion, duplicate and mixed-date rejection.
- [x] Run `python -m unittest discover -s tests -v`; expected missing-feature failures.
- [x] Implement parsers, market/source boundaries, compact summary and bounded retry with gzip support.
- [x] Run tests; expected all pass. Commit task.

### Task 2: Drive and Claude boundaries

**Files:** `drive.py`, `analysis.py`, `tests/test_integrations.py`.

**Interfaces:** `Drive.from_env(env) -> Drive | None`; `Drive.folder(parent,name) -> str`; `Drive.put(parent,name,bytes,mime) -> str`; `Drive.get(parent,name) -> bytes | None`; `analyze(context,model,key,http) -> dict`.

- [x] Write tests for idempotent upload, duplicate ambiguity, partial credentials, stable analysis key, truncation rejection and single paid attempt.
- [x] Run tests, expected missing-feature failure.
- [x] Implement OAuth refresh via fixed endpoint, Drive file operations and analysis with input hash and usage.
- [x] Run suite; expected all pass. Commit task.

### Task 3: Runnable pipeline and setup

**Files:** `__main__.py`, `pipeline.py`, `scripts/setup_drive.py`, `.github/workflows/*.yml`, `README.md`, `docs/SETUP.md`, `tests/test_pipeline.py`.

**Interfaces:** `run(target_date, output, watchlist, demo=False, drive=None, analysis_config=None) -> dict` with dependency injection at external boundaries.

- [x] Write integration tests for no-secret demo, stale-date skip, mixed-market failure, preserved collection after analysis failure, cached analysis and latest-date ordering.
- [x] Run tests, expected missing-feature failure.
- [x] Implement CLI, output artifacts, OAuth bootstrap, CI and daily workflow; document credentials and source limits.
- [x] Run full suite, CLI demo, real collection, YAML parsing and whitespace checks; expected known correct results, with cloud credential checks explicitly pending.
- [x] Review whole project and fix important findings. All 31 tests pass; real-source snapshot replay and 8 file hashes verified.

## Deployment notes

Deployment target: `studyouhreg/tw-stock-digest`, default branch `main`. The owner installed the GitHub App and the initial README upload succeeded. Drive OAuth and the optional Anthropic API key must be configured before those integrations can be verified. Cloud test results are recorded in GitHub Actions and `docs/VERIFICATION.md`.
