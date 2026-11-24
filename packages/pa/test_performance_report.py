from contextlib import redirect_stdout
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest

from prepare_performance_report import (
    aggregate_bins, bin_intervals, bin_stats, link_speed_bps, port_utilization,
    all_links_down, zombie_fields, zombie_snapshots,
    prepare,
)


def interval(start, end, numerator, denominator, scale=1, base=None):
    return {
        "a": start, "b": end, "numerator": numerator, "denominator": denominator,
        "scale": scale, "base_counter": base,
    }


class AlignmentTests(unittest.TestCase):
    def test_time_overlap_drops_incomplete_edges(self):
        result = bin_intervals([
            interval(10, 310, 600, 300), interval(310, 610, 900, 300),
        ])
        self.assertEqual(set(result), {300})
        self.assertAlmostEqual(result[300]["numerator"], 890)
        self.assertAlmostEqual(result[300]["denominator"], 300)

    def test_latency_is_io_weighted_not_arithmetic(self):
        a = bin_intervals([interval(0, 300, 10000, 10, 0.001, "ops")])
        b = bin_intervals([interval(0, 300, 900000, 90, 0.001, "ops")])
        self.assertAlmostEqual(bin_stats(aggregate_bins([a, b], {0}))["avg"], 9.1)

    def test_parent_peak_is_not_sum_of_member_peaks(self):
        a = bin_intervals([interval(0, 300, 30000, 300), interval(300, 600, 3000, 300)])
        b = bin_intervals([interval(0, 300, 3000, 300), interval(300, 600, 30000, 300)])
        self.assertEqual(bin_stats(aggregate_bins([a, b], {0, 300}))["max"], 110)

    def test_zero_ops_is_not_missing_sampling_coverage(self):
        result = bin_intervals([interval(0, 300, 0, 0, 0.001, "ops")])
        self.assertEqual(set(result), {0})
        summary = bin_stats(aggregate_bins([result], {0}))
        self.assertIsNone(summary["avg"])
        self.assertEqual(summary["seconds"], 300)

    def test_overlap_rejected(self):
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            bin_intervals([interval(0, 300, 1, 300), interval(0, 300, 1, 300)])


class NetworkTests(unittest.TestCase):
    def test_hide_only_consistently_down_ports(self):
        self.assertTrue(all_links_down([{"link_current_state": "down"}]))
        self.assertFalse(all_links_down([]))
        self.assertFalse(all_links_down([
            {"link_current_state": "down"}, {"link_current_state": "up"},
        ]))

    def test_speed_units(self):
        self.assertEqual(link_speed_bps("10000M"), 1e10)
        self.assertEqual(link_speed_bps("100G"), 1e11)
        self.assertIsNone(link_speed_bps("unknown"))

    def test_full_duplex_sum_is_not_clamped_to_100_percent(self):
        rows = bin_intervals([interval(0, 300, 8e9 / 8 * 300, 300)])
        link = [{"timestamp_ms": 0, "link_speed": "10000M", "link_duplex": "full"}]
        value, = port_utilization(rows, rows, link)
        self.assertAlmostEqual(value["rx"], 0.8)
        self.assertAlmostEqual(value["total"], 1.6)

    def test_missing_zero_or_changing_speed_not_fabricated(self):
        rows = bin_intervals([interval(0, 300, 300, 300)])
        for links in [
            [], [{"timestamp_ms": 0, "link_speed": "0M", "link_duplex": "full"}],
            [
                {"timestamp_ms": 0, "link_speed": "10000M", "link_duplex": "full"},
                {"timestamp_ms": 100000, "link_speed": "1000M", "link_duplex": "full"},
            ],
        ]:
            with self.subTest(links=links):
                self.assertEqual(port_utilization(rows, rows, links), [])

class ZombieTests(unittest.TestCase):
    @staticmethod
    def sample(timestamp, value):
        return {"timestamp_ms": timestamp, "value": value, "period_ms": 300000}

    def test_common_asof_uses_real_latest_prior_samples(self):
        asof, selected = zombie_snapshots([
            [self.sample(100000, 10), self.sample(400000, 100)],
            [self.sample(200000, 20), self.sample(500000, 200)],
        ])
        self.assertEqual(asof, 400000)
        self.assertEqual([s["value"] for s in selected], [100, 20])

    def test_missing_and_stale_are_not_zero(self):
        _, selected = zombie_snapshots([
            [self.sample(400000, 10)],
            [self.sample(50000, 20), self.sample(500000, 30)],
            [],
        ])
        self.assertEqual(selected, [self.sample(400000, 10), None, None])
        self.assertIsNone(zombie_fields(None)["zombie_tib"])
        self.assertEqual(zombie_fields(self.sample(1, 0))["zombie_tib"], 0)

    def test_4k_blocks_to_binary_tib(self):
        self.assertEqual(zombie_fields(self.sample(1, 2**28))["zombie_tib"], 1)
        self.assertEqual(zombie_fields(self.sample(1, 1))["zombie_tib"], 4096 / 1024**4)


class BatchReportTests(unittest.TestCase):
    @staticmethod
    def node():
        return {
            "collector_version": 3, "status": "decoded_experimental_not_vendor_calibrated",
            "node": "test-01", "cluster": "test", "cluster_uuid": "cluster-id",
            "node_uuid": "node-id", "metrics": [], "inventory": [],
            "file": "test.7z", "source_sha256": "digest",
            "requested_start_ms": 1794963600000, "requested_end_ms": 1794970800000,
        }

    def test_customer_metadata_and_empty_detail_are_batch_specific(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "batch.json").write_text(json.dumps([self.node()]), encoding="utf-8")
            with gzip.open(root / "test-01.samples.json.gz", "wt", encoding="utf-8") as handle:
                json.dump([], handle)
            with redirect_stdout(io.StringIO()):
                model = prepare(root, root / "model.json", "Test / Site")
            self.assertEqual(model["customer"], "Test / Site")
            self.assertEqual(model["output_filename"], "PA_Report_Test _ Site_202611.xlsx")
            self.assertEqual(model["counts"]["FlexGroup"], 0)
            self.assertIsNone(model["sheets"][0]["rows"][0]["io_avg"])
            info = dict(model["info"])
            self.assertIn("2026-11-18", info["采样日期"])
            self.assertNotIn("2026-11-06", info["采样日期"])

    def test_duplicate_nodes_cannot_silently_mix_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "batch.json").write_text(
                json.dumps([self.node(), self.node()]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate nodes/windows"):
                prepare(root, root / "model.json")


if __name__ == "__main__":
    unittest.main()
