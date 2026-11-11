import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from test_market import tpex, twse
from tw_stock_digest import backfill
from tw_stock_digest.daily import TAIPEI, collect_pending, exit_code, pending_days
from tw_stock_digest.http import RemoteError
from tw_stock_digest.market import SOURCES, normalize


def roc(day):
    return f'{day.year - 1911}{day:%m%d}'


def at(text):
    return dt.datetime.fromisoformat(text).replace(tzinfo=TAIPEI)


WED, THU, FRI, MON = (dt.date(2026, 10, d) for d in (21, 22, 23, 26))
# Weekdays before WED that are still inside a late-October run's 7-day window.
EARLIER = [dt.date(2026, 10, d) for d in (15, 16, 19, 20)]


class PendingDaysTests(unittest.TestCase):
    def latest(self, day, **fields):
        return {**dict(trade_date=day.isoformat(), snapshot_status='complete', extras_complete=True), **fields}

    def test_today_waits_until_reports_can_exist(self):
        saved = {d.isoformat() for d in EARLIER + [WED]}
        self.assertEqual(pending_days(saved, self.latest(WED), at('2026-10-22T13:00')), [])
        self.assertEqual(pending_days(saved, self.latest(WED), at('2026-10-22T18:00')), [THU])

    def test_missed_days_come_first_and_weekends_are_not_asked_for(self):
        saved = {dt.date(2026, 10, 19).isoformat(), dt.date(2026, 10, 20).isoformat()}
        days = pending_days(saved, self.latest(dt.date(2026, 10, 20)), at('2026-10-25T09:00'))  # Sunday
        self.assertEqual(days, [WED, THU, FRI])

    def test_incomplete_latest_day_is_collected_again(self):
        saved = {d.isoformat() for d in EARLIER + [WED, THU]}
        latest = self.latest(THU, extras_complete=False)
        self.assertEqual(pending_days(saved, latest, at('2026-10-22T21:00')), [THU])
        self.assertEqual(pending_days(saved, self.latest(THU), at('2026-10-22T21:00')), [])

    def test_snapshot_day_outside_weekdays_is_included_unless_older_than_saved(self):
        saturday = dt.date(2026, 10, 24)
        saved = {d.isoformat() for d in (dt.date(2026, 10, 19), dt.date(2026, 10, 20), WED, THU, FRI)}
        days = pending_days(saved, self.latest(FRI), at('2026-10-25T09:00'), [saturday, THU])
        self.assertEqual(days, [saturday])


class CollectPendingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output, self.archive = self.root / 'output', self.root / 'data'
        self.calls = []

    def rows(self, market, day, **changes):
        return [twse(Date=roc(day), **changes)] if market == 'TWSE' else [tpex(Date=roc(day), **changes)]

    def sources(self, snapshot_days, trading, unreachable=()):
        def snapshot(market):
            day = snapshot_days[market]
            if day is None:
                raise RemoteError('snapshot.example')
            return self.rows(market, day)

        def report(market, day):
            self.calls.append((market, day))
            if (market, day) in unreachable:
                raise RemoteError('report.example')
            if day not in trading:
                raise backfill.NoData('no data')
            return self.rows(market, day)
        return dict(snapshot=snapshot, report=report)

    def collect(self, now, **sources):
        return collect_pending(self.output, ['2330'], archive_root=self.archive, now=at(now), **sources)

    def seed(self, *days):
        for day in days:
            collect_pending(self.output, ['2330'], archive_root=self.archive, only=day,
                            snapshot=lambda market, day=day: self.rows(market, day), report=None)

    def saved(self):
        return sorted(p.name for p in (self.archive / 'daily').iterdir())

    def test_evening_run_uses_twse_per_date_report_when_its_snapshot_lags(self):
        self.seed(*EARLIER, WED)
        summary = self.collect('2026-10-22T19:30', **self.sources({'TWSE': WED, 'TPEX': THU}, {THU}))
        (day,) = summary['days']
        self.assertEqual((day['trade_date'], day['status']), ('2026-10-22', 'complete'))
        self.assertEqual(self.calls, [('TWSE', THU)])  # TPEx came from its snapshot
        manifest = json.loads((self.archive / 'daily/2026-10-22/manifest.json').read_text())
        self.assertEqual(manifest['sources']['TWSE'], backfill.URLS['TWSE'].format(d=THU))
        self.assertEqual(manifest['sources']['TPEX'], SOURCES['TPEX'])
        self.assertIn('per-date report', manifest['source_note'])
        self.assertEqual(json.loads((self.archive / 'latest.json').read_text())['trade_date'], '2026-10-22')
        self.assertEqual(json.loads((self.output / 'run.json').read_text())['days'][0]['status'], 'complete')
        self.assertEqual(exit_code(summary), 0)

    def test_morning_run_uses_snapshots_when_both_describe_the_day(self):
        self.seed(*EARLIER, WED)
        summary = self.collect('2026-10-23T08:20', **self.sources({'TWSE': THU, 'TPEX': THU}, {THU}))
        self.assertEqual([d['status'] for d in summary['days']], ['complete'])
        self.assertEqual(self.calls, [])
        manifest = json.loads((self.archive / 'daily/2026-10-22/manifest.json').read_text())
        self.assertNotIn('source_note', manifest)

    def test_missed_day_is_recovered_before_the_newest_day(self):
        self.seed(*EARLIER)
        summary = self.collect('2026-10-22T19:30', **self.sources({'TWSE': WED, 'TPEX': THU}, {WED, THU}))
        self.assertEqual([(d['trade_date'], d['status']) for d in summary['days']],
                         [('2026-10-21', 'complete'), ('2026-10-22', 'complete')])
        self.assertEqual(self.saved()[-2:], ['2026-10-21', '2026-10-22'])
        self.assertEqual(json.loads((self.archive / 'latest.json').read_text())['trade_date'], '2026-10-22')

    def test_closure_and_unpublished_today_are_skipped_not_failed(self):
        self.seed(*EARLIER, WED, THU)
        # Friday was a typhoon closure; Monday's reports are not out yet.
        summary = self.collect('2026-10-26T15:00', **self.sources({'TWSE': THU, 'TPEX': THU}, set()))
        self.assertEqual([(d['trade_date'], d['status'], d['reason']) for d in summary['days']],
                         [('2026-10-23', 'skipped', 'no_trading'), ('2026-10-26', 'skipped', 'not_published')])
        self.assertEqual(self.calls, [('TWSE', FRI), ('TWSE', MON)])
        self.assertEqual(exit_code(summary), 0)
        self.assertEqual(self.saved()[-1], '2026-10-22')

    def test_unreachable_source_is_retried_later_and_does_not_block_other_days(self):
        self.seed(*EARLIER)
        summary = self.collect('2026-10-22T19:30', **self.sources(
            {'TWSE': WED, 'TPEX': THU}, {WED, THU}, unreachable={('TPEX', WED)}))
        self.assertEqual([(d['trade_date'], d['status'], d.get('reason')) for d in summary['days']],
                         [('2026-10-21', 'failed', 'source_unreachable'), ('2026-10-22', 'complete', None)])
        self.assertEqual(exit_code(summary), 0)
        # The next run finds the hole and fills it without moving latest back.
        summary = self.collect('2026-10-22T21:30', **self.sources({'TWSE': WED, 'TPEX': THU}, {WED, THU}))
        self.assertEqual([(d['trade_date'], d['status']) for d in summary['days']], [('2026-10-21', 'complete')])
        self.assertEqual(json.loads((self.archive / 'latest.json').read_text())['trade_date'], '2026-10-22')

    def test_snapshot_outage_falls_back_to_per_date_reports(self):
        self.seed(*EARLIER, WED)
        summary = self.collect('2026-10-22T19:30', **self.sources({'TWSE': None, 'TPEX': None}, {THU}))
        self.assertEqual([d['status'] for d in summary['days']], ['complete'])
        self.assertEqual(set(summary['snapshot_notes']), {'TWSE', 'TPEX'})
        self.assertEqual(self.calls, [('TWSE', THU), ('TPEX', THU)])

    def test_pipeline_error_for_one_day_is_reported_and_later_days_continue(self):
        self.seed(*EARLIER)
        sources = self.sources({'TWSE': WED, 'TPEX': THU}, {WED, THU})
        good = sources['report']

        def report(market, day):
            rows = good(market, day)
            if day == WED and market == 'TPEX':
                rows.append(tpex('6488', Date=roc(WED)))  # duplicate symbol: structural error
            return rows
        summary = self.collect('2026-10-22T19:30', snapshot=sources['snapshot'], report=report)
        first, second = summary['days']
        self.assertEqual((first['status'], first['error_type'], first['stage']), ('failed', 'ValueError', 'collect'))
        self.assertEqual(second['status'], 'complete')
        self.assertEqual(exit_code(summary), 1)

    def test_scheduled_holidays_are_not_asked_for_again(self):
        self.seed(*EARLIER, WED, THU)
        sources = self.sources({'TWSE': THU, 'TPEX': THU}, set())
        summary = self.collect('2026-10-27T08:20', holidays=lambda: {MON}, **sources)
        # Friday (typhoon, unscheduled) is still checked; Monday (scheduled holiday) is not.
        self.assertEqual([(d['trade_date'], d['reason']) for d in summary['days']], [('2026-10-23', 'no_trading')])
        self.assertEqual(self.calls, [('TWSE', FRI)])

        def unavailable():
            raise RemoteError('openapi.example')
        self.calls.clear()
        summary = self.collect('2026-10-27T08:20', holidays=unavailable, **sources)
        self.assertEqual(self.calls, [('TWSE', FRI), ('TWSE', MON)])

    def test_holiday_schedule_keeps_first_and_last_trading_days_open(self):
        from tw_stock_digest.daily import closed_days
        rows = [dict(Date='1150211', Name='農曆春節前最後交易日'), dict(Date='1150212', Name='市場無交易，僅辦理結算交割作業'),
                dict(Date='1150216', Name='農曆除夕及春節'), dict(Date='1150223', Name='農曆春節後開始交易日')]
        self.assertEqual(closed_days(rows), {dt.date(2026, 2, 12), dt.date(2026, 2, 16)})

    def test_nothing_pending(self):
        self.seed(*EARLIER, WED, THU)
        summary = self.collect('2026-10-22T23:00', **self.sources({'TWSE': WED, 'TPEX': THU}, {THU}))
        self.assertEqual(summary['days'], [])
        self.assertEqual(self.calls, [])


class RowToleranceTests(unittest.TestCase):
    def test_one_bad_security_is_left_out_and_recorded(self):
        rows = [twse('2330'), twse('2317', HighestPrice='90'), twse('2454', ClosingPrice='abc')]
        with self.assertRaises(ValueError):
            normalize('TWSE', rows)
        rejected = []
        quotes = normalize('TWSE', rows, rejected)
        self.assertEqual([q['symbol'] for q in quotes], ['2330'])
        self.assertEqual(rejected, [('TWSE', '2317', 'inconsistent_ohlc'), ('TWSE', '2454', 'invalid_number')])

    def test_many_bad_rows_still_fail(self):
        rows = [twse('2330')] + [twse(str(3000 + i), HighestPrice='90') for i in range(4)]
        with self.assertRaisesRegex(ValueError, '4 securities failed validation'):
            normalize('TWSE', rows, [])

    def test_rejected_rows_are_visible_in_the_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            day = dt.date(2026, 10, 21)

            def snapshot(market):
                return [twse(), twse('2317', HighestPrice='90')] if market == 'TWSE' else [tpex()]
            summary = collect_pending(root / 'output', ['2330'], archive_root=root / 'data', only=day,
                                      snapshot=snapshot, report=None)
            self.assertEqual(summary['days'][0]['rejected_rows'], [['TWSE', '2317', 'inconsistent_ohlc']])
            manifest = json.loads((root / 'data/daily/2026-10-21/manifest.json').read_text())
            self.assertEqual(manifest['rejected_rows'], [['TWSE', '2317', 'inconsistent_ohlc']])
            self.assertIn('TWSE 2317', (root / 'data/daily/2026-10-21/facts.md').read_text())


class ExRightsTests(unittest.TestCase):
    def test_change_is_restored_from_the_reference_price(self):
        rows = [dict(Code='0056', ClosingPrice='57.50', Change=''),
                dict(Code='2330', ClosingPrice='1000.00', Change='5.00'),
                dict(Code='1101', ClosingPrice='30.00', Change='')]
        table = {'stat': 'OK', 'data': [['115年10月22日', '0056', '元大高股息', '59.30', '57.03', '2.27', '息']]}
        self.assertEqual(backfill.fill_ex_rights(rows, table), 1)
        self.assertEqual([r['Change'] for r in rows], ['0.47', '5.00', ''])
        self.assertEqual(backfill.fill_ex_rights(rows, {'stat': '查無資料'}), 0)
