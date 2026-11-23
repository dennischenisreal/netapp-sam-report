import json
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

from openpyxl import load_workbook
import py7zr


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'tools'))
from validate_synthetic_report import fixture_workbook, prepare, xml_rows


class SyntheticFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.logs = prepare(cls.root)
        cls.data = cls.root / '.local/data'

    def test_workbook_knowledge_fixture_is_readable_without_production_files(self):
        workbook = load_workbook(self.data / 'knowledge/ONTAP EOS date/example.xlsx', data_only=True)
        try:
            self.assertEqual(workbook.active['A2'].value, 'Version')
            self.assertEqual(workbook.active['B3'].value, '2030-01-01')
        finally:
            workbook.close()

    def test_volume_identity_is_not_collapsed_across_clusters_or_svms(self):
        expected = json.loads((self.data / 'expected.json').read_text(encoding='utf-8'))
        selected = [row for row in expected['volumes'] if row['volume'] == 'vol0045_SHARED']
        self.assertEqual(len(selected), 4)
        self.assertEqual(len({(row['cluster'], row['svm'], row['volume']) for row in selected}), 4)

    def test_unknown_and_zero_have_different_source_fields(self):
        tree = ET.parse(self.data / 'fixture-source-1/volume.xml')
        records = {row.findtext('vol'): row for row in tree.getroot()}
        self.assertEqual(records['zero'].findtext('used'), '0')
        self.assertIsNone(records['unknown'].find('used'))

    def test_snapshot_fixture_contains_both_boundary_and_constituent_cases(self):
        tree = ET.parse(self.data / 'fixture-source-1/snapshot.xml')
        records = {row.findtext('s'): row for row in tree.getroot()}
        self.assertEqual(set(records), {'old-demo', 'boundary-demo', 'recent-demo', 'child-demo'})
        self.assertEqual(records['child-demo'].findtext('vo'), 'FG__0001')
        self.assertNotEqual(records['old-demo'].findtext('ct'), records['boundary-demo'].findtext('ct'))

    def test_archives_have_real_contents_and_unrelated_filenames(self):
        archives = sorted(self.logs.glob('*.7z'))
        self.assertEqual(len(archives), 2)
        with py7zr.SevenZipFile(archives[0]) as archive:
            self.assertTrue({'volume.xml', 'snapshot.xml', 'CLUSTER-INFO.xml', 'SYSCONFIG-A.txt'}.issubset(archive.getnames()))
        self.assertNotIn('demo-node', archives[0].name)

    def test_xml_values_are_escaped_without_changing_identity(self):
        tree = ET.fromstring(xml_rows([{'vol': 'PROJ_A&B', 'vs': 'demo'}]))
        self.assertEqual(list(tree)[0].findtext('vol'), 'PROJ_A&B')

    def test_fixture_writer_does_not_overwrite_existing_workbook(self):
        target = self.root / 'nonoverwriting.xlsx'
        fixture_workbook(target, [('Example', [[0]])])
        original = target.read_bytes()
        with self.assertRaises(FileExistsError):
            fixture_workbook(target, [('Example', [[1]])])
        self.assertEqual(target.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
