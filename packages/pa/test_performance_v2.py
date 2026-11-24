import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from pa_capabilities import differences, rule
from performance_analysis import METRIC_VERSION, above, longest, overlap, p95, span, thresholds
from performance_history import comparable, connect, consecutive_months, digest, trend_sheets


def row(a, b, value):
    return {"a": a, "b": b, "value": value}


class WindowTests(unittest.TestCase):
    def test_threshold_is_strict_and_gaps_break_runs(self):
        src = [row(0, 60, 70), row(60, 120, 85), row(180, 240, 90)]
        actual = above(src, 70)
        self.assertEqual(actual["minutes"], 2)
        self.assertEqual(actual["longest"], 1)
        self.assertAlmostEqual(actual["share"], 2 / 3)

    def test_null_latency_is_not_low_latency(self):
        self.assertIsNone(above([row(0, 60, None)], 5)["share"])
        self.assertEqual(span([row(0, 60, None)]), 60)

    def test_interval_p95_is_time_weighted_not_io_weighted(self):
        self.assertEqual(p95([row(0, 96, 1), row(96, 100, 100)]), 1)
        self.assertEqual(p95([row(0, 94, 1), row(94, 100, 100)]), 100)

    def test_native_intersection_never_adds_samples(self):
        actual = overlap([row(0, 60, 80), row(60, 120, 90)], [row(30, 90, 8)])
        self.assertEqual([(r["a"], r["b"]) for r in actual], [(30, 60), (60, 90)])
        self.assertEqual(span(actual), 60)

    def test_overlap_cannot_double_count_coverage(self):
        with self.assertRaises(ValueError):
            span([row(0, 60, 1), row(30, 90, 1)])

    def test_5_gbps_is_not_5_gib_per_second(self):
        self.assertAlmostEqual(thresholds({}, "a")["throughput_high_mib_s"], 596.0464477539062)

    def test_platform_and_cluster_overrides(self):
        cfg = {"platforms": {"AFF-A1K": {"iops_high": 200}}, "clusters": {"a": {"iops_high": 300}}}
        self.assertEqual(thresholds(cfg, "a", "AFF-A1K")["iops_high"], 300)


class SupplementalTests(unittest.TestCase):
    def samples(self):
        return [
            {"timestamp_ms": t, "raw_value": v, "raw_base": None, "period_ms": 60000,
             "object": "nic_common", "resource": "e0a", "metric": "rx_errors",
             "source_counter": "rx_errors", "base_counter": None, "scale": 1,
             "unit": "count/s", "group": "kernel/opm"}
            for t, v in [(0, 10), (60000, 14), (120000, 2), (180000, 5)]
        ]

    def test_reset_is_excluded_not_wrap_guessed(self):
        rows, rejected = differences(self.samples(), {"requested_start_ms": 0, "requested_end_ms": 180000, "node": "n", "cluster": "c"})
        self.assertEqual(rejected["reset_or_wrap"], 1)
        self.assertEqual(sum(r["numerator"] for r in rows), 7)

    def test_conflicting_boundary_samples_are_not_silently_deduplicated(self):
        rows, rejected = differences(self.samples() + [{**self.samples()[1], "raw_value": 999}], {"requested_start_ms": 0, "requested_end_ms": 180000})
        self.assertFalse(rows)
        self.assertEqual(rejected["identity_or_sample_conflicts"], 1)

    def test_waits_without_denominator_stay_raw(self):
        spec = rule("workload_queue_dblade", "delay_disk_io_wait_time", {
            "delay_disk_io_wait_time": {"description": "Units are microseconds."}})
        self.assertEqual(spec["mode"], "raw")
        self.assertEqual(spec["unit"], "us(raw)")

    def test_latency_dependency_drift_is_unsupported(self):
        defs = {"other_latency": {"base_id": 99}, "other_ops": {"id": 10}}
        self.assertIsNone(rule("volume", "other_latency", defs))


class HistoryTests(unittest.TestCase):
    def sample(self):
        return {
            "metric_version": "v2", "resource_id": "uuid", "cluster": "a", "coverage": .98,
            "value": 20, "context": {
                "config_hash": "x", "schema_hash": "same", "ontap": ["9.15.1P12"],
                "cluster_nodes": ["n1", "n2"], "members": [], "location": ["n1", "v1"],
                "duration": 7200, "hour": 10, "expected_complete": True,
            },
        }

    def test_same_identity_compatible_window(self):
        self.assertEqual(comparable(self.sample(), self.sample(), {}), [])

    def test_unknown_expected_inventory_is_not_full_coverage(self):
        other = self.sample()
        other["context"]["expected_complete"] = None
        self.assertIn("期望节点清单未确认完整", comparable(self.sample(), other, {}))

    def test_identity_members_versions_location_and_window_are_checked(self):
        other = self.sample()
        other["resource_id"] = "different"
        other["context"].update(hour=2, duration=1200, members=["new"], ontap=["9.17"], location=["n2", "v2"])
        self.assertGreaterEqual(len(comparable(self.sample(), other, {})), 6)

    def test_short_gaps_same_month_are_not_three_months(self):
        self.assertFalse(consecutive_months(["2026-09", "2026-09", "2026-11"]))
        self.assertFalse(consecutive_months(["2026-07", "2026-09", "2026-11"]))
        self.assertTrue(consecutive_months(["2026-11", "2026-12", "2027-01"]))

    def test_database_is_versioned_and_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "history.sqlite"
            db = connect(p)
            with db:
                for _ in range(2):
                    db.execute("INSERT OR IGNORE INTO files VALUES(?,?,?,?)", ("sha", "pa.7z", "n1", "{}"))
                db.execute("INSERT INTO batches VALUES(?,?,?,?,?,?)", ("batch", "site", 0, 1, "2026-11", "[]"))
                for version in ["v1", "v2"]:
                    db.execute("INSERT INTO runs VALUES(?,?,?,?,?)", ("batch", version, "cfg", "p", "{}"))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM files").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 2)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
            db.close()
            reopened = connect(p)
            self.assertEqual(reopened.execute("SELECT COUNT(*) FROM files").fetchone()[0], 1)
            reopened.close()

    def test_config_digest_is_order_independent(self):
        self.assertEqual(digest({"a": 1, "b": 2}), digest({"b": 2, "a": 1}))

    def test_trend_requires_three_real_comparable_months_and_zero_base_is_not_infinite(self):
        with tempfile.TemporaryDirectory() as d:
            db = connect(Path(d) / "h.sqlite")
            tz = timezone(timedelta(hours=8))
            current = None
            for month, value in [(9, 90), (10, 0), (11, 95)]:
                start = datetime(2026, month, 18, 10, tzinfo=tz)
                end = start + timedelta(hours=2)
                stamp = int(start.timestamp() * 1000)
                batch = f"batch-{month}"
                r = self.sample()
                r.update(kind="Node", node="n", svm="", resource="n", metric="cpu_busy_pct", unit="%", value=value)
                r["context"].update(start_iso=start.isoformat(), end_iso=end.isoformat(), config_hash="cfg")
                r["metric_version"] = METRIC_VERSION
                with db:
                    db.execute("INSERT INTO batches VALUES(?,?,?,?,?,?)", (batch, "site", stamp, stamp + 7200000, f"2026-{month:02}", "[]"))
                    db.execute("INSERT INTO snapshots VALUES(" + ",".join("?" for _ in range(15)) + ")", (
                        batch, r["metric_version"], "cfg", r["resource_id"], "Node", "a", "n", "", "n",
                        r["metric"], value, "%", .98, "VALID", json.dumps(r["context"])))
                current = r
            analysis = SimpleNamespace(model={"customer": "site"}, config={})
            sheets = trend_sheets(db, analysis, "batch-11", "cfg", [current])
            result = sheets[0]["rows"][0]
            self.assertEqual(result["previous"], 0)
            self.assertIsNone(result["relative"])
            self.assertEqual(result["delta"], 95)
            self.assertEqual(sheets[1]["rows"][0]["count"], 3)
            self.assertEqual(sheets[1]["rows"][0]["status"], "VALID")
            # A changed expected-node validation blocks every past comparison.
            current["context"]["expected_complete"] = None
            sheets = trend_sheets(db, analysis, "batch-11", "cfg", [current])
            self.assertEqual(sheets[0]["rows"][0]["status"], "NOT_COMPARABLE")
            self.assertEqual(sheets[1]["rows"][0]["status"], "NOT_COMPARABLE")
            db.close()


if __name__ == "__main__":
    unittest.main()
