"""Recover trading days the daily run missed, from the exchanges' per-date reports.

The daily pipeline reads the "latest snapshot" APIs, which cannot be queried
for past days. TWSE (MI_INDEX) and TPEx (afterTrading/otc) also publish a
report for any date; this module converts those rows into the snapshot API's
shape and runs the normal pipeline, so every validation step still applies.

    python -m tw_stock_digest.backfill --date 2026-10-28 --repo-dir data

Differences from a live run, recorded in the manifest:
- TPEx share volume and turnover exclude odd-lot trades (about 1-2% lower);
- on TWSE ex-rights days the report gives no change; it is derived from the
  exchange's reference price table (TWT49U), or left empty if that fails.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import sys
import time

from .http import HttpClient, RemoteError, persistent_json
from .insights import fetch_extras
from .pipeline import run

URLS = {
    'TWSE': 'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={d:%Y%m%d}&type=ALLBUT0999&response=json',
    'TPEX': 'https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date={d:%Y}%2F{d:%m}%2F{d:%d}&type=EW&response=json',
}
NOTE = ('Backfilled from the per-date reports (TWSE MI_INDEX, TPEx afterTrading/otc). '
        'TPEx volume and turnover exclude odd lots; TWSE ex-rights changes come from the reference price table.')


EX_RIGHTS = 'https://www.twse.com.tw/rwd/zh/exRight/TWT49U?response=json&startDate={d:%Y%m%d}&endDate={d:%Y%m%d}'


class NoData(ValueError):
    """The exchange has no report for this date: not a trading day, or not published yet."""


def roc(day):
    return f'{day.year - 1911}{day:%m%d}'


def plain(text):
    """'1,234.50' -> '1234.50'; leaves placeholders such as '--' untouched."""
    text = str(text).strip()
    return text.replace(',', '') if re.fullmatch(r'[+-]?[\d,]+(\.\d+)?', text) else text


def twse_rows(data, day):
    if not data or data.get('stat') != 'OK':
        raise NoData('TWSE report has no data for this date')
    table = next(t for t in data['tables'] if '證券代號' in (t.get('fields') or []))
    c = {name: i for i, name in enumerate(table['fields'])}
    rows = []
    for r in table['data']:
        sign = re.sub(r'<[^>]+>', '', r[c['漲跌(+/-)']]).strip()
        change = plain(r[c['漲跌價差']])
        change = '' if sign == 'X' else ('-' + change if sign == '-' and change not in ('0', '0.00') else change)
        rows.append(dict(Date=roc(day), Code=r[c['證券代號']].strip(), Name=r[c['證券名稱']].strip(),
                         TradeVolume=plain(r[c['成交股數']]), TradeValue=plain(r[c['成交金額']]),
                         OpeningPrice=plain(r[c['開盤價']]), HighestPrice=plain(r[c['最高價']]),
                         LowestPrice=plain(r[c['最低價']]), ClosingPrice=plain(r[c['收盤價']]),
                         Change=change, Transaction=plain(r[c['成交筆數']])))
    return rows


def tpex_rows(data, day):
    tables = (data or {}).get('tables') or []
    if not tables or not tables[0].get('data'):
        raise NoData('TPEx report has no data for this date')
    fields = [f.strip() for f in tables[0]['fields']]
    c = {name: i for i, name in enumerate(fields)}
    return [dict(Date=roc(day), SecuritiesCompanyCode=r[c['代號']].strip(), CompanyName=r[c['名稱']].strip(),
                 Open=plain(r[c['開盤']]), High=plain(r[c['最高']]), Low=plain(r[c['最低']]),
                 Close=plain(r[c['收盤']]), Change=plain(r[c['漲跌']]), TradingShares=plain(r[c['成交股數']]),
                 TransactionAmount=plain(r[c['成交金額(元)']]), TransactionNumber=plain(r[c['成交筆數']]))
            for r in tables[0]['data']]


def fill_ex_rights(rows, data):
    """Give ex-rights rows their change, from the official reference price table.

    The per-date quote report marks ex-rights days with X and no change. The
    exchange's own reference price (TWT49U) restores it: change = close - reference.
    """
    references = {str(r[1]).strip(): r[4] for r in (data or {}).get('data') or []}
    filled = 0
    for row in rows:
        reference, close = plain(references.get(row['Code'], '')), row['ClosingPrice']
        if row['Change'] == '' and re.fullmatch(r'\d+(\.\d+)?', reference) and re.fullmatch(r'\d+(\.\d+)?', close):
            row['Change'] = f'{float(close) - float(reference):.2f}'
            filled += 1
    return filled


def fetcher(day, *, http=None, sleep=time.sleep):
    http = http or HttpClient(timeout=60)

    def fetch(market):
        data = persistent_json(http, URLS[market].format(d=day), sleep=sleep)
        if market == 'TPEX':
            return tpex_rows(data, day)
        rows = twse_rows(data, day)
        if any(row['Change'] == '' for row in rows):
            try:
                fill_ex_rights(rows, persistent_json(http, EX_RIGHTS.format(d=day), sleep=sleep))
            except RemoteError:
                pass  # the rows keep their missing-change flag
        return rows
    return fetch


def main(argv=None):
    parser = argparse.ArgumentParser(description='用證交所／櫃買中心的逐日報表補回漏掉的交易日')
    parser.add_argument('--date', type=dt.date.fromisoformat, action='append', required=True,
                        help='要補的交易日，可重複指定')
    parser.add_argument('--output', type=Path, default=Path('output'))
    parser.add_argument('--repo-dir', type=Path, default=Path('data'))
    parser.add_argument('--raw-dir', type=Path,
                        help='重建單日時改用該次正式執行保存的 TWSE.json／TPEX.json（Actions artifact 的 raw/）')
    parser.add_argument('--watchlist', default=os.getenv('WATCHLIST', '0050,2330,2317,2454,6488'))
    args = parser.parse_args(argv)
    watchlist = [s.strip().upper() for s in args.watchlist.split(',') if s.strip()]
    if args.raw_dir and len(args.date) != 1:
        parser.error('--raw-dir rebuilds exactly one --date')
    failed = 0
    for day in sorted(args.date):
        try:
            if args.raw_dir:
                # The original snapshot API responses: same data as the live run.
                fetch = lambda market: json.loads((args.raw_dir / f'{market}.json').read_text(encoding='utf-8'))
                options = {}
            else:
                fetch, options = fetcher(day), dict(sources=URLS, source_note=NOTE)
            state = run(day.isoformat(), args.output / 'backfill', watchlist, fetch=fetch,
                        archive_root=args.repo_dir, extras=fetch_extras, **options)
            print(day, state['status'], state.get('reason', ''), state.get('row_count', ''), flush=True)
            failed += state['status'] != 'complete'
        except Exception as exc:  # report and continue with the next day
            print(day, 'failed', type(exc).__name__, exc, flush=True)
            failed += 1
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
