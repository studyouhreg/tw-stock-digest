"""Human-readable factual reports, with no model required."""


def cell(value):
    return '—' if value is None else str(value).replace('|', '\\|').replace('\n', ' ')


def factual_report(context, demo=False):
    lines = [f"# 臺股盤後資料摘要 · {context['trade_date']}", '',
             '**合成示範資料，非實際行情。**' if demo else '以下數字由程式整理；尚未包含 Claude 分析。', '',
             '| 市場 | 股票類筆數 | 上漲 | 下跌 | 平盤 | 漲跌缺值 | ETF 筆數 |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for name, market in context['markets'].items():
        e = market['equities']
        lines.append(f"| {name} | {e['count']} | {e['advancers']} | {e['decliners']} | {e['unchanged']} | {e['unknown_change_count']} | {market['etf_count']} |")
    lines.extend(['', '## 自選清單', '', '| 市場 | 代碼 | 名稱 | 收盤 TWD | 價差 % | 成交股數 |',
                  '|---|---|---|---:|---:|---:|'])
    for q in context['watchlist']:
        lines.append('| ' + ' | '.join(cell(q[k]) for k in
                      ('market', 'symbol', 'name', 'close', 'change_pct', 'volume_shares')) + ' |')
    if context['missing_watchlist']:
        lines.extend(['', '未取得：' + ', '.join(context['missing_watchlist'])])
    lines.extend(['', '## 資料限制', ''] + ['- ' + x for x in context['limitations']])
    lines.extend(['', '## 來源', ''] + [f"- {m}：{x['source']}" for m, x in context['markets'].items()])
    return '\n'.join(lines) + '\n'
