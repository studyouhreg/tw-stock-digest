"""Turn the auxiliary payloads and per-symbol columns into digest sections.

Pure calculations: ``payloads`` come from ``auxiliary.fetch_auxiliary`` and
``days`` from ``insights`` (saved history plus today, each row carrying the
per-symbol auxiliary columns). A section is only present when its data is.
"""
from .insights import FLOW_DAYS, to_100m

LIQUID_TURNOVER = 100_000_000
DAYTRADE_TURNOVER = 500_000_000
MARKETS = (('TWSE', 'twse'), ('TPEX', 'tpex'))


def index_section(p):
    out = {}
    if p.get('twse_index'):
        turnover = p.get('twse_turnover') or {}
        out['taiex'] = dict(p['twse_index']['taiex'], turnover_100m_twd=to_100m(turnover.get('turnover_twd')))
        sectors = sorted(p['twse_index']['sectors'], key=lambda x: (-x['change_pct'], x['name']))
        out.update(sectors_up=sectors[:3], sectors_down=sectors[::-1][:3], sector_count=len(sectors))
    if p.get('tpex_index'):
        t = p['tpex_index']
        out['tpex'] = dict(close=t['close'], change=t['change'], change_pct=t['change_pct'],
                           turnover_100m_twd=to_100m(t['turnover_twd']))
    return out or None


def futures_section(p):
    out = {key: p[name] for key, name in (('institutions', 'taifex_futures'), ('put_call', 'taifex_pcr'),
                                           ('large_traders', 'taifex_large')) if p.get(name)}
    return out or None


def window_sum(days, symbol, field, n=FLOW_DAYS):
    values = [d['rows'].get(symbol, {}).get(field) for d in days[-n:]]
    present = [v for v in values if v is not None]
    return (sum(present), len(present)) if present else (None, 0)


def margin_section(p, days, watchlist, top):
    today = days[-1]['rows']
    market = {}
    for name, prefix in MARKETS:
        m, s = p.get(f'{prefix}_margin') or {}, p.get(f'{prefix}_sbl') or {}
        if m or s:
            market[name] = dict(margin_100m_twd=to_100m(m.get('margin_twd')),
                                margin_change_100m_twd=to_100m(m.get('margin_change_twd')),
                                margin_lots_change=m.get('margin_lots_change'),
                                short_lots=m.get('short_lots'), short_lots_change=m.get('short_lots_change'),
                                sbl_lots=s.get('sbl_lots'), sbl_lots_change=s.get('sbl_lots_change'))
    moves = [(r['margin_change'] * 1000 * r['close'], s, r) for s, r in today.items()
             if r['kind'] == 'equity' and r.get('margin_change') and r.get('close')]
    if not market and not moves:
        return None

    def ranked(rows):
        return [dict(symbol=s, name=r['name'], margin_change_lots=round(r['margin_change']),
                     margin_change_100m_twd=to_100m(v), margin_lots=round(r['margin'] or 0),
                     change_pct=None if r.get('change_pct') is None else round(r['change_pct'], 2))
                for v, s, r in rows[:top]]
    moves.sort(key=lambda x: (-x[0], x[1]))
    rows = []
    for s in watchlist:
        r = today.get(s)
        if not r or r.get('margin') is None:
            continue
        total, count = window_sum(days, s, 'margin_change')
        rows.append(dict(symbol=s, name=r['name'], margin_lots=round(r['margin']),
                         margin_change_lots=None if r.get('margin_change') is None else round(r['margin_change']),
                         margin_window_change_lots=None if total is None else round(total), window_days=count,
                         short_lots=None if r.get('short') is None else round(r['short']),
                         short_margin_ratio_pct=round(100 * r['short'] / r['margin'], 2) if r['margin'] and r.get('short') is not None else None,
                         sbl_lots=None if r.get('sbl') is None else round(r['sbl']),
                         sbl_change_lots=None if r.get('sbl_change') is None else round(r['sbl_change'])))
    return dict(market=market, increases=ranked([m for m in moves if m[0] > 0]),
                decreases=ranked([m for m in moves[::-1] if m[0] < 0]),
                watchlist=rows)


def daytrade_section(p, days, watchlist, top):
    today = days[-1]['rows']
    market = {name: p[f'{prefix}_daytrade'] for name, prefix in MARKETS if p.get(f'{prefix}_daytrade')}

    def ratio(r):
        return round(100 * r['daytrade'] / r['volume_shares'], 1) if r.get('daytrade') is not None and r.get('volume_shares') else None
    ranked = sorted(((ratio(r), s, r) for s, r in today.items() if r['kind'] == 'equity'
                     and (r.get('turnover_twd') or 0) >= DAYTRADE_TURNOVER and ratio(r) is not None),
                    key=lambda x: (-x[0], x[1]))
    rows = [dict(symbol=s, name=today[s]['name'], daytrade_pct=ratio(today[s])) for s in watchlist
            if s in today and ratio(today[s]) is not None]
    if not market and not ranked and not rows:
        return None
    return dict(market=market, watchlist=rows,
                highest=[dict(symbol=s, name=r['name'], daytrade_pct=v, turnover_100m_twd=to_100m(r['turnover_twd']),
                              change_pct=None if r.get('change_pct') is None else round(r['change_pct'], 2))
                         for v, s, r in ranked[:top]])


def flags_section(p, watchlist, limit=15):
    if not any(name in p for name in ('twse_notice', 'tpex_notice', 'twse_punish', 'tpex_punish')):
        return None
    attention = (p.get('twse_notice') or []) + (p.get('tpex_notice') or [])
    disposal = (p.get('twse_punish') or []) + (p.get('tpex_punish') or [])
    watch = set(watchlist)
    return dict(attention_count=len(attention), attention=attention[:limit],
                disposal_count=len(disposal), disposal=sorted(disposal, key=lambda x: (x['end'], x['symbol']))[:limit],
                watchlist=sorted({x['symbol'] for x in attention + disposal if x['symbol'] in watch}))


def announcements_section(p):
    m = p.get('mops_material')
    return dict(count=m['count'], since=m.get('since'), items=m['items'][-20:]) if m else None


def revenue_section(p):
    markets = {name: {k: v for k, v in p[f'{prefix}_revenue'].items() if k != 'items'}
               for name, prefix in MARKETS if p.get(f'{prefix}_revenue')}
    if not markets:
        return None
    return dict(markets=markets, watchlist=[i for _, prefix in MARKETS
                                            for i in (p.get(f'{prefix}_revenue') or {}).get('items', [])])


def exdividend_section(p):
    parts = [p[name] for name in ('twse_exright', 'tpex_exright') if p.get(name)]
    if not parts:
        return None
    dates = [part['next_date'] for part in parts if part['next_date']]
    first = min(dates) if dates else None
    return dict(count=sum(part['count'] for part in parts), next_date=first,
                next=[x for part in parts for x in part['next'] if x['date'] == first][:15],
                watchlist=[x for part in parts for x in part['watchlist']])


def holders_section(p, days, watchlist, top):
    if not p.get('tdcc'):
        return None
    date, today = p['tdcc']['date'], days[-1]['rows']
    earlier = next((d for d in reversed(days[:-1]) if d.get('tdcc_date') and d['tdcc_date'] < date), None)

    def change(symbol, field):
        now = today.get(symbol, {}).get(field)
        before = earlier['rows'].get(symbol, {}).get(field) if earlier else None
        return round(now - before, 2) if now is not None and before is not None else None
    rows = [dict(symbol=s, name=today[s]['name'], big400_pct=today[s]['big400_pct'],
                 big400_change_pp=change(s, 'big400_pct'), big1000_pct=today[s].get('big1000_pct'),
                 big1000_change_pp=change(s, 'big1000_pct'), holders=today[s].get('holders'),
                 holders_change=change(s, 'holders'))
            for s in watchlist if s in today and today[s].get('big400_pct') is not None]
    out = dict(date=date, previous_date=earlier['tdcc_date'] if earlier else None, watchlist=rows)
    if earlier:
        moves = sorted(((change(s, 'big400_pct'), s, r) for s, r in today.items() if r['kind'] == 'equity'
                        and (r.get('turnover_twd') or 0) >= LIQUID_TURNOVER and change(s, 'big400_pct') is not None),
                       key=lambda x: (-x[0], x[1]))

        def ranked(items):
            return [dict(symbol=s, name=r['name'], big400_pct=r['big400_pct'], big400_change_pp=v)
                    for v, s, r in items[:top]]
        out.update(increases=ranked([m for m in moves if m[0] > 0]),
                   decreases=ranked([m for m in moves[::-1] if m[0] < 0]))
    return out


def summarize(payloads, days, watchlist, top=5):
    """Digest sections from the auxiliary data; keys are omitted when there is nothing to show."""
    p = payloads or {}
    sections = dict(index=index_section(p), futures=futures_section(p),
                    margin=margin_section(p, days, watchlist, top),
                    daytrade=daytrade_section(p, days, watchlist, top),
                    flags=flags_section(p, watchlist), announcements=announcements_section(p),
                    revenue=revenue_section(p), exdividend=exdividend_section(p),
                    holders=holders_section(p, days, watchlist, top))
    return {key: value for key, value in sections.items() if value is not None}
