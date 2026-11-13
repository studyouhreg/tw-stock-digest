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
    insights = context.get('insights') or {}
    index = insights.get('index') or {}
    if index.get('taiex'):
        t = index['taiex']
        text = f"大盤：加權指數 {t['close']:,.2f}（{signed(t['change'])}，{percent(t['change_pct'])}%）"
        if t.get('turnover_100m_twd') is not None:
            text += f"，成交金額 {t['turnover_100m_twd']:,.0f} 億"
        if index.get('tpex'):
            text += f"；櫃買指數 {index['tpex']['close']:,.2f}（{percent(index['tpex']['change_pct'])}%）"
        points.append(text)
        title.append(f"加權 {t['close']:,.0f}（{percent(t['change_pct'])}%）")
    else:
        title.append(breadth[0])
    points.append('漲跌家數：' + '、'.join(breadth))
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
    futures = insights.get('futures') or {}
    if futures.get('institutions') and 'foreign' in futures['institutions']['net_oi']:
        net = futures['institutions']['net_oi']['foreign']
        text = f"期貨：外資臺股期貨淨{'多' if net >= 0 else '空'}單 {abs(net):,} 口"
        moved = (futures['institutions'].get('net_oi_change') or {}).get('foreign')
        if moved:
            grew = (moved > 0) == (net >= 0)
            text += f"（較前一交易日{'增加' if grew else '減少'} {abs(moved):,} 口）"
        if futures.get('put_call'):
            text += f"；Put/Call 未平倉比 {futures['put_call']['oi_ratio']}%"
        points.append(text)
    margin = ((insights.get('margin') or {}).get('market') or {}).get('TWSE')
    if margin and margin.get('margin_100m_twd') is not None:
        points.append(f"融資：上市融資餘額 {margin['margin_100m_twd']:,.0f} 億（{signed(margin['margin_change_100m_twd'])} 億）")
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
    breadth_now = (insights.get('technicals') or {}).get('breadth')
    if breadth_now:
        text = f"技術面：{breadth_now['above_ma20_pct']}% 的股票站上 20 日均線"
        if breadth_now.get('window'):
            text += f"；創 {breadth_now['window']} 日新高 {breadth_now['new_highs']} 檔、新低 {breadth_now['new_lows']} 檔"
        points.append(text)
    notes = []
    if (insights.get('flags') or {}).get('watchlist'):
        notes.append('自選股列入注意或處置：' + '、'.join(insights['flags']['watchlist']))
    if (insights.get('announcements') or {}).get('items'):
        notes.append(f"自選股重大訊息 {len(insights['announcements']['items'])} 則")
    if (insights.get('exdividend') or {}).get('watchlist'):
        notes.append('自選股即將除權息：' + '、'.join(
            f"{w['name']} {w['date'][5:]}" for w in insights['exdividend']['watchlist']))
    if notes:
        points.append('留意：' + '；'.join(notes))
    return dict(title='｜'.join(title), points=points)


def factual_report(context, demo=False):
    lines = [f"# 臺股盤後資料摘要 · {context['trade_date']}", '',
             '**合成示範資料，非實際行情。**' if demo else '以下數字由程式整理；尚未包含 Claude 分析。', '',
             '## 今日重點', ''] + ['- ' + point for point in headline(context)['points']]
    insights = context.get('insights') or {}
    if insights.get('index'):
        lines.extend(index_lines(insights['index']))
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
    lines.extend(insight_sections(insights, skip=('index',)))
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


def valuation_lines(v):
    lines = ['', '## 本益比與估值', '', '官方日報的本益比（近四季 EPS）、股價淨值比與殖利率；虧損公司無本益比。', '']
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
    return lines


def institutions_lines(f):
    lines = ['', '## 法人資金流向', '']
    if 'today_100m_twd' not in f:
        return lines + [f.get('note', '無法人資料')]
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
    return lines


def rotation_lines(r):
    lines = ['', '## 產業資金輪動', '']
    if 'gaining' not in r:
        return lines + [r.get('note', '無資料')]
    lines += [f'成交金額占比與前 {r["days_available"]} 個交易日平均比較；5 日報酬為產業成分股中位數。', '']
    header = ['產業', '今日占比 %', '平均占比 %', '變化（百分點）', '5 日報酬中位數 %', '外資累計億元']
    for key, title in (('gaining', '資金流入（占比上升）'), ('losing', '資金流出（占比下降）')):
        lines += [f'{title}：', '']
        lines += table(header, left=1, rows=[[x['industry'], x['turnover_share_pct'], x['share_average_pct'],
                                             signed(x['share_change_pp']), x['median_return_5d_pct'],
                                             signed(x['foreign_window_100m_twd'])] for x in r[key]])
        lines.append('')
    return lines[:-1]


def money_flow_lines(m):
    lines = ['', '## 個股量價資金流', '']
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


def whole(value, unit=''):
    return '—' if value is None else f'{value:+,.0f}{unit}'


def plain(value):
    return '—' if value is None else f'{value:,.0f}'


def index_lines(x):
    lines, rows = ['', '## 大盤與類股', ''], []
    for label, key in (('加權指數', 'taiex'), ('櫃買指數', 'tpex')):
        if x.get(key):
            i = x[key]
            rows.append([label, f"{i['close']:,.2f}", signed(i['change']), percent(i['change_pct']),
                         '—' if i.get('turnover_100m_twd') is None else f"{i['turnover_100m_twd']:,.0f}"])
    lines += table(['指數', '收盤', '漲跌點', '漲跌 %', '成交金額（億）'], rows)
    if x.get('sectors_up'):
        def names(items):
            return '、'.join(f"{s['name']} {percent(s['change_pct'])}%" for s in items)
        lines += ['', f"上市類股指數（{x['sector_count']} 類）：最強 {names(x['sectors_up'])}；最弱 {names(x['sectors_down'])}"]
    return lines


def futures_lines(x):
    lines = ['', '## 期貨與選擇權籌碼', '']
    inst = x.get('institutions')
    if inst:
        labels = (('外資', 'foreign'), ('投信', 'trust'), ('自營商', 'dealer'))
        change = inst.get('net_oi_change') or {}
        lines += ['臺股期貨三大法人未平倉淨額（口；正為淨多單、負為淨空單）：', '']
        lines += table(['身份', '未平倉淨額', '較前一交易日', '當日交易淨額'],
                       [[label, whole(inst['net_oi'].get(k)), whole(change.get(k)), whole(inst['net_traded'].get(k))]
                        for label, k in labels if k in inst['net_oi']])
    pc = x.get('put_call')
    if pc:
        moved = f"（較前一交易日 {signed(pc['oi_ratio_change'])} 個百分點）" if pc.get('oi_ratio_change') is not None else ''
        lines += ['', f"臺指選擇權 Put/Call 比：成交量 {pc['volume_ratio']}%、未平倉 {pc['oi_ratio']}%{moved}。"
                      '未平倉比高於 100% 代表賣權未平倉多於買權。']
    large = x.get('large_traders')
    if large:
        specific = f"（其中特定法人 {whole(large['top10_specific_net'])} 口）" if 'top10_specific_net' in large else ''
        lines += ['', f"臺股期貨大額交易人（所有月份）：前五大淨部位 {whole(large['top5_net'])} 口、"
                      f"前十大 {whole(large['top10_net'])} 口{specific}；全市場未平倉 {plain(large['market_oi'])} 口。"]
    return lines


def margin_lines(x):
    lines = ['', '## 融資融券與借券', '']
    if x.get('market'):
        lines += table(['市場', '融資餘額（億）', '增減（億）', '融券餘額（張）', '增減（張）', '借券賣出餘額（張）', '增減（張）'],
                       [[MARKET_NAMES.get(m, m), '—' if v['margin_100m_twd'] is None else f"{v['margin_100m_twd']:,.2f}",
                         signed(v['margin_change_100m_twd']), plain(v['short_lots']), whole(v['short_lots_change']),
                         plain(v['sbl_lots']), whole(v['sbl_lots_change'])]
                        for m, v in sorted(x['market'].items(), key=lambda item: (item[0] != 'TWSE', item[0]))])
    for key, title in (('increases', '融資增加最多（依金額）'), ('decreases', '融資減少最多（依金額）')):
        if x.get(key):
            lines += ['', f'{title}：', '']
            lines += table(['代碼', '名稱', '增減（張）', '增減（億）', '融資餘額（張）', '今日漲跌 %'],
                           [[r['symbol'], r['name'], whole(r['margin_change_lots']), signed(r['margin_change_100m_twd']),
                             plain(r['margin_lots']), r['change_pct']] for r in x[key]], left=2)
    if x.get('watchlist'):
        days = max((w['window_days'] for w in x['watchlist']), default=0)
        lines += ['', '自選清單（張）：', '']
        lines += table(['代碼', '名稱', '融資餘額', '今日增減', f'{days} 日增減', '融券餘額', '券資比 %', '借券賣出餘額', '借券增減'],
                       [[w['symbol'], w['name'], plain(w['margin_lots']), whole(w['margin_change_lots']),
                         whole(w['margin_window_change_lots']), plain(w['short_lots']), w['short_margin_ratio_pct'],
                         plain(w['sbl_lots']), whole(w['sbl_change_lots'])] for w in x['watchlist']], left=2)
    return lines


def daytrade_lines(x):
    lines = ['', '## 當沖', '']
    if x.get('market'):
        lines += ['當沖占全市場比重：' + '；'.join(
            f"{MARKET_NAMES.get(m, m)} 成交股數 {v['volume_share_pct']}%、買進金額 {v['buy_value_share_pct']}%"
            for m, v in sorted(x['market'].items(), key=lambda item: (item[0] != 'TWSE', item[0]))) + '。']
    if x.get('highest'):
        lines += ['', '當沖比最高（成交金額 ≥ 5 億；當沖股數 ÷ 成交股數）：', '']
        lines += table(['代碼', '名稱', '當沖比 %', '成交億元', '漲跌 %'],
                       [[r['symbol'], r['name'], r['daytrade_pct'], r['turnover_100m_twd'], r['change_pct']]
                        for r in x['highest']], left=2)
    if x.get('watchlist'):
        lines += ['', '自選清單當沖比：' + '、'.join(f"{w['name']} {w['daytrade_pct']}%" for w in x['watchlist'])]
    return lines


def technicals_lines(x):
    lines = ['', '## 技術面', '', f"使用已保存的 {x['days_available']} 個交易日；價格未還原除權息。"]
    if x.get('note'):
        lines += ['', x['note']]
    b = x.get('breadth')
    if b:
        text = f"市場寬度（{b['counted']:,} 檔，日成交金額 ≥ 2,000 萬）：{b['above_ma20_pct']}% 站上 20 日均線"
        if b.get('above_ma60_pct') is not None:
            text += f"、{b['above_ma60_pct']}% 站上 60 日均線"
        if b.get('window'):
            text += f"；收盤創 {b['window']} 日新高 {b['new_highs']} 檔、新低 {b['new_lows']} 檔"
        lines += ['', text + '。']
    if x.get('new_highs'):
        lines += ['', f"創 {b['window']} 日新高（成交金額 ≥ 1 億，依成交金額排序）：", '']
        lines += table(['代碼', '名稱', '收盤', '漲跌 %', '成交億元'],
                       [[r['symbol'], r['name'], price(r['close']), r['change_pct'], r['turnover_100m_twd']]
                        for r in x['new_highs']], left=2)
    if x.get('watchlist'):
        lines += ['', '自選清單（乖離為收盤相對均線的百分比；60 日位置 0 為區間低點、100 為高點）：', '']
        lines += table(['代碼', '名稱', '5 日乖離 %', '20 日乖離 %', '60 日乖離 %', 'RSI(14)', 'K(9)', 'D(9)',
                        'MACD 柱 %', '60 日位置'],
                       [[w['symbol'], w['name'], signed(w['bias5_pct']), signed(w['bias20_pct']), signed(w['bias60_pct']),
                         w['rsi14'], w['k9'], w['d9'], signed(w['macd_hist_pct']), w['range60_pct']]
                        for w in x['watchlist']], left=2)
    return lines


def holders_lines(x):
    lines = ['', '## 大戶持股（集保）', '']
    compared = f"，與 {x['previous_date']} 比較" if x.get('previous_date') else '；尚無上一週資料可比較'
    lines += [f"集保戶股權分散表資料日 {x['date']}（每週更新）{compared}。比例為占集保庫存的百分比。"]
    if x.get('watchlist'):
        lines += ['', '自選清單：', '']
        lines += table(['代碼', '名稱', '400 張以上 %', '週變化', '1,000 張以上 %', '週變化', '股東人數', '週變化'],
                       [[w['symbol'], w['name'], w['big400_pct'], signed(w['big400_change_pp']), w['big1000_pct'],
                         signed(w['big1000_change_pp']), plain(w['holders']), whole(w['holders_change'])]
                        for w in x['watchlist']], left=2)
    for key, title in (('increases', '400 張以上持股比例增加最多（成交金額 ≥ 1 億）'), ('decreases', '減少最多')):
        if x.get(key):
            lines += ['', f'{title}：' + '、'.join(
                f"{r['symbol']} {r['name']} {signed(r['big400_change_pp'])}（{r['big400_pct']}%）" for r in x[key])]
    return lines


def flags_lines(x):
    lines = ['', '## 注意與處置股', '']
    if x.get('watchlist'):
        lines += ['**自選股在名單內：' + '、'.join(x['watchlist']) + '**', '']
    names = '、'.join(f"{a['symbol']} {a['name']}" for a in x['attention'])
    more = f"（列出前 {len(x['attention'])} 檔）" if x['attention_count'] > len(x['attention']) else ''
    lines += [f"今日公布注意股 {x['attention_count']} 檔{more}" + (f'：{names}' if names else '') + '。']
    if x['disposal']:
        lines += ['', f"處置中或即將處置 {x['disposal_count']} 檔（處置期間多為分盤撮合）：", '']
        lines += table(['代碼', '名稱', '起', '迄'],
                       [[d['symbol'], d['name'], d['start'], d['end']] for d in x['disposal']], left=4)
    else:
        lines += ['', '目前沒有處置中的股票或 ETF。']
    return lines


def announcements_lines(x):
    since = f"自 {x['since'][5:]} 起" if x.get('since') else '前一交易日收盤後起'
    lines = ['', '## 自選股重大訊息', '', f"公開資訊觀測站{since}至收集時的重大訊息；當日清單共 {x['count']:,} 則。"]
    if x['items']:
        lines += [''] + [f"- {i['date'][5:]} {i['time']} {i['symbol']} {i['name']}：{i['subject']}" for i in x['items']]
    else:
        lines += ['', '這段期間自選股沒有重大訊息。']
    return lines


def revenue_lines(x):
    lines = ['', '## 月營收', '']
    lines += ['交易所目前提供的最新月份：' + '；'.join(
        f"{MARKET_NAMES.get(m, m)} {v['month']}（{v['reported']:,} 家，年增率中位數 {percent(v['median_yoy_pct'])}%）"
        for m, v in sorted(x['markets'].items(), key=lambda item: (item[0] != 'TWSE', item[0]))) + '。']
    if x['watchlist']:
        lines += ['', '自選清單：', '']
        lines += table(['代碼', '名稱', '月份', '營收（億）', '月增 %', '年增 %', '累計年增 %'],
                       [[w['symbol'], w['name'], w['month'], w['revenue_100m_twd'], percent(w['mom_pct']),
                         percent(w['yoy_pct']), percent(w['ytd_yoy_pct'])] for w in x['watchlist']], left=3)
    return lines


def exdividend_lines(x):
    lines = ['', '## 除權息預告', '']
    if not x['count']:
        return lines + ['未來 7 天沒有除權息的股票或 ETF。']
    names = '、'.join(f"{n['symbol']} {n['name']}（{n['kind']}）" for n in x['next'])
    lines += [f"未來 7 天共 {x['count']} 檔；最近一批 {x['next_date']}：{names}。"]
    if x['watchlist']:
        lines += ['', '自選股：' + '、'.join(f"{w['name']} {w['date']}（{w['kind']}）" for w in x['watchlist'])]
    return lines


SECTIONS = (('index', index_lines), ('valuation', valuation_lines), ('institutions', institutions_lines),
            ('futures', futures_lines), ('margin', margin_lines), ('rotation', rotation_lines),
            ('money_flow', money_flow_lines), ('technicals', technicals_lines), ('daytrade', daytrade_lines),
            ('holders', holders_lines), ('flags', flags_lines), ('announcements', announcements_lines),
            ('revenue', revenue_lines), ('exdividend', exdividend_lines))


def insight_sections(insights, skip=()):
    lines = []
    for key, render in SECTIONS:
        if key not in skip and insights.get(key):
            lines += render(insights[key])
    return lines
