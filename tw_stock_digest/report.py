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
    insights = context.get('insights')
    if insights:
        lines.extend(insight_sections(insights))
    lines.extend(['', '## 資料限制', ''] + ['- ' + x for x in context['limitations']])
    lines.extend(['', '## 來源', ''] + [f"- {m}：{x['source']}" for m, x in context['markets'].items()])
    return '\n'.join(lines) + '\n'


def signed(value, unit=''):
    return '—' if value is None else f'{value:+,.2f}{unit}'


def streak_text(days):
    if not days:
        return '—'
    return f'連買 {days} 日' if days > 0 else f'連賣 {-days} 日'


def table(header, rows, left=1):
    out = ['| ' + ' | '.join(header) + ' |', '|' + '|'.join('---:' if i >= left else '---' for i in range(len(header))) + '|']
    out += ['| ' + ' | '.join(cell(c) if not isinstance(c, str) or c != '' else '—' for c in r) + ' |' for r in rows]
    return out


def insight_sections(insights):
    lines = []
    v = insights['valuation']
    lines += ['', '## 本益比與估值', '', '官方日報的本益比（近四季 EPS）、股價淨值比與殖利率；虧損公司無本益比。', '']
    lines += table(['市場', '本益比中位數', '淨值比中位數', '殖利率中位數 %', '有本益比', '無本益比（虧損等）'],
                   [[m, x['median_pe'], x['median_pb'], x['median_dividend_yield'], x['with_pe'], x['without_pe']]
                    for m, x in v['markets'].items()])
    if v['watchlist']:
        lines += ['', '自選清單：', '']
        lines += table(['代碼', '名稱', '產業', '本益比', '產業中位數', '股價淨值比', '殖利率 %'],
                       [[w['symbol'], w['name'], w['industry'] or '—', w['pe'], w['industry_median_pe'],
                         w['pb'], w['dividend_yield']] for w in v['watchlist']], left=3)
    if v['industries']:
        items = list(v['industries'].items())
        lines += ['', '產業本益比中位數（至少 5 檔有本益比）：最低 ' +
                  '、'.join(f'{k} {x["median_pe"]}' for k, x in items[:5]) + '；最高 ' +
                  '、'.join(f'{k} {x["median_pe"]}' for k, x in reversed(items[-5:]))]

    f = insights['institutions']
    lines += ['', '## 法人資金流向', '']
    if 'today_100m_twd' not in f:
        lines.append(f.get('note', '無法人資料'))
    else:
        lines += [f'金額為買賣超股數 × 當日收盤價（億元，近似值）。累計期間為最近 {f["window_days"]} 個有法人資料的交易日。', '']
        lines += table(['市場', '外資', '投信', '自營商'],
                       [[{'ALL': '合計'}.get(m, m), signed(x['foreign']), signed(x['trust']), signed(x['dealer'])]
                        for m, x in f['today_100m_twd'].items()])
        if len(f['foreign_series_100m_twd']) > 1:
            lines += ['', '外資逐日（億元）：' + '、'.join(
                f'{d["date"][5:]} {signed(d["foreign"])}' for d in f['foreign_series_100m_twd'])]
        for key, title in (('foreign_buy', '外資累計買超'), ('trust_buy', '投信累計買超'),
                           ('foreign_sell', '外資累計賣超'), ('trust_sell', '投信累計賣超')):
            if f[key]:
                lines += ['', f'{title}（{f["window_days"]} 日）：', '']
                lines += table(['代碼', '名稱', '累計億元', '連續', '今日漲跌 %'],
                               [[x['symbol'], x['name'], signed(x['net_100m_twd']), streak_text(x['streak_days']),
                                 x['change_pct']] for x in f[key]], left=2)
        if f['watchlist']:
            lines += ['', '自選清單：', '']
            lines += table(['代碼', '名稱', '外資今日', f'外資 {f["window_days"]} 日', '外資連續',
                            '投信今日', f'投信 {f["window_days"]} 日', '投信連續'],
                           [[w['symbol'], w['name'], signed(w['foreign_today_100m_twd']),
                             signed(w['foreign_window_100m_twd']), streak_text(w['foreign_streak_days']),
                             signed(w['trust_today_100m_twd']), signed(w['trust_window_100m_twd']),
                             streak_text(w['trust_streak_days'])] for w in f['watchlist']], left=2)

    r = insights['rotation']
    lines += ['', '## 產業資金輪動', '']
    if 'gaining' not in r:
        lines.append(r.get('note', '無資料'))
    else:
        lines += [f'成交金額占比與前 {r["days_available"]} 個交易日平均比較；5 日報酬為產業成分股中位數。', '']
        header = ['產業', '今日占比 %', '平均占比 %', '變化（百分點）', '5 日報酬中位數 %', '外資累計億元']
        for key, title in (('gaining', '資金流入（占比上升）'), ('losing', '資金流出（占比下降）')):
            lines += [f'{title}：', '']
            lines += table(header, left=1, rows=[[x['industry'], x['turnover_share_pct'], x['share_average_pct'],
                                     signed(x['share_change_pp']), x['median_return_5d_pct'],
                                     signed(x['foreign_window_100m_twd'])] for x in r[key]])
            lines.append('')

    m = insights['money_flow']
    lines += ['', '## 個股量價資金流', '']
    if m.get('note'):
        lines += [m['note'], '']
    if m.get('volume_surge_up'):
        lines += ['量增價漲：成交金額 ≥ 前 20 日平均 2 倍且收紅（成交金額 ≥ 5,000 萬元）', '']
        lines += table(['代碼', '名稱', '量增倍數', '成交億元', '漲跌 %'],
                       [[x['symbol'], x['name'], x['turnover_ratio'], x['turnover_100m_twd'], x['change_pct']]
                        for x in m['volume_surge_up']], left=2)
        lines.append('')
    for key, title in (('mfi_high', 'MFI(14) ≥ 80（資金持續流入）'), ('mfi_low', 'MFI(14) ≤ 20（資金持續流出）')):
        if m.get(key):
            lines += [f'{title}：' + '、'.join(f'{x["symbol"]} {x["name"]} {x["mfi"]}' for x in m[key]), '']
    if m.get('watchlist'):
        lines += ['自選清單（OBV 10 日淨量以日均量為單位，正值代表上漲日量能較大）：', '']
        lines += table(['代碼', '名稱', 'MFI(14)', 'OBV 10 日'],
                       [[w['symbol'], w['name'], w['mfi14'], w['obv10_days']] for w in m['watchlist']], left=2)
    return lines
