import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / 'src/NetApp-SAM-Report/run.py'


class CapacityParameterPathTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.skills = self.root / 'isolated skills'
        self.template = self.skills / '2-netapp-capacity-aggr-vol-fg/params.json'
        self.template.parent.mkdir(parents=True)
        self.original = (PROJECT / 'src/2-netapp-capacity-aggr-vol-fg/params.json').read_bytes()
        self.template.write_bytes(self.original)
        self.config = self.root / 'project.json'
        self.config.write_text(json.dumps({'schema_version': 1, 'skills_root': str(self.skills)}), encoding='utf-8')
        spec = importlib.util.spec_from_file_location('isolated_capacity_settings', PROJECT / 'src/NetApp-SAM-Report/sam_settings.py')
        self.settings = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.settings)
        tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
        selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in ('update_capacity_params', 'get_default_thresholds')]
        self.namespace = {'os': os, 'Path': Path, 'json': json, 'path_setting': self.settings.path_setting}
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), 'exec'), self.namespace)
        self.environment = patch.dict(os.environ, {'SAM_PROJECT_ROOT': str(self.root), 'SAM_PROJECT_CONFIG': str(self.config)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_default_thresholds_write_explicit_output_without_changing_template(self):
        output = self.root / 'run-params.json'
        defaults = self.namespace['get_default_thresholds']()
        result = self.namespace['update_capacity_params'](defaults, str(output))
        self.assertEqual(result, str(output))
        actual = json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(actual['thresholds']['top_volume_size_tb']['value'], 5)
        self.assertEqual(actual['highlights']['capacity_used_percent']['value'], 85)
        self.assertEqual(self.template.read_bytes(), self.original)

    def test_custom_threshold_only_changes_requested_values(self):
        thresholds = self.namespace['get_default_thresholds']()
        thresholds['top_volume_size_tb'] = 17
        thresholds['capacity_used_percent'] = 91
        output = self.root / 'custom.json'
        self.namespace['update_capacity_params'](thresholds, str(output))
        actual = json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(actual['thresholds']['top_volume_size_tb']['value'], 17)
        self.assertEqual(actual['highlights']['capacity_used_percent']['value'], 91)
        self.assertEqual(actual['highlights']['volume_list_used_percent']['value'], thresholds['volume_list_used_percent'])
        self.assertEqual(self.template.read_bytes(), self.original)

    def test_legacy_no_output_argument_still_writes_selected_template(self):
        result = self.namespace['update_capacity_params'](self.namespace['get_default_thresholds']())
        self.assertEqual(result, str(self.template))

    def test_missing_selected_template_fails_instead_of_using_another_skill(self):
        self.template.unlink()
        with self.assertRaises(FileNotFoundError):
            self.namespace['update_capacity_params'](self.namespace['get_default_thresholds'](), str(self.root / 'not-created.json'))
        self.assertFalse((self.root / 'not-created.json').exists())

    def test_invalid_explicit_config_does_not_fall_back(self):
        self.config.write_text('{invalid', encoding='utf-8')
        with self.assertRaises(json.JSONDecodeError):
            self.namespace['update_capacity_params'](self.namespace['get_default_thresholds']())

    def test_calculations_threshold_updates_and_output_body_remain_unchanged(self):
        tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'update_capacity_params')
        function.body = function.body[1:]
        actual = hashlib.sha256(ast.dump(function, include_attributes=False).encode()).hexdigest()
        self.assertEqual(actual, 'a2667564e48a51c792876e89853a40f35be9587b91a43797f027f584573b9c4b')


if __name__ == '__main__':
    unittest.main()
