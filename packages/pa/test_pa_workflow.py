"""Synthetic workflow boundary tests; no customer archives or Artifact Tool required."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import run_pa_report as workflow
from tools.check_source_privacy import problems


def node(name="test-01", start="2026-12-02T01:00:00+00:00"):
    stamp = int(datetime.fromisoformat(start).timestamp() * 1000)
    return {
        "node": name, "node_uuid": name + "-uuid", "cluster_uuid": "test-cluster",
        "serial_number": "synthetic-serial", "requested_start_ms": stamp,
        "requested_end_ms": stamp + 7200000, "file": name + ".7z",
    }


class WorkflowTests(unittest.TestCase):
    def test_safe_names_and_traversal(self):
        self.assertEqual(workflow.leaf("Site A & B"), "Site A & B")
        for value in ("", "..", "../other", "other\\file", "COM1", "NUL.txt", "site.", "site "):
            with self.subTest(value=value), self.assertRaises(ValueError):
                workflow.leaf(value)

    def test_month_comes_from_sampling_not_current_date(self):
        self.assertEqual(workflow.sampling_month([node()]), "202612")
        # A UTC November timestamp in Shanghai December belongs to December.
        self.assertEqual(workflow.sampling_month([node(start="2026-11-30T18:00:00+00:00")]), "202612")

    def test_mixed_month_and_invalid_windows_rejected(self):
        with self.assertRaises(ValueError):
            workflow.sampling_month([node(), node(start="2027-01-02T01:00:00+00:00")])
        value = node()
        value["requested_end_ms"] = value["requested_start_ms"]
        with self.assertRaises(ValueError):
            workflow.sampling_month([value])

    def test_inventory_deduplicates_exact_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.7z").write_bytes(b"same synthetic bytes")
            (root / "two.7z").write_bytes(b"same synthetic bytes")
            with patch.object(workflow, "headers", return_value=node()):
                selected, duplicates = workflow.inventory(root)
            self.assertEqual(len(selected), 1)
            self.assertEqual(len(duplicates), 1)

    def test_inventory_rejects_two_windows_same_node(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.7z").write_bytes(b"window one")
            (root / "two.7z").write_bytes(b"window two")
            with patch.object(workflow, "headers", return_value=node()), self.assertRaises(ValueError):
                workflow.inventory(root)

    def test_empty_input_fails(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            workflow.inventory(Path(directory))

    def test_baseline_cache_identity_must_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache/data"
            cache.mkdir(parents=True)
            value = {**node(), "collector_version": workflow.COLLECTOR_VERSION, "source_sha256": "sha"}
            workflow.save(cache / "test-01.json", {**value, "cluster_uuid": "wrong"})
            (cache / "test-01.samples.json.gz").write_bytes(b"cached")
            with patch.object(workflow, "parse_archive", return_value=value) as decode:
                batch = workflow.baseline_data(
                    {"sha": (root / "input.7z", node())}, root / "new/data", root / "cache")
            decode.assert_called_once()
            self.assertEqual(batch[0]["cluster_uuid"], "test-cluster")

    def test_baseline_matching_cache_reused_without_decode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache/data"
            cache.mkdir(parents=True)
            value = {**node(), "collector_version": workflow.COLLECTOR_VERSION, "source_sha256": "sha"}
            workflow.save(cache / "test-01.json", value)
            (cache / "test-01.samples.json.gz").write_bytes(b"cached")
            with patch.object(workflow, "parse_archive") as decode:
                workflow.baseline_data(
                    {"sha": (root / "input.7z", node())}, root / "new/data", root / "cache")
            decode.assert_not_called()
            self.assertEqual((root / "new/data/test-01.samples.json.gz").read_bytes(), b"cached")

    def report(self, root):
        report = root / "run/report"
        report.mkdir(parents=True)
        source = report / "PA_Report_Test_202612_first.xlsx"
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("synthetic-package-marker", "not a real customer workbook")
        workflow.save(report / "report_model.json", {
            "model_version": "performance-report-v6-refined", "customer": "Test",
            "output_filename": source.name,
        })
        workflow.save(report / "verification_compact.json", {
            "formula_errors": 0, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        })
        return report, source

    def test_publish_preserves_prior_file_and_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report, source = self.report(root)
            result = workflow.publish(report, root / "published", {"version": "test"})
            self.assertEqual(result.read_bytes(), source.read_bytes())
            self.assertTrue(result.with_suffix(".run.json").is_file())
            self.assertTrue(result.with_suffix(".verification.json").is_file())
            with self.assertRaises(ValueError):
                workflow.publish(report, root / "published", {})
            self.assertEqual(result.read_bytes(), source.read_bytes())

    def test_tampered_workbook_cannot_be_published(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report, source = self.report(root)
            source.write_bytes(b"changed")
            with self.assertRaises(ValueError):
                workflow.publish(report, root / "published", {})
            self.assertFalse((root / "published").exists())

    def test_publication_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report, _ = self.report(root)
            model = workflow.load(report / "report_model.json")
            model["output_filename"] = "../other.xlsx"
            workflow.save(report / "report_model.json", model)
            with self.assertRaises(ValueError):
                workflow.verified_package(report)

    def test_privacy_preflight_rejects_data_and_profile_paths(self):
        self.assertTrue(problems("config/project.local.json", "{}"))
        self.assertTrue(problems("customer.xlsx", ""))
        profile = "C:" + "/Users/" + "Example/private-data"
        self.assertIn("personal Windows profile path", problems("README.md", profile))
        self.assertEqual(problems("config/project.example.json", '{"reports_root":"D:/Private/Reports"}'), [])

    def test_focused_cache_checks_changed_source_before_reuse(self):
        import pa_compact_counters
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow.save(root / "expected/audit.json", {"compact_version": pa_compact_counters.VERSION})
            with patch.object(pa_compact_counters, "source_digest", return_value="changed"):
                with self.assertRaisesRegex(ValueError, "Source changed"):
                    pa_compact_counters.collect(root / "input.7z", {"source_sha256": "expected"}, root)

    def test_managed_worker_requires_hashed_completion_before_termination(self):
        from run_performance_v2 import managed_export
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code = (
                "import pathlib,hashlib,json,time,sys;"
                "p=pathlib.Path(sys.argv[1]);"
                "(p/'result.bin').write_bytes(b'complete');"
                "(p/'export_complete.json').write_text(json.dumps({"
                "'filename':'result.bin','sha256':hashlib.sha256(b'complete').hexdigest()}));"
                "print('SAVED',flush=True);time.sleep(30)"
            )
            result = managed_export([sys.executable, "-c", code, str(root)], root, timeout=10)
            self.assertIn("SAVED", result.stdout)
            self.assertTrue(workflow.load(root / "worker_status.json")["terminated_by_parent_after_export"])

    def test_managed_worker_rejects_unexpected_exit(self):
        from run_performance_v2 import managed_export
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "exited before"):
                managed_export([sys.executable, "-c", "raise SystemExit(9)"], root, timeout=5)
            self.assertFalse(workflow.load(root / "worker_status.json")["terminated_by_parent_after_export"])

    def test_managed_worker_timeout_stops_child(self):
        from run_performance_v2 import managed_export
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(TimeoutError):
                managed_export([sys.executable, "-c", "import time;time.sleep(30)"], root, timeout=.1)
            self.assertFalse(workflow.load(root / "worker_status.json")["terminated_by_parent_after_export"])


if __name__ == "__main__":
    unittest.main()
