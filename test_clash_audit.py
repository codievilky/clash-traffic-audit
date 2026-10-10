"""Accounting, recovery, retention and maximum-cardinality verification."""
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import clash_audit as a


def flow(cid, tx, rx, host="chat.example.com", start=0, node="ExampleProxy"):
    return {"id": cid, "upload": tx, "download": rx,
            "start": dt.datetime.fromtimestamp(start, a.TZ).isoformat(),
            "metadata": {"host": host}, "chains": [node, "Proxy"]}


def snap(tx, rx, *rows):
    return {"uploadTotal": tx, "downloadTotal": rx, "connections": list(rows)}


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "audit.sqlite3"
        self.store = a.Store(self.path)
        self.now = dt.datetime(2026, 10, 10, 12, tzinfo=a.TZ).timestamp()
        self.acc = a.Accounting(self.store, self.now)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def amount(self, kind, label=None):
        sql = "SELECT COALESCE(SUM(tx),0),COALESCE(SUM(rx),0) FROM daily WHERE kind=?"
        args = [kind]
        if label is not None:
            sql += " AND label=?"
            args.append(label)
        return self.store.db.execute(sql, args).fetchone()

    def test_baseline_deltas_closed_connection_gap_and_restart(self):
        t = self.now
        self.acc.ingest(snap(1000, 2000, flow("old", 100, 200, start=t-30)), "core", t)
        self.acc.ingest(snap(1050, 2090, flow("old", 130, 260, start=t-30), flow("new", 20, 30, start=t+.5)), "core", t+1)
        self.acc.flush(t+1)
        self.assertEqual(self.amount("total"), (50, 90))
        self.assertEqual(self.amount("domain"), (50, 90))
        # Both connections close between samples; totals retain otherwise lost bytes.
        self.acc.ingest(snap(1060, 2140), "core", t+2)
        self.acc.flush(t+2)
        self.assertEqual(self.amount("total"), (60, 140))
        self.assertEqual(self.amount("domain"), (50, 90))
        recovered = a.Accounting(self.store, t+3)
        recovered.ingest(snap(1060, 2140), "core", t+3)
        recovered.flush(t+3)
        self.assertEqual(self.amount("total"), (60, 140))

    def test_kernel_restart_baselines_and_preserves_history(self):
        t = self.now
        self.acc.ingest(snap(500, 900), "a", t)
        self.acc.ingest(snap(600, 1100), "a", t+1)
        self.acc.ingest(snap(50, 80, flow("x", 50, 80, start=t+1.5)), "b", t+2)
        self.acc.ingest(snap(55, 89, flow("x", 55, 89, start=t+1.5)), "b", t+3)
        self.acc.flush(t+3)
        self.assertEqual(self.amount("total"), (105, 209))
        self.assertEqual(self.amount("domain"), (5, 9))
        self.assertEqual(self.acc.health["core_resets"], 1)

    def test_gap_recovery_is_labelled(self):
        t = self.now
        self.acc.ingest(snap(100, 100), "a", t)
        self.acc.ingest(snap(130, 140), "a", t+70)
        self.acc.flush(t+70)
        self.assertEqual(self.amount("total"), (30, 40))
        self.assertEqual(self.acc.health["gaps"][-1]["reason"], "sampling_gap")

    def test_unseen_old_connection_does_not_replay_lifetime(self):
        t = self.now
        self.acc.ingest(snap(10000, 20000), "a", t)
        self.acc.ingest(snap(10001, 20002, flow("late", 9000, 18000, start=t-10)), "a", t+1)
        self.acc.flush(t+1)
        self.assertEqual(self.amount("domain"), (0, 0))
        self.assertEqual(self.amount("total"), (1, 2))

    def test_daily_cap_retains_existing_keys_and_folds_new(self):
        t = self.now
        self.acc.change_day(a.date_at(t), t)
        for i in range(a.LIMITS["domain"] + 30):
            self.acc.add(a.date_at(t), "domain", "proxy", "d%d.example" % i, (1, 2))
        self.acc.add(a.date_at(t), "domain", "proxy", "d0.example", (7, 11))
        self.acc.flush(t)
        self.assertEqual(self.amount("domain", "__other__"), (30, 60))
        self.assertEqual(self.amount("domain", "d0.example"), (8, 13))
        count = self.store.db.execute("SELECT COUNT(*) FROM daily WHERE kind='domain'").fetchone()[0]
        self.assertEqual(count, a.LIMITS["domain"] + 1)

    def test_retention_and_day_boundary(self):
        t = dt.datetime(2026, 10, 10, 23, 59, 59, tzinfo=a.TZ).timestamp()
        self.store.db.execute("INSERT INTO daily VALUES ('2026-09-01','total','all','all',99,99)")
        self.store.db.commit()
        self.acc.ingest(snap(100, 100), "a", t)
        self.acc.ingest(snap(102, 104), "a", t+2)
        self.acc.flush(t+2)
        rows = self.store.db.execute("SELECT day,tx,rx FROM daily WHERE kind='total' ORDER BY day").fetchall()
        self.assertEqual(rows, [('2026-10-10', 0, 0), ('2026-10-11', 2, 4)])

    def test_input_validation_and_routes(self):
        self.assertEqual(a.dimensions(flow('x', 0, 0, node='DIRECT'))[0], 'direct')
        self.assertEqual(a.dimensions(flow('x', 0, 0, node='REJECT-DROP'))[0], 'blocked')
        with self.assertRaises(ValueError):
            self.acc.ingest(snap(-1, 0), 'a', self.now)
        self.assertEqual(a.connection_start('2026-10-10T12:00:00.123456789+08:00'), self.now + .123456)

    def test_report_uses_independent_dimensions(self):
        t = self.now
        self.acc.ingest(snap(0, 0), "a", t)
        self.acc.ingest(snap(10, 20, flow("one", 10, 20, start=t+.5)), "a", t+1)
        self.acc.flush(t+1)
        result = a.report(self.path, {"from": ['2026-10-10'], "to": ['2026-10-10']})
        self.assertEqual(result['totals']['total'], {'tx': 10, 'rx': 20})
        self.assertEqual(len(result['rows']), 1)
        self.assertEqual(result['rows'][0]['label'], 'chat.example.com')

    def test_report_denominator_includes_rows_beyond_export_limit(self):
        with self.store.db:
            self.store.db.executemany('INSERT INTO daily VALUES (?,?,?,?,?,?)',
                ((('2026-10-09' if i < 1500 else '2026-10-10'), 'domain', 'proxy', 'd%d.example' % i, 1, 2) for i in range(3000)))
        result = a.report(self.path, {'from': ['2026-10-09'], 'to': ['2026-10-10']})
        self.assertEqual(len(result['rows']), 2000)
        self.assertEqual(result['matched_totals'], {'tx': 3000, 'rx': 6000})


def capacity_test():
    with tempfile.TemporaryDirectory() as root:
        path = Path(root) / 'capacity.sqlite3'
        store = a.Store(path)
        today = dt.datetime.now(a.TZ).date()
        rows = []
        for d in range(a.RETENTION):
            day = (today-dt.timedelta(days=d)).isoformat()
            for kind, cap in a.LIMITS.items():
                length = 253 if kind == 'domain' else 128 if kind in ('node', 'process') else 10
                for i in range(cap):
                    label = ('%s-%02d-%04d-' % (kind, d, i)).ljust(length, 'x')
                    rows.append((day, kind, a.ROUTES[i % 4], label, 10**12, 10**12))
                for route in a.ROUTES:
                    rows.append((day, kind, route, '__other__', 10**12, 10**12))
        with store.db:
            store.db.executemany('INSERT INTO daily VALUES (?,?,?,?,?,?)', rows)
            store.db.executemany('INSERT INTO active VALUES (?,?,?)', ((str(i).ljust(80,'x'), 10**12, 10**12) for i in range(a.ACTIVE_LIMIT)))
        size = path.stat().st_size
        # Measure a pessimistic rollback journal covering all historical rows.
        store.db.execute('BEGIN IMMEDIATE')
        store.db.execute('UPDATE daily SET tx=tx+1,rx=rx+1')
        journal = Path(str(path)+'-journal')
        journal_size = journal.stat().st_size if journal.exists() else 0
        store.db.rollback()
        store.close()
        result = {'daily_rows': len(rows), 'active_rows': a.ACTIVE_LIMIT, 'database_bytes': size,
                  'all_rows_rollback_journal_bytes': journal_size, 'database_limit_bytes': a.MAX_PAGES*4096,
                  'designed_max_database_plus_journal_and_logs_bytes': a.MAX_PAGES*4096*2+3*1048576,
                  'data_budget_bytes': 128*1048576}
        assert size < a.MAX_PAGES*4096
        assert result['designed_max_database_plus_journal_and_logs_bytes'] < result['data_budget_bytes']
        print(json.dumps(result))


if __name__ == '__main__':
    import sys
    if '--capacity' in sys.argv:
        capacity_test()
    else:
        unittest.main()
