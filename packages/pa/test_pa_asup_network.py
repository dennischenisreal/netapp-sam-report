"""Synthetic ASUP identity, cache and IFGRP alias checks."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from pa_asup_network import collect


class CollectTests(unittest.TestCase):
    header = (
        "X-Netapp-asup-generated-on: Fri Nov 27 00:00:00 +0000 2026\n"
        "X-Netapp-asup-hostname: demo-node\n"
        "X-Netapp-asup-serial-num: demo-serial\n"
        "X-Netapp-asup-cluster-uuid: demo-cluster\n"
    ).encode()

    @staticmethod
    def xml(body, serial="demo-serial"):
        return (f'<T node_sn="{serial}" cluster_uuid="demo-cluster">{body}</T>').encode()

    def fixture(self, source, output, bad_identity=False):
        (source / "demo.7z").touch()
        old = output / ("a" * 64 + ".v4.json")
        output.mkdir()
        old.write_text(json.dumps({"e1a": {"role": "stale"}}), encoding="utf-8")
        payload = {
            "X-HEADER-DATA.TXT": self.header,
            "network-ports.xml": self.xml(
                "<ROW><node>demo-node</node><port>e1a</port><role>data</role></ROW>",
                "wrong-serial" if bad_identity else "demo-serial"),
            "network-interface.xml": self.xml(
                "<ROW><curr_node>demo-node</curr_node><curr_port>a0a</curr_port>"
                "<role>intercluster</role></ROW>"),
            "ifgrps.xml": b"",
            "ifgrp.xml": self.xml(
                "<ROW><node>demo-node</node><ifgrp-name>a0a</ifgrp-name>"
                "<ports><li>e1a</li></ports></ROW>"),
        }
        info = {"node": "demo-node", "serial_number": "demo-serial",
                "cluster_uuid": "demo-cluster", "requested_end_ms": 1795737600000}
        return payload, info

    def test_old_cache_is_ignored_and_singular_ifgrp_alias_is_read(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            payload, info = self.fixture(root, root / "out")
            with patch("pa_asup_network.read_member", side_effect=lambda _, name: payload[name]), patch(
                    "pa_asup_network.source_digest", return_value="a" * 64):
                result = collect([info], root, root / "out")
            row = result["ports"]["demo-node"]["e1a"]
            self.assertEqual(row["purpose_roles"], ["intercluster"])
            self.assertEqual(row["role_raw"], "data")
            self.assertTrue((root / "out" / ("a" * 64 + ".v5-purpose.json")).is_file())

    def test_wrong_xml_identity_cannot_use_matching_filename(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            payload, info = self.fixture(root, root / "out", bad_identity=True)
            with patch("pa_asup_network.read_member", side_effect=lambda _, name: payload[name]), patch(
                    "pa_asup_network.source_digest", return_value="a" * 64):
                with self.assertRaisesRegex(ValueError, "identity mismatch"):
                    collect([info], root, root / "out")

    def test_wrong_cluster_does_not_match_node_name(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            payload, info = self.fixture(root, root / "out")
            info["cluster_uuid"] = "another-cluster"
            with patch("pa_asup_network.read_member", side_effect=lambda _, name: payload[name]):
                result = collect([info], root, root / "out")
            self.assertEqual(result["ports"], {})


if __name__ == "__main__":
    unittest.main()
