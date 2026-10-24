"""Acceptance run: validate the historical data, then backtest every strategy.

    python -m tw_stock_digest.acceptance --cache cache/history --out docs/strategies

Data checks have pass/fail thresholds. Strategy results are findings, compared
with the direction the literature (docs/strategies/research.md) leads us to expect.
"""
import argparse
import csv
import datetime as dt
import json
from pathlib import Path
import statistics
import sys
import time

from . import backtest, strategies
from .history import HEADERS
from .http import HttpClient
from .panel import build, screen_snapshot

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
}
CHECKS = []


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
    gaps = [(a, b) for a, b in zip(panel.dates, panel.dates[1:]) if (b - a).days > 11]
    detail = (f'{len(panel.dates)} 個交易日（{panel.dates[0]}～{panel.dates[-1]}）；每日有成交股票數 '
              f'上市 {min(counts["TWSE"])}–{max(counts["TWSE"])}、上櫃 {min(counts["TPEX"])}–{max(counts["TPEX"])}；'
              f'超過 11 天的空檔 {len(gaps)} 個')
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
    corr = statistics.correlation(er, ir)
    # 0050 charges roughly 0.3-0.4% a year and holds some cash, so a small shortfall is expected.
    diff = etf_cagr - idx_cagr
    check('0050 含息報酬 vs 臺灣50報酬指數', -0.015 < diff < 0.005 and corr > 0.98,
          f'0050 年化 {etf_cagr:.2%}、指數年化 {idx_cagr:.2%}、差 {diff:+.2%}；日報酬相關係數 {corr:.4f}')


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
            path.write_text(json.dumps(http.json('GET', FINMIND.format(symbol, start, end), headers=HEADERS)))
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


def check_snapshot(panel, data_dir):
    """The live pipeline snapshot and the historical report must describe the same day."""
    results = []
    for path in sorted(Path(data_dir).glob('daily/*/quotes.csv')):
        day = dt.date.fromisoformat(path.parent.name)
        i = panel.index.get(day)
        if i is None:
            continue
        quotes, compared, mismatched = [], 0, 0
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
                pairs = [(q['open'], panel.data['open'][s][i]), (q['close'], panel.data['close'][s][i]),
                         (q['volume_shares'], panel.data['volume'][s][i])]
                if any((a is None) != (b is None) or (a is not None and abs(a - b) > 1e-6) for a, b in pairs):
                    mismatched += 1
        names = [n for n, c in strategies.REGISTRY.items() if c.lookback == 1 and not c.needs_flows]
        snap = screen_snapshot(quotes, names, min_turnover=0)
        view = strategies.MarketView(panel, i)
        same = all([p.symbol for p in snap[n]] == [p.symbol for p in strategies.get(n, min_turnover=0).select(view)]
                    for n in names)
        results.append((day, compared, mismatched, same))
    if not results:
        check('每日快照 vs 歷史報表', False, '沒有重疊日期')
        return
    ok = all(m == 0 and same for _, _, m, same in results)
    check('每日快照 vs 歷史報表', ok, '；'.join(
        f'{d}：{c} 檔比對、{m} 檔不一致、盤後篩選結果{"一致" if s else "不一致"}' for d, c, m, s in results))


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
        lines.append(f"| {c['name']} | {'✅ 通過' if c['passed'] else '❌ 未通過'} | {c['detail']} |")
    lines += ['', '## 二、策略回測', '',
              '扣除手續費 0.1425%（買賣各一次）與證交稅 0.3%；訊號用當日收盤、隔日開盤成交；每期等權 20 檔。'
              '超額報酬以同範圍等權組合為基準，t 值未做自我相關調整，|t| > 2 才視為有方向。', '',
              '| 策略 | 換股 | 年化報酬 | 未扣成本 | 年化波動 | Sharpe | 最大回撤 | 超額（等權） | t 值 | 超額（加權報酬指數） | 前半／後半超額 | 年週轉 | 文獻預期 | 觀察 |',
              '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---|---|']
    sign = {1: '正', -1: '負', 0: '不顯著'}
    for name, m in results.items():
        if 'error' in m:
            lines.append(f"| {name} | — | 無法執行：{m['error']} |" + ' |' * 11)
            continue
        agree = '✔' if m['expected'] == m['observed'] else '✘'
        lines.append(
            f"| {m['label']}<br>`{name}` | {m['rebalance']} | {pct(m['cagr'])} | {pct(m['gross_cagr'])} | "
            f"{m['volatility']:.1%} | {m['sharpe']:.2f} | {m['max_drawdown']:.1%} | {pct(m['excess_annual'])} | "
            f"{m['excess_t']:.2f} | {pct(m['vs_taiex_tr'])} | {pct(m['halves_excess'][0])}／{pct(m['halves_excess'][1])} | "
            f"{m['turnover_per_year']:.1f}× | {sign[m['expected']]} | {sign[m['observed']]} {agree} |")
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
    check_coverage(panel)
    check_references(panel)
    check_limits(panel)
    check_total_return(panel)
    if not args.no_finmind:
        check_finmind(panel, args.cache)
    check_snapshot(panel, args.data)

    end = len(panel.dates) - 1
    price_start = 250  # one year of history before the first decision
    flow_days = [i for i, ok in enumerate(panel.flows_available) if ok]
    flow_start = flow_days[0] + 20 if flow_days else None
    windows = {}
    results = {}
    for name in [n.strip() for n in args.strategies.split(',') if n.strip()]:
        cls = strategies.REGISTRY[name]
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
    return 0 if all(c['passed'] for c in CHECKS) else 1


def write_benchmarks(path, benchmarks):
    text = path.read_text(encoding='utf-8').rstrip()
    lines = ['', '### 基準', '', '| 期間 | 基準 | 年化報酬 | 年化波動 | Sharpe | 最大回撤 |', '|---|---|---:|---:|---:|---:|']
    labels = dict(equal_weight='等權股票（未扣成本）', taiex_tr='發行量加權股價報酬指數', etf_0050='元大台灣50（0050）含息')
    for window, values in benchmarks.items():
        for key, m in values.items():
            if m:
                lines.append(f"| {window} | {labels[key]} | {pct(m['cagr'])} | {m['volatility']:.1%} | "
                             f"{m['sharpe']:.2f} | {m['max_drawdown']:.1%} |")
    path.write_text(text + '\n' + '\n'.join(lines) + '\n', encoding='utf-8')


if __name__ == '__main__':
    sys.exit(main())
