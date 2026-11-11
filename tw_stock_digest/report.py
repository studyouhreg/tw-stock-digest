"""Human-readable factual reports, with no model required."""
import datetime as dt


def cell(value):
    return '—' if value is None else str(value).replace('|', '\\|').replace('\n', ' ')


MARKET_NAMES = {'TWSE': '上市', 'TPEX': '上櫃'}


def price(value):
    return '—' if value is None else f'{value:,.2f}'.rstrip('0').rstrip('.')


def percent(value):
    return '—' if value is None else f'{value:+.2f}'


def lots(shares):
    return '—' if shares is None else f'{round(shares / 1000):,}'


def ordered_markets(context):
    return sorted(context['markets'].items(), key=lambda item: (item[0] != 'TWSE', item[0]))


def headline(context):
    """Short title and key points: the top of the digest and the notification."""
    date = context['trade_date']
    weekday = '一二三四五六日'[dt.date.fromisoformat(date).weekday()]
    title, points = [f'臺股盤後 {date}（{weekday}）'], []
    breadth = [f"{MARKET_NAMES.get(m, m)} {x['equities']['advancers']} 漲 {x['equities']['decliners']} 跌"
               for m, x in ordered_markets(context)]
    points.append('漲跌家數：' + '、'.join(breadth))
    title.append(breadth[0])
    insights = context.get('insights') or {}
    market = (insights.get('institutions') or {}).get('market')
    if market:
        window = insights['institutions']['window_days']

        def flow(label, key):
            m = market[key]
            detail = [streak_text(m['streak_days'])] if abs(m['streak_days']) > 1 else []
            if window > 1:
                detail.append(f"{window} 日累計 {signed(m['window_100m_twd'])} 億")
            return f"{label} {signed(m['today_100m_twd'])} 億" + (f"（{'，'.join(detail)}）" if detail else '')
        points.append('法人買賣超：' + '；'.join(flow(label, key) for label, key in
                                              (('外資', 'foreign'), ('投信', 'trust'), ('自營商', 'dealer'))))
        title.append(f"外資 {market['foreign']['today_100m_twd']:+,.0f} 億、投信 {market['trust']['today_100m_twd']:+,.0f} 億")
    rotation = insights.get('rotation') or {}
    if rotation.get('gaining') and rotation.get('losing'):
        up, down = rotation['gaining'][0], rotation['losing'][0]
        points.append(f"產業資金：流入最多 {up['industry']}（成交占比 {signed(up['share_change_pp'])} 個百分點），"
                      f"流出最多 {down['industry']}（{signed(down['share_change_pp'])}）")
    moves = sorted((q for q in context['watchlist'] if q['change_pct'] is not None),
                   key=lambda q: -q['change_pct'])
    if moves:
        points.append('自選股：' + '、'.join(f"{q['name']} {percent(q['change_pct'])}%" for q in moves))
    flow_counts = insights.get('money_flow') or {}
    if flow_counts.get('volume_surge_up') or flow_counts.get('mfi_high') or flow_counts.get('mfi_low'):
        points.append(f"量價：量增價漲 {len(flow_counts.get('volume_surge_up') or [])} 檔上榜；"
                      f"MFI ≥ 80 有 {len(flow_counts.get('mfi_high') or [])} 檔上榜、"
                      f"≤ 20 有 {len(flow_counts.get('mfi_low') or [])} 檔上榜")
    return dict(title='｜'.join(title), points=points)


def factual_report(context, demo=False):
    lines = [f"# 臺股盤後資料摘要 · {context['trade_date']}", '',
             '**合成示範資料，非實際行情。**' if demo else '以下數字由程式整理；尚未包含 Claude 分析。', '',
             '## 今日重點', ''] + ['- ' + point for point in headline(context)['points']]
    lines.extend(['', '## 市場概況', '',
                  '| 市場 | 股票類筆數 | 上漲 | 下跌 | 平盤 | 漲跌缺值 | ETF 筆數 |',
                  '|---|---:|---:|---:|---:|---:|---:|'])
    for name, market in ordered_markets(context):
        e = market['equities']
        lines.append(f"| {MARKET_NAMES.get(name, name)} | {e['count']} | {e['advancers']} | {e['decliners']} | {e['unchanged']} | {e['unknown_change_count']} | {market['etf_count']} |")
    lines.extend(['', '## 自選清單', '', '| 市場 | 代碼 | 名稱 | 收盤 | 漲跌 % | 成交張數 |',
                  '|---|---|---|---:|---:|---:|'])
    for q in context['watchlist']:
        lines.append('| ' + ' | '.join(cell(v) for v in (
            MARKET_NAMES.get(q['market'], q['market']), q['symbol'], q['name'], price(q['close']),
            percent(q['change_pct']), lots(q['volume_shares']))) + ' |')
    if context['missing_watchlist']:
        lines.extend(['', '未取得：' + ', '.join(context['missing_watchlist'])])
    insights = context.get('insights')
    if insights:
        lines.extend(insight_sections(insights))
    lines.extend(['', '## 資料限制', ''] + ['- ' + x for x in context['limitations']])
    lines.extend(['', '## 來源', ''] + [f"- {MARKET_NAMES.get(m, m)}：{x['source']}" for m, x in ordered_markets(context)])
    if context.get('source_note'):
        lines.append(f"- 備註：{context['source_note']}")
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
                   [[MARKET_NAMES.get(m, m), x['median_pe'], x['median_pb'], x['median_dividend_yield'],
                     x['with_pe'], x['without_pe']]
                    for m, x in sorted(v['markets'].items(), key=lambda item: (item[0] != 'TWSE', item[0]))])
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
                       [[{'ALL': '合計', **MARKET_NAMES}.get(m, m), signed(x['foreign']), signed(x['trust']), signed(x['dealer'])]
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
