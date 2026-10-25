"""Acceptance run: validate the historical data, then backtest every strategy.

    python -m tw_stock_digest.acceptance --cache cache/history --out docs/strategies

Data checks have pass/fail thresholds. Strategy results are findings, compared
with the direction the literature (docs/strategies/research.md) leads us to expect.
"""
import argparse
import csv
import datetime as dt
import json
import os
from pathlib import Path
import statistics
import sys
import time

from . import backtest, strategies
from .history import HEADERS
from .http import HttpClient, RemoteError
from .panel import build, num, screen_snapshot
from .fundamentals import attach

FINMIND = 'https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id={}&start_date={}&end_date={}'
SAMPLE = ['2330', '2317', '2454', '1101', '2881', '3008', '0050', '0056', '6488', '5483', '8069', '3105']
EXPECTED = {
    # name: (expected sign of excess return vs equal weight, short reason)
    'momentum_12_1': (0, '臺股為動能例外，預期不顯著'),
    'intraday_momentum': (1, 'Ho 等人 2023：IMOM 為正'),
    'overnight_reversal': (1, 'Ho 等人 2023：OMOM 為負，反向應為正'),
    'high_52w': (1, '接近 52 週高點者後續較強'),
    'low_volatility': (1, '彩券型高波動股易被高估（看風險調整後）'),
    'short_term_reversal': (0, '扣成本後不確定'),
    'ma_trend': (0, 'Chen 等人 2011：扣成本後無經濟利潤'),
    'foreign_flow': (1, '外資買超有正向預測力'),
    'trust_flow': (1, '投信買超標的異常報酬為正'),
    'limit_up': (-1, '漲停隔日可能高於均衡價'),
    'close_at_high': (1, '盤中強勢延續'),
    'gap_up': (0, '隔夜報酬短期延續、長期反轉'),
    'turnover_leaders': (0, '無明確方向'),
    'revenue_yoy': (1, '未預期月營收預測報酬為正（陳柔君等 2022）'),
    'revenue_3m_yoy': (1, '同上，較平滑的版本'),
    'revenue_surprise': (1, 'SURGE 有獨立的正向解釋力'),
    'low_pe': (1, 'E/P 是臺股較佳的價值指標（Hsu 等 2015）'),
    'low_pb': (0, '淨值比效果較弱'),
    'high_dividend_yield': (1, 'D/P 是臺股較佳的價值指標'),
    'margin_surge': (-1, '散戶融資買進推高價格、之後報酬較低（Ting 等 2018）'),
    'margin_retreat': (0, '反向推論，未直接檢驗'),
    'short_interest': (1, '散戶融券壓低價格、之後報酬較高'),
    'roe': (1, '獲利能力因子在國際上普遍有效'),
    'gross_profitability': (1, '毛利資產比是最穩健的獲利因子（Hanauer、Huber 2018）'),
    'eps_growth': (1, '盈餘驚喜後價格漂移'),
}
CHECKS = []


def skip(name, detail):
    CHECKS.append(dict(name=name, passed=None, detail=detail))
    print('SKIP ' + name + ': ' + detail, flush=True)


def check(name, passed, detail):
    CHECKS.append(dict(name=name, passed=bool(passed), detail=detail))
    print(('PASS ' if passed else 'FAIL ') + name + ': ' + detail, flush=True)


# --- data checks ---------------------------------------------------------------

def check_coverage(panel):
    counts = {'TWSE': [], 'TPEX': []}
    for i in range(len(panel.dates)):
        for market in counts:
            counts[market].append(sum(1 for s in panel.symbols if panel.markets[s] == market
                                      and panel.kind(s) == 'equity' and panel.data['close'][s][i] is not None))
    # Lunar New Year closures last up to ~12 calendar days; longer gaps mean missing data.
    gaps = [(a, b) for a, b in zip(panel.dates, panel.dates[1:]) if (b - a).days > 14]
    detail = (f'{len(panel.dates)} 個交易日（{panel.dates[0]}～{panel.dates[-1]}）；每日有成交股票數 '
              f'上市 {min(counts["TWSE"])}–{max(counts["TWSE"])}、上櫃 {min(counts["TPEX"])}–{max(counts["TPEX"])}；'
              f'超過 14 天的空檔 {len(gaps)} 個（春節休市最長約 12 天）')
    check('交易日與市場覆蓋', not gaps and min(counts['TWSE']) > 500 and min(counts['TPEX']) > 300, detail)


def check_references(panel):
    total = unresolved = 0
    examples = []
    for s in panel.symbols:
        if panel.kind(s) != 'equity':
            continue
        for i, (c, ref) in enumerate(zip(panel.data['close'][s], panel.data['ref'][s])):
            if c is not None:
                total += 1
                if ref is None:
                    unresolved += 1
                    if len(examples) < 5:
                        examples.append(f'{s}@{panel.dates[i]}')
    share = unresolved / total if total else 1
    check('參考價可判定比例', share < 0.002,
          f'{total:,} 筆有成交的股票日資料中 {unresolved} 筆無參考價（{share:.3%}），例：{", ".join(examples) or "無"}')


def check_limits(panel):
    out = []
    for s in panel.symbols:
        if panel.kind(s) != 'equity':
            continue
        for i, r in enumerate(panel.data['ret'][s]):
            if r is not None and abs(r) > 0.1005:
                out.append((abs(r), s, panel.dates[i], r))
    out.sort(reverse=True)
    total = sum(1 for s in panel.symbols if panel.kind(s) == 'equity' for r in panel.data['ret'][s] if r is not None)
    share = len(out) / total if total else 0
    sample = ', '.join(f'{s}@{d} {r:+.1%}' for _, s, d, r in out[:6])
    # New listings (no limit for 5 days) and resumptions are expected exceptions.
    check('漲跌幅超過 10% 的日報酬', share < 0.001, f'{len(out)} 筆（{share:.4%}），最大：{sample or "無"}')
    return out


def check_total_return(panel):
    index = panel.indices.get('臺灣50報酬指數') or []
    pairs = [(i, index[i]) for i in range(len(index)) if index[i]]
    if len(pairs) < 100:
        check('0050 含息報酬 vs 臺灣50報酬指數', False, '報酬指數資料不足')
        return
    a, b = pairs[0][0], pairs[-1][0]
    etf = backtest.benchmark_symbol(panel, '0050', a, b)
    idx = [index[i] for i in range(a, b + 1)]
    years = (b - a) / backtest.DAYS_PER_YEAR
    etf_cagr = etf[-1] ** (1 / years) - 1
    idx_cagr = (idx[-1] / idx[0]) ** (1 / years) - 1
    er = [y / x - 1 for x, y in zip(etf, etf[1:])]
    ir = [y / x - 1 if x and y else 0.0 for x, y in zip(idx, idx[1:])]
    daily = statistics.correlation(er, ir)
    # ETF closes carry a small premium/discount to NAV that reverses within days,
    # so weekly returns measure tracking; daily correlation is reported as well.
    weekly = statistics.correlation([etf[k + 5] / etf[k] - 1 for k in range(0, len(etf) - 5, 5)],
                                    [idx[k + 5] / idx[k] - 1 for k in range(0, len(idx) - 5, 5)])
    # 0050 charges roughly 0.3-0.4% a year and holds some cash, so a small shortfall is expected.
    diff = etf_cagr - idx_cagr
    check('0050 含息報酬 vs 臺灣50報酬指數', -0.015 < diff < 0.005 and weekly > 0.98,
          f'0050 年化 {etf_cagr:.2%}、指數年化 {idx_cagr:.2%}、差 {diff:+.2%}；'
          f'週報酬相關係數 {weekly:.4f}（日報酬 {daily:.4f}）。期間含 2025 年 6 月 1 拆 4 與多次配息')


def check_finmind(panel, cache):
    """Cross-check OHLCV with FinMind for a fixed sample of stocks."""
    folder = Path(cache).parent / 'finmind'
    http = HttpClient(timeout=60)
    start, end = panel.dates[0].isoformat(), panel.dates[-1].isoformat()
    compared = mismatched = 0
    examples = []
    for symbol in SAMPLE:
        path = folder / f'{symbol}_{start}_{end}.json'
        if not path.exists():
            folder.mkdir(parents=True, exist_ok=True)
            token = os.getenv('FINMIND_TOKEN', '').strip()
            headers = {**HEADERS, **({'Authorization': f'Bearer {token}'} if token else {})}
            try:
                data = http.json('GET', FINMIND.format(symbol, start, end), headers=headers)
            except RemoteError as error:
                skip('與 FinMind 抽樣比對 OHLCV', f'FinMind 拒絕請求（{error}，未登入的免費額度已用完）；可申請免費帳號並設定環境變數 FINMIND_TOKEN 後重跑')
                return
            path.write_text(json.dumps(data))
            time.sleep(2)
        rows = json.loads(path.read_text()).get('data') or []
        for row in rows:
            i = panel.index.get(dt.date.fromisoformat(row['date']))
            if i is None or symbol not in panel.data['close']:
                continue
            ours = [panel.data[f][symbol][i] for f in ('open', 'high', 'low', 'close', 'volume')]
            theirs = [row['open'], row['max'], row['min'], row['close'], row['Trading_Volume']]
            if ours[3] is None and not theirs[4]:
                continue  # neither source has a trade
            compared += 1
            if any(o is None or abs(o - t) > 1e-6 * max(1, abs(t)) for o, t in zip(ours, theirs)):
                mismatched += 1
                if len(examples) < 5:
                    examples.append(f'{symbol}@{row["date"]} ours={ours} finmind={theirs}')
    check('與 FinMind 抽樣比對 OHLCV', compared > 1000 and mismatched / max(compared, 1) < 0.001,
          f'{len(SAMPLE)} 檔、{compared:,} 筆股票日資料，不一致 {mismatched} 筆' +
          (f'；例：{"; ".join(examples)}' if examples else ''))


STOCK_MONTH = {
    'TWSE': ('https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date={d:%Y%m}01&stockNo={s}&response=json',
             ['2330', '2317', '2454', '1101', '2881', '0050', '0056']),
    'TPEX': ('https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?code={s}&date={d:%Y}%2F{d:%m}%2F01&response=json',
             ['6488', '5483', '8069', '3105']),
}
CHECK_MONTHS = [dt.date(2023, 11, 1), dt.date(2024, 6, 1), dt.date(2024, 12, 1),
                dt.date(2025, 6, 1), dt.date(2025, 12, 1), dt.date(2026, 9, 1)]


def check_stock_month(panel, cache):
    """Cross-check the all-market daily report against the per-stock monthly report.

    They are separate official reports, so a column-mapping or parsing error in
    one shows up as a mismatch. TPEx's per-stock page reports volume in lots.
    """
    from .history import Throttle, fetch_json, save
    from .panel import num, roc_date
    folder = Path(cache).parent / 'crosscheck'
    http, throttles = HttpClient(timeout=60), {m: Throttle(2.5, ceiling=2.5) for m in STOCK_MONTH}
    compared = mismatched = 0
    examples, tpex_gaps = [], []
    for market, (template, symbols) in STOCK_MONTH.items():
        for symbol in symbols:
            for month in CHECK_MONTHS:
                path = folder / market / f'{symbol}_{month:%Y%m}.json'
                if not path.exists():
                    try:
                        save(path, fetch_json(http, template.format(d=month, s=symbol), throttles[market]))
                    except RemoteError:
                        continue
                data = json.loads(path.read_text(encoding='utf-8'))
                rows = data.get('data') if market == 'TWSE' else (data.get('tables') or [{}])[0].get('data')
                for row in rows or []:
                    i = panel.index.get(roc_date(row[0]))
                    if i is None:
                        continue
                    o, h, l, c = (num(x) for x in row[3:7])
                    volume = num(row[1])
                    ours = [panel.data[f][symbol][i] for f in ('open', 'high', 'low', 'close')]
                    if c is None and ours[3] is None:
                        continue
                    our_volume = panel.data['volume'][symbol][i] or 0
                    if market == 'TPEX':
                        our_volume = round(our_volume / 1000)
                    compared += 1
                    if market == 'TPEX' and volume and our_volume:
                        tpex_gaps.append(volume / our_volume - 1)
                    if [o, h, l, c] != ours or (market == 'TWSE' and volume != our_volume):
                        mismatched += 1
                        if len(examples) < 4:
                            examples.append(f'{symbol}@{roc_date(row[0])} 個股={[o, h, l, c, volume]} 全市場={ours + [our_volume]}')
    gap = statistics.median(tpex_gaps) if tpex_gaps else 0.0
    check('全市場日報 vs 個股月報', compared > 500 and mismatched == 0,
          f'{sum(len(v[1]) for v in STOCK_MONTH.values())} 檔 × {len(CHECK_MONTHS)} 個月、{compared:,} 筆股票日資料；'
          f'開高低收（上市另含成交股數）不一致 {mismatched} 筆' + (f'，例：{"; ".join(examples)}' if examples else '') +
          f'。上櫃個股月報的成交量含鉅額與零股，中位數多 {gap:+.1%}，屬定義差異')


def check_snapshot(panel, data_dir):
    """The live pipeline snapshot and the historical report must describe the same day.

    Prices must match exactly. Volume is compared separately: the TPEx snapshot
    API includes odd-lot shares while the historical report counts board lots.
    """
    results = []
    for path in sorted(Path(data_dir).glob('daily/*/quotes.csv')):
        day = dt.date.fromisoformat(path.parent.name)
        i = panel.index.get(day)
        if i is None:
            continue
        quotes, compared, mismatched, volume_gaps = [], 0, 0, []
        with path.open(encoding='utf-8-sig') as handle:
            for row in csv.DictReader(handle):
                q = {k: (None if row[k] == '' else row[k]) for k in row}
                for f in ('open', 'high', 'low', 'close', 'change', 'change_pct', 'turnover_twd'):
                    q[f] = float(q[f]) if q[f] is not None else None
                for f in ('volume_shares', 'trades'):
                    q[f] = int(q[f]) if q[f] is not None else None
                q['quality_flags'] = q['quality_flags'].split('|') if q['quality_flags'] else []
                quotes.append(q)
                s = q['symbol']
                if s not in panel.data['close']:
                    mismatched += 1
                    continue
                compared += 1
                if any((q[f] is None) != (panel.data[f][s][i] is None) or
                       (q[f] is not None and abs(q[f] - panel.data[f][s][i]) > 1e-6)
                       for f in ('open', 'high', 'low', 'close')):
                    mismatched += 1
                hist = panel.data['volume'][s][i]
                if q['market'] == 'TPEX' and q['volume_shares'] and hist:
                    volume_gaps.append(q['volume_shares'] / hist - 1)
        names = [n for n, c in strategies.REGISTRY.items()
                 if issubclass(c, strategies.Screen) and n != 'turnover_leaders']
        snap = screen_snapshot(quotes, names, min_turnover=0)
        view = strategies.MarketView(panel, i)
        same = all([p.symbol for p in snap[n]] == [p.symbol for p in strategies.get(n, min_turnover=0).select(view)]
                    for n in names)
        median_gap = statistics.median(volume_gaps) if volume_gaps else 0.0
        results.append((day, compared, mismatched, same, median_gap, len(names)))
    if not results:
        check('每日快照 vs 歷史報表', False, '沒有重疊日期')
        return
    ok = all(m == 0 and same for _, _, m, same, _, _ in results)
    check('每日快照 vs 歷史報表', ok, '；'.join(
        f'{d}：{c} 檔開高低收比對、{m} 檔不一致；{k} 個不依賴成交量的盤後篩選結果{"一致" if s else "不一致"}；'
        f'成交股數中位差 {g:+.2%}（上櫃快照含零股，屬定義差異）' for d, c, m, s, g, k in results))


def independent_equity(panel, picks):
    """Recompute a periodic strategy by hand from raw prices, without the engine.

    Equal weight at each rebalance, buy at the next open (skipping limit-up
    opens), sell at the open after the next decision, cost charged on the names
    that change. Continuing names are not trimmed back to equal weight.
    """
    data, dates = panel.data, sorted(picks)
    index = [panel.index[dt.date.fromisoformat(d)] for d in dates]
    equity, previous = 1.0, set()
    for k, day in enumerate(dates):
        a = index[k] + 1
        b = index[k + 1] + 1 if k + 1 < len(dates) else len(panel.dates) - 1
        chosen = [s for s in picks[day] if data['open'][s][a] and data['ref'][s][a]
                  and data['open'][s][a] / data['ref'][s][a] - 1 < 0.095]
        held = set(chosen)
        equity *= (1 - len(held - previous) / max(len(held), 1) * backtest.FEE
                   - len(previous - held) / max(len(previous), 1) * (backtest.FEE + backtest.TAX))
        rets = []
        for s in chosen:
            value = (data['close'][s][a] or data['open'][s][a]) / data['open'][s][a]
            for j in range(a + 1, b + 1):
                if j == b and k + 1 < len(dates):
                    o, ref = data['open'][s][j], data['ref'][s][j]
                    value *= o / ref if o and ref else 1.0
                else:
                    value *= 1 + (data['ret'][s][j] or 0.0)
            rets.append(value - 1)
        equity *= 1 + (sum(rets) / len(rets) if rets else 0.0)
        previous = held
    return equity


def check_engine(panel, start):
    rows = []
    for name in ('momentum_12_1', 'high_52w', 'low_volatility'):
        result = backtest.run(panel, strategies.get(name), start=start)
        rows.append((name, result.equity[-1], independent_equity(panel, result.picks)))
    worst = max(abs(e / i - 1) for _, e, i in rows)
    check('回測引擎 vs 獨立手算', worst < 0.05, '；'.join(
        f'{n}：引擎 {e:.3f}、手算 {i:.3f}' for n, e, i in rows) +
        f'（最大差 {worst:.1%}；手算不把續抱股票調回等權，故有小差距）')


def check_fundamentals(panel, cache):
    """Internal consistency of the point-in-time fundamentals."""
    facts = getattr(panel, 'facts', None)
    if facts is None or not facts.data:
        skip('基本面資料', '沒有快取的基本面資料')
        return
    store = facts.data
    # 1) Our YoY from the two published amounts must equal the published YoY %.
    compared = bad = 0
    for symbol, (_, recs) in store.get('revenue', {}).items():
        for r in recs:
            if r['last_year'] and r['last_year'] > 0 and r.get('published_yoy') is not None:
                compared += 1
                bad += abs(100 * (r['revenue'] / r['last_year'] - 1) - r['published_yoy']) > 0.011
    check('月營收：自算年增率 vs 官方年增率', compared > 10000 and bad / compared < 0.001,
          f'{compared:,} 筆、差超過 0.01 個百分點 {bad} 筆')
    # 2) Three monthly revenues should add up to the quarter's statement revenue.
    gaps = []
    for symbol, (_, recs) in store.get('statement', {}).items():
        months = {r['period']: r['revenue'] for r in store.get('revenue', {}).get(symbol, ([], []))[1]}
        for r in recs:
            (year, q), quarter = r['period'], r.get('q_revenue')
            parts = [months.get((year, m)) for m in range(3 * q - 2, 3 * q + 1)]
            if quarter and quarter > 0 and all(x is not None for x in parts):
                gaps.append(abs(sum(parts) / quarter - 1))
    if gaps:
        gaps.sort()
        median, within = gaps[len(gaps) // 2], sum(g < 0.02 for g in gaps) / len(gaps)
        check('月營收 3 個月合計 vs 季報單季營收', median < 0.01,
              f'{len(gaps):,} 個公司季度，差距中位數 {median:.2%}、差距 < 2% 的占 {within:.1%}'
              '（金融業「收益」定義與月營收不同、少數公司月營收非合併數，是主要差異來源）')
    else:
        skip('月營收 3 個月合計 vs 季報單季營收', '沒有同時具備月營收與季報的公司季度')
    # 3) Trailing EPS implied by the official P/E vs the sum of four reported quarters.
    gaps = []
    for symbol, (index, recs) in store.get('valuation', {}).items():
        statements = store.get('statement', {}).get(symbol)
        if not statements:
            continue
        for i, r in zip(index, recs):
            close = panel.data['close'].get(symbol, [None] * len(panel.dates))[i]
            if not (r['pe'] and r['pe'] > 0 and close):
                continue
            known = facts.latest('statement', symbol, i, 4)
            if len(known) == 4 and all(k['q_eps'] is not None for k in known):
                ttm = sum(k['q_eps'] for k in known)
                if ttm > 0:
                    gaps.append(abs(close / r['pe'] / ttm - 1))
    if gaps:
        gaps.sort()
        median, within = gaps[len(gaps) // 2], sum(g < 0.05 for g in gaps) / len(gaps)
        check('本益比推算 EPS vs 季報近 4 季 EPS', median < 0.05,
              f'{len(gaps):,} 筆月底資料，差距中位數 {median:.2%}、差距 < 5% 的占 {within:.1%}'
              '（證交所在公司申報當天就更新 EPS，本專案保守地等到法定期限，財報季前後會有落差）')
    else:
        skip('本益比推算 EPS vs 季報近 4 季 EPS', '沒有同時具備本益比與 4 季財報的資料')
    # 4) Per-stock TWSE margin balances add up to the published market total.
    compared = bad = 0
    for path in sorted((Path(cache) / 'twse_margin').glob('*/*.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        tables = data.get('tables') or []
        summary = next((t for t in tables if '信用交易統計' in (t.get('title') or '')), None)
        detail = next((t for t in tables if '彙總' in (t.get('title') or '')), None)
        if not summary or not detail:
            continue
        total = next((num(r[5]) for r in summary['data'] if r[0].startswith('融資(交易單位)')), None)
        parts = sum(num(r[6]) or 0 for r in detail['data'])
        compared += 1
        bad += total is None or abs(parts - total) > 0.5
    check('上市個股融資餘額合計 vs 全市場統計', compared > 50 and bad == 0,
          f'{compared} 個週末交易日、不一致 {bad} 個')


# --- strategy runs -------------------------------------------------------------

def evaluate(panel, name, start, end, bench_ew, bench_tr):
    strategy = strategies.get(name)
    began = time.time()
    gross = backtest.run(panel, strategy, start=start, end=end, costs=backtest.Costs(0, 0))
    net = backtest.run(panel, strategy, start=start, end=end)
    years = (end - start) / backtest.DAYS_PER_YEAR
    m = backtest.metrics(net.equity, bench_ew)
    m['vs_taiex_tr'] = backtest.metrics(net.equity, bench_tr)['excess_annual'] if bench_tr else None
    m['gross_cagr'] = backtest.metrics(gross.equity)['cagr']
    m['turnover_per_year'] = net.turnover / years
    m['avg_holdings'] = statistics.fmean(net.holdings[1:])
    m['skipped_buys'] = net.skipped_buys
    half = (end - start) // 2
    m['halves_excess'] = [
        backtest.metrics(net.equity[:half + 1], bench_ew[:half + 1])['excess_annual'],
        backtest.metrics(net.equity[half:], bench_ew[half:])['excess_annual'],
    ]
    last = max(net.picks) if net.picks else None
    m['latest_picks'] = {'date': last, 'symbols': [(s, panel.names.get(s, '')) for s in net.picks.get(last, [])[:10]]}
    m['seconds'] = round(time.time() - began, 1)
    expected, reason = EXPECTED.get(name, (0, ''))
    observed = 1 if m['excess_t'] > 2 else -1 if m['excess_t'] < -2 else 0
    m['expected'], m['observed'], m['reason'] = expected, observed, reason
    m['window'] = [panel.dates[start].isoformat(), panel.dates[end].isoformat()]
    m['label'], m['rebalance'] = strategy.label, strategy.rebalance
    return m


def facts_start(panel, strategy):
    """First month-end on which the strategy can fill its whole portfolio."""
    ends = sorted(backtest.rebalance_days(panel.dates, 'monthly'))
    for i in ends:
        if i >= 20 and len(strategy.select(strategies.MarketView(panel, i))) >= strategy.top_n:
            return i
    return None


def tr_index(panel, start, end):
    idx = panel.indices.get('發行量加權股價報酬指數') or []
    values = idx[start:end + 1]
    if len(values) != end - start + 1 or any(v is None for v in values):
        return None
    return [v / values[0] for v in values]


def pct(x):
    return '—' if x is None else f'{x:+.1%}'


def report(results, checks, panel, out):
    lines = ['# 策略驗收報告', '',
             f'產生時間：{dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC；資料期間 {panel.dates[0]}～{panel.dates[-1]}。'
             '由 `python -m tw_stock_digest.acceptance` 產生，請勿手動編輯。', '',
             '> 研究用途，不構成投資建議。回測期間短（約兩年），結果受市場狀態影響很大，見下方限制。', '',
             '## 一、資料驗收', '', '| 檢查 | 結果 | 說明 |', '|---|---|---|']
    for c in checks:
        mark = '⏸ 未執行' if c['passed'] is None else '✅ 通過' if c['passed'] else '❌ 未通過'
        lines.append(f"| {c['name']} | {mark} | {c['detail']} |")
    lines += ['', '## 二、策略回測', '',
              '扣除手續費 0.1425%（買賣各一次）與證交稅 0.3%；訊號用當日收盤、隔日開盤成交；每期等權 20 檔。'
              '超額報酬以同範圍、同期間的等權組合為基準，t 值未做自我相關調整，|t| > 2 才視為有方向。'
              '價格策略需先累積一年歷史，基本面策略從資料足以選滿 20 檔的第一個月底開始，所以期間不同；'
              '各期間的基準報酬列在下方。', '',
              '| 策略 | 換股 | 年化報酬 | 未扣成本 | 年化波動 | Sharpe | 最大回撤 | 超額（等權） | t 值 | 超額（加權報酬指數） | 前半／後半超額 | 年週轉 | 文獻預期 | 觀察 |',
              '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---|---|']
    sign = {1: '正', -1: '負', 0: '不顯著'}
    for name, m in results.items():
        if 'error' in m:
            lines.append(f"| {name} | — | 無法執行：{m['error']} |" + ' |' * 11)
            continue
        agree = '✔' if m['expected'] == m['observed'] else '✘'
        lines.append(
            f"| {m['label']}<br>`{name}`<br>{m['window'][0]} 起 | {m['rebalance']} | {pct(m['cagr'])} | {pct(m['gross_cagr'])} | "
            f"{m['volatility']:.1%} | {m['sharpe']:.2f} | {m['max_drawdown']:.1%} | {pct(m['excess_annual'])} | "
            f"{m['excess_t']:.2f} | {pct(m['vs_taiex_tr'])} | {pct(m['halves_excess'][0])}／{pct(m['halves_excess'][1])} | "
            f"{m['turnover_per_year']:.1f}× | {sign[m['expected']]} | {sign[m['observed']]} {agree} |")
    lines += ['', '## 三、限制', '',
              '- **期間短且偏多頭**：策略回測約兩年（法人策略約 20 個月），期間大型權值股主導漲勢，'
              '加權報酬指數年化遠高於等權股票，任何從 20 檔等權中小型股出發的策略都很難勝過它。結論不能外推到空頭或盤整市況。',
              '- **統計檢定力低**：兩年資料的 t 值門檻很難達到，「不顯著」多半代表證據不足，不代表策略無效。',
              '- **日換股策略的超額報酬**是每日超額報酬的算術平均 × 250，成本極高時會出現低於 −100% 的數字，請以年化報酬比較。',
              '- **成本以未打折手續費計算**：實際券商折扣會讓高週轉策略（短期反轉、盤後篩選）表現好一些，但不足以翻正。',
              '- **未模擬**：分盤交易、處置股、借券放空（全部策略只做多）、股利入帳延遲、滑價與市場衝擊。',
              '- **參數未最佳化**：照文獻常用值固定，避免資料探勘；同一組結果不應再拿來挑參數。']
    out.mkdir(parents=True, exist_ok=True)
    (out / 'acceptance.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description='歷史資料驗收與策略回測')
    parser.add_argument('--cache', type=Path, default=Path('cache/history'))
    parser.add_argument('--data', type=Path, default=Path('data'))
    parser.add_argument('--out', type=Path, default=Path('docs/strategies'))
    parser.add_argument('--strategies', default=','.join(strategies.REGISTRY))
    parser.add_argument('--no-finmind', action='store_true')
    args = parser.parse_args(argv)
    panel = build(args.cache, log=print)
    attach(panel, args.cache, log=print)
    check_coverage(panel)
    check_references(panel)
    check_limits(panel)
    check_total_return(panel)
    check_stock_month(panel, args.cache)
    if not args.no_finmind:
        check_finmind(panel, args.cache)
    check_snapshot(panel, args.data)
    check_fundamentals(panel, args.cache)

    end = len(panel.dates) - 1
    price_start = 250  # one year of history before the first decision
    check_engine(panel, price_start)
    flow_days = [i for i, ok in enumerate(panel.flows_available) if ok]
    flow_start = flow_days[0] + 20 if flow_days else None
    windows = {}
    results = {}
    for name in [n.strip() for n in args.strategies.split(',') if n.strip()]:
        cls = strategies.REGISTRY[name]
        if cls.needs_facts:
            start = facts_start(panel, cls())
        else:
            start = flow_start if cls.needs_flows else price_start
        if start is None or start >= end:
            results[name] = dict(error='資料不足')
            continue
        if start not in windows:
            windows[start] = (backtest.benchmark_equal_weight(panel, start, end), tr_index(panel, start, end))
        ew, tr = windows[start]
        results[name] = evaluate(panel, name, start, end, ew, tr)
        print(f"{name}: cagr={results[name]['cagr']:+.2%} excess={results[name]['excess_annual']:+.2%} "
              f"t={results[name]['excess_t']:.2f} ({results[name]['seconds']}s)", flush=True)
    benchmarks = {}
    for start, (ew, tr) in windows.items():
        key = f'{panel.dates[start]}～{panel.dates[end]}'
        benchmarks[key] = dict(equal_weight=backtest.metrics(ew),
                               taiex_tr=backtest.metrics(tr) if tr else None,
                               etf_0050=backtest.metrics(backtest.benchmark_symbol(panel, '0050', start, end)))
    payload = dict(generated_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                   period=[panel.dates[0].isoformat(), panel.dates[-1].isoformat()],
                   checks=CHECKS, strategies=results, benchmarks=benchmarks)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'acceptance.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    report(results, CHECKS, panel, args.out)
    write_benchmarks(args.out / 'acceptance.md', benchmarks)
    return 0 if all(c['passed'] is not False for c in CHECKS) else 1


def write_benchmarks(path, benchmarks):
    text = path.read_text(encoding='utf-8').rstrip()
    lines = ['', '### 基準', '', '| 期間 | 基準 | 年化報酬 | 年化波動 | Sharpe | 最大回撤 |', '|---|---|---:|---:|---:|---:|']
    labels = dict(equal_weight='等權股票（未扣成本）', taiex_tr='發行量加權股價報酬指數', etf_0050='元大台灣50（0050）含息')
    for window, values in benchmarks.items():
        for key, m in values.items():
            if m:
                lines.append(f"| {window} | {labels[key]} | {pct(m['cagr'])} | {m['volatility']:.1%} | "
                             f"{m['sharpe']:.2f} | {m['max_drawdown']:.1%} |")
    head, marker, tail = text.partition('\n## 三、限制')
    path.write_text(head.rstrip() + '\n' + '\n'.join(lines) + '\n\n' + marker.lstrip('\n') + tail + '\n',
                    encoding='utf-8')


if __name__ == '__main__':
    sys.exit(main())
