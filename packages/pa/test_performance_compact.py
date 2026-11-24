"""Regression checks for the resource-first report and new source decoding."""

import unittest

from pa_asup_network import describe, purposes
from pa_compact_counters import flatten, rules
from prepare_performance_compact import split, fabricpool_sheet, member_hotspots, site_hotspots
from performance_analysis import DEFAULTS, above


def interval(numerator, scale=1, base=None, a=0, b=300):
    denominator = base if base is not None else b - a
    return dict(a=a, b=b, numerator=numerator, denominator=denominator,
                scale=scale, base_counter="ops" if base is not None else None,
                value=numerator / denominator * scale if denominator else None)


class MixTests(unittest.TestCase):
    def test_rw_excludes_other_and_uses_common_time(self):
        read = [interval(800), interval(9999999, a=300, b=600)]
        write = [interval(200)]
        self.assertEqual(split(read, write), (.8, .2, 5))

    def test_missing_or_zero_is_not_eighty_twenty(self):
        for read, write in [([], []), ([interval(0)], [interval(0)]), ([interval(10)], [])]:
            a, b, _ = split(read, write)
            self.assertIsNone(a)
            self.assertIsNone(b)

    def test_flatten_preserves_large_int_precision(self):
        self.assertEqual(flatten({"stats": {"total": 2**60}, "instance_name": "0_put"}),
                         {"stats.total": 2**60, "instance_name": "0_put"})

    def test_cloud_latency_requires_its_verified_base(self):
        counter = dict(id=7, base_id=6, key=False, ctype=8, array_labels=["total-latency"],
                       size=8, description="")
        schema = {"average_latency": counter, "ops": {**counter, "id": 6, "base_id": 0}}
        self.assertEqual(rules("object_store_client_op_stats", schema)[
            "average_latency.total-latency"]["base"], "ops.total-latency")
        schema["average_latency"]["base_id"] = 999
        self.assertNotIn("average_latency.total-latency", rules("object_store_client_op_stats", schema))


class PurposeTests(unittest.TestCase):
    def test_port_role_does_not_inherit_lif_protocols(self):
        ports = [{"node": "n1", "port": "e1a", "role": "data", "type": "physical", "ifgrp": "a0a*"},
                 {"node": "n1", "port": "e1b", "role": "data", "type": "physical"}]
        lifs = [{"curr_node": "n1", "curr_port": "a0a", "role": "data",
                 "services": ["data-cifs"], "data_protocol": ["cifs"]}]
        groups = [{"node": "n1", "ifgrp-name": "a0a", "ports": ["e1a"]}]
        result = describe(ports, lifs, groups, "n1")
        self.assertEqual(result["e1a"]["role_raw"], "data")
        self.assertEqual(result["e1a"]["role"], "data（数据）")
        self.assertIn("CIFS", result["e1a"]["lif_services"])
        self.assertNotIn("CIFS", result["e1b"]["lif_services"])
        self.assertEqual(result["e1a"]["ifgrp"], "a0a")

    def test_home_lif_is_not_current_placement(self):
        self.assertNotIn("NFS数据", describe(
            [{"node": "n1", "port": "e1a", "role": "data"}],
            [{"curr_node": "n2", "curr_port": "e1a", "home_node": "n1",
              "home_port": "e1a", "data_protocol": ["nfs"]}], [], "n1")["e1a"]["role"])

    def test_mixed_services_cannot_change_cluster_port_role(self):
        result = describe(
            [{"node": "n1", "port": "e0a", "role": "cluster"}],
            [{"curr_node": "n1", "curr_port": "e0a", "services": ["data-cifs", "data-nfs", "intercluster-core"]}],
            [], "n1")["e0a"]
        self.assertEqual(result["role"], "cluster（集群互联）")
        self.assertEqual(describe([{"node": "n1", "port": "e0a"}], [], [], "n1")["e0a"]["role"], "未知")


class RefinedTests(unittest.TestCase):
    def test_default_thresholds_are_strict(self):
        for key, threshold in (("cpu_high_pct", 70), ("latency_watch_ms", 10), ("network_high", .7)):
            self.assertEqual(DEFAULTS[key], threshold)
            rows = [dict(a=0, b=300, value=threshold),
                    dict(a=300, b=600, value=threshold + .001),
                    dict(a=900, b=1200, value=threshold + 1)]
            result = above(rows, DEFAULTS[key])
            self.assertEqual(result["minutes"], 10)
            self.assertEqual(result["longest"], 5)

    def compare(self, iops, ios=None, times=None):
        ios = ios or [0] * len(iops)
        times = list(range(0, 1800, 300)) if times is None else times
        members = [{"resource_id": str(i)} for i in range(len(iops))]
        bins = {str(i): {
            "iops": {t: interval(ops * 300, a=t, b=t + 300) for t in times},
            "read_mib_s": {t: interval(io * 300 * 2**20, a=t, b=t + 300) for t in times},
            "write_mib_s": {t: interval(0, a=t, b=t + 300) for t in times},
        } for i, (ops, io) in enumerate(zip(iops, ios))}
        return member_hotspots(members, bins, set(times), DEFAULTS)

    def test_balanced_members_are_not_hotspots(self):
        rows, result = self.compare([1000, 1000])
        self.assertEqual(result["skew_minutes"], 0)
        self.assertEqual(rows["0"]["peer_iops_ratio"], 1)
        self.assertFalse(rows["0"]["hotspot_marked"])

    def test_two_member_peer_ratio_detects_both_dimensions(self):
        rows, result = self.compare([3000, 1000], [30, 10])
        self.assertEqual(rows["0"]["hotspot"], "IOPS / 吞吐热点")
        self.assertEqual(rows["0"]["peer_iops_ratio"], 3)
        self.assertEqual(rows["0"]["hot_minutes"], 30)
        self.assertEqual(result["skew_minutes"], 30)
        self.assertFalse(rows["1"]["hotspot_marked"])

    def test_low_load_not_marked_even_when_ratio_large(self):
        rows, _ = self.compare([9, 1], [.09, .01])
        self.assertEqual(rows["0"]["hotspot"], "低负载，不判热点")
        self.assertEqual(rows["0"]["peer_iops_ratio"], 9)
        self.assertFalse(rows["0"]["hotspot_marked"])

    def test_dimension_guards_are_independent(self):
        rows, _ = self.compare([500, 500], [1, 0])
        self.assertFalse(rows["0"]["hotspot_marked"])

    def test_zero_peers_and_zero_group_are_distinguished(self):
        rows, _ = self.compare([1000, 0])
        self.assertTrue(rows["0"]["hotspot_marked"])
        self.assertTrue(rows["0"]["hotspot_zero_peers"])
        self.assertIsNone(rows["0"]["peer_iops_ratio"])
        rows, _ = self.compare([0, 0])
        self.assertFalse(rows["0"]["hotspot_marked"])

    def test_short_missing_and_single_member_are_not_hotspots(self):
        for iops, times in [([3000, 0], []), ([3000, 0], [0, 300]), ([3000], list(range(0, 1800, 300)))]:
            rows, _ = self.compare(iops, times=times)
            self.assertFalse(rows["0"]["hotspot_marked"])
            self.assertIsNone(rows["0"]["hot_minutes"])

    def test_gap_breaks_longest_run_and_boundary_is_strict(self):
        rows, _ = self.compare([3000, 1000], times=[0, 300, 600, 1200, 1500, 1800])
        self.assertEqual(rows["0"]["hot_minutes"], 30)
        self.assertEqual(rows["0"]["hot_longest"], 15)
        rows, _ = self.compare([2000, 1000])
        self.assertFalse(rows["0"]["hotspot_marked"])

    def test_rankings_only_keep_site_top_ten(self):
        generated = dict(fields=[dict(key=k) for k in ("scope", "rank", "dimension", "volume", "category")],
                         rows=[dict(scope=s, rank=i, dimension=d) for s in ("本Site", "cluster1")
                               for d in ("IOPS", "吞吐", "延迟") for i in range(1, 13)])
        result = site_hotspots(generated)
        self.assertEqual(len(result["rows"]), 30)
        self.assertEqual(result["rows"][0]["dimension"], "延迟")
        self.assertTrue(all(r["scope"] == "本Site" for r in result["rows"]))
        self.assertNotIn("category", {f["key"] for f in result["fields"]})


class CloudTests(unittest.TestCase):
    def test_put_is_not_confirmed_cold_data_tiering(self):
        class Source:
            batch = [{"node": "n1", "cluster": "c1"}]
            nodes = {"n1": {"cluster": "c1"}}
            series = {
                ("n1", "object_store_client_op_stats", "", "0_put", "stats.total"): [interval(100)],
                ("n1", "object_store_client_op_stats", "", "0_put", "throughput_bytes"): [interval(2**20, 1 / 2**20)],
                ("n1", "object_store_client_op_stats", "", "0_put", "average_latency.total-latency"): [interval(100000, .001, 100)],
            }

            def get(self, node, obj, resource, metric, svm):
                return self.series.get((node, obj, svm, resource, metric), [])

        row = fabricpool_sheet(Source())["rows"][0]
        self.assertAlmostEqual(row["io"], 1 / 300)
        self.assertAlmostEqual(row["iops"], 1 / 3)
        self.assertEqual(row["latency"], 1)
        self.assertIn("未确认", row["scope"])
        self.assertIn("不能单独识别", row["notes"])


if __name__ == "__main__":
    unittest.main()
