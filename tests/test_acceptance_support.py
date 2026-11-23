import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'tools'))
from acceptance_support import checked_path, extract
import package_candidate


class AcceptanceSafetyTests(unittest.TestCase):
    def setUp(self):
        temporary = PROJECT / '.local/acceptance-unit-temp'
        temporary.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=temporary)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def archive(self, names=('example.txt',), payload=b'fictional', manifest=None, symbolic=False):
        target = self.root / 'example.zip'
        entries = manifest if manifest is not None else [{'path': name, 'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()} for name in names]
        with zipfile.ZipFile(target, 'x') as stream:
            for name in names:
                info = zipfile.ZipInfo(name)
                info.external_attr = (0o120777 if symbolic else 0o100644) << 16
                stream.writestr(info, payload)
            stream.writestr('package-manifest.json', json.dumps({'files': entries}))
        return target

    def source_tree(self):
        source = self.root / 'source'
        source.mkdir()
        (source / 'example.txt').write_text('fictional', encoding='utf-8')
        payload = (source / 'example.txt').read_bytes()
        (source / 'package-manifest.json').write_text(json.dumps({'files': [{'path': 'example.txt', 'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}]}), encoding='utf-8')
        return source

    def test_valid_archive_has_exact_inventory(self):
        target = self.root / 'extracted'
        self.assertEqual(extract(self.archive(), target), 2)
        self.assertEqual((target / 'example.txt').read_bytes(), b'fictional')

    def test_traversal_rejected(self):
        with self.assertRaises(ValueError):
            extract(self.archive(('../outside.txt',)), self.root / 'extracted')
        self.assertFalse((self.root / 'outside.txt').exists())

    def test_case_collisions_rejected(self):
        with self.assertRaises(ValueError):
            extract(self.archive(('Example.txt', 'example.txt')), self.root / 'extracted')

    def test_noncanonical_and_absolute_paths_rejected(self):
        for name in ('a//b', './example', '/example', 'folder\\file', 'drive:file', '.'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                checked_path(name)

    def test_private_paths_case_insensitively_rejected(self):
        for name in ('.git/config', '.GIT/config', '.local/config.json', '.LOCAL/config.json',
                     'release.lock.json', 'config/project.json', 'config/customer-profiles.local.json'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                checked_path(name)

    def test_modified_hash_rejected(self):
        with self.assertRaises(ValueError):
            extract(self.archive(manifest=[{'path': 'example.txt', 'bytes': 9, 'sha256': '0' * 64}]), self.root / 'extracted')

    def test_unlisted_file_rejected(self):
        with self.assertRaises(ValueError):
            extract(self.archive(('extra.txt',), manifest=[{'path': 'example.txt', 'bytes': 9, 'sha256': '0' * 64}]), self.root / 'extracted')

    def test_empty_inventory_rejected(self):
        with self.assertRaises(ValueError):
            extract(self.archive(names=(), manifest=[]), self.root / 'extracted')

    def test_duplicate_inventory_rejected(self):
        entry = {'path': 'example.txt', 'bytes': 9, 'sha256': '0' * 64}
        with self.assertRaises(ValueError):
            extract(self.archive(manifest=[entry, entry]), self.root / 'extracted')

    def test_symbolic_entry_rejected(self):
        with self.assertRaises(ValueError):
            extract(self.archive(symbolic=True), self.root / 'extracted')

    def test_oversized_member_rejected(self):
        with self.assertRaises(ValueError):
            extract(self.archive(payload=b'x' * 2_000_001), self.root / 'extracted')

    def test_extraction_does_not_overwrite_destination(self):
        target = self.root / 'extracted'
        target.mkdir()
        with self.assertRaises(FileExistsError):
            extract(self.archive(), target)

    def test_packaging_is_reproducible_and_excludes_local_runtime(self):
        source = self.source_tree()
        (source / 'release.lock.json').write_text('fictional runtime lock', encoding='utf-8')
        with patch.object(package_candidate, 'PROJECT', source):
            first = package_candidate.package(source / '.local/first')
            second = package_candidate.package(source / '.local/second')
        self.assertEqual(first['archive_sha256'], second['archive_sha256'])
        self.assertEqual(first['archive_files'], 2)

    def test_packaging_rejects_changed_source(self):
        source = self.source_tree()
        (source / 'example.txt').write_text('changed', encoding='utf-8')
        with patch.object(package_candidate, 'PROJECT', source), self.assertRaises(ValueError):
            package_candidate.package(source / '.local/package')

    def test_packaging_rejects_unreviewed_source(self):
        source = self.source_tree()
        (source / 'unreviewed.txt').write_text('fictional', encoding='utf-8')
        with patch.object(package_candidate, 'PROJECT', source), self.assertRaises(ValueError):
            package_candidate.package(source / '.local/package')

    def test_packaging_rejects_external_output_and_overwrite(self):
        source = self.source_tree()
        with patch.object(package_candidate, 'PROJECT', source):
            with self.assertRaises(ValueError):
                package_candidate.package(self.root / 'outside')
            package_candidate.package(source / '.local/package')
            with self.assertRaises(FileExistsError):
                package_candidate.package(source / '.local/package')


if __name__ == '__main__':
    unittest.main()
