"""Portable distribution tests with fictional source trees and temporary repositories."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools import distribution as dist


def sources():
    return {"VERSION": b"0.1.1\n", "README.md": b"Synthetic PA package\n",
            "skills/netapp-pa-report/SKILL.md": b"---\nname: netapp-pa-report\ndescription: test\n---\n"}


class DistributionTests(unittest.TestCase):
    def test_archive_is_deterministic_and_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(dist, "reviewed_sources", return_value=sources()), redirect_stdout(io.StringIO()):
                a = dist.build(root / "one")
                b = dist.build(root / "two")
            self.assertEqual(a.read_bytes(), b.read_bytes())
            self.assertEqual(dist.verify(a)["source_files"], 3)
            self.assertEqual(dist.verify(a)["version"], "0.1.1")
            with zipfile.ZipFile(a) as stream:
                self.assertEqual(len(stream.namelist()), 4)

    def test_inventory_rejects_changed_or_unexpected_data(self):
        files = sources()
        manifest = dist.inventory(files)
        with self.assertRaises(ValueError):
            dist.validate_payload({**files, "README.md": b"changed"}, manifest)
        with self.assertRaises(ValueError):
            dist.validate_payload({**files, "config/project.local.json": b"{}"}, manifest)

    def test_local_config_remains_forbidden_even_if_in_inventory(self):
        files = {**sources(), "config/project.local.json": b"{}"}
        with self.assertRaisesRegex(ValueError, "Private or unexpected"):
            dist.validate_payload(files, dist.inventory(files))

    def test_unsafe_paths_rejected(self):
        for name in ("../data", "/data", "C:/data", "a\\b", "a/./b", "a/../b", "folder./data"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                dist.safe_name(name)

    def test_archive_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as stream:
                stream.writestr("netapp-pa-report/../private", b"bad")
            with self.assertRaises(ValueError):
                dist.verify(archive)

    def test_inventory_case_collision_rejected(self):
        files = {**sources(), "readme.md": b"duplicate case"}
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            dist.validate_payload(files, dist.inventory(files))

    def suite(self, directory):
        root = Path(directory) / "suite"
        root.mkdir()
        self.git(root, "init", "-b", "main")
        (root / "README.md").write_text("Synthetic suite", encoding="utf-8")
        self.git(root, "add", "README.md")
        self.git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-m", "fixture")
        base = self.git(root, "rev-parse", "HEAD").strip()
        self.git(root, "switch", "-c", "codex/pa-test")
        return root, base

    @staticmethod
    def git(root, *args):
        return subprocess.check_output(["git", *args], cwd=root, text=True, stderr=subprocess.PIPE)

    def test_stage_preview_and_apply_are_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            suite, base = self.suite(directory)
            with patch.object(dist, "reviewed_sources", return_value=sources()), redirect_stdout(io.StringIO()):
                dist.stage_suite(suite, base)
                self.assertFalse((suite / "packages").exists())
                dist.stage_suite(suite, base, True)
                dist.stage_suite(suite, base, True)
            self.assertEqual((suite / "README.md").read_text(), "Synthetic suite")
            self.assertEqual((suite / "packages/pa/VERSION").read_text().strip(), "0.1.1")
            self.assertTrue((suite / "packages/pa/source-inventory.json").is_file())

    def test_independent_target_changes_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            suite, base = self.suite(directory)
            with patch.object(dist, "reviewed_sources", return_value=sources()), redirect_stdout(io.StringIO()):
                dist.stage_suite(suite, base, True)
                target = suite / "packages/pa/README.md"
                target.write_text("independent edit", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Uncommitted mirror differs"):
                    dist.stage_suite(suite, base, True)
            self.assertEqual(target.read_text(), "independent edit")

    def test_default_branch_and_wrong_base_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            suite, base = self.suite(directory)
            with self.assertRaises(ValueError):
                dist.stage_suite(suite, "0" * 40)
            self.git(suite, "switch", "main")
            with self.assertRaises(ValueError):
                dist.stage_suite(suite, base)

    def test_tracked_independent_deletion_not_restored(self):
        with tempfile.TemporaryDirectory() as directory:
            suite, base = self.suite(directory)
            with patch.object(dist, "reviewed_sources", return_value=sources()), redirect_stdout(io.StringIO()):
                dist.stage_suite(suite, base, True)
                self.git(suite, "add", "packages/pa")
                self.git(suite, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                         "commit", "-m", "synthetic export")
                base = self.git(suite, "rev-parse", "HEAD").strip()
                (suite / "packages/pa/VERSION").unlink()
                with self.assertRaisesRegex(ValueError, "missing"):
                    dist.stage_suite(suite, base, True)


if __name__ == "__main__":
    unittest.main()
