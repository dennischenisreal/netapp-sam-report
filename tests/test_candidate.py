import ast
import importlib.util
import json
import os
from pathlib import Path
import runpy
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / '.local/runtime/skills/NetApp-SAM-Report'
sys.path.insert(0, str(SHARED))


class CandidateTests(unittest.TestCase):
    def test_management_test_subprocess_stays_inside_source_project(self):
        tree = ast.parse((ROOT / 'sam.py').read_text(encoding='utf-8'))
        command = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                       and node.args and isinstance(node.args[0], ast.List)
                       and any(isinstance(value, ast.Constant) and value.value == 'unittest' for value in node.args[0].elts))
        working_directory = next(value.value for value in command.keywords if value.arg == 'cwd')
        self.assertEqual(ast.dump(working_directory), ast.dump(ast.Name(id='PROJECT', ctx=ast.Load())))

    def test_six_modules_and_complete_sheet_contract(self):
        from sam_runtime import MODULE_SHEETS, validate_final_workbook
        self.assertEqual(len(MODULE_SHEETS), 6)
        self.assertEqual(sum(map(len, MODULE_SHEETS.values())) + 2, 25)
        self.assertTrue(callable(validate_final_workbook))

    def test_existing_main_entry_is_callable(self):
        module = runpy.run_path(str(SHARED / 'run.py'))
        self.assertTrue(callable(module['process_directory']))
        self.assertEqual(module['get_default_thresholds']()['volume_list_used_percent'], 80)

    def test_three_real_adapters_import_without_original_install(self):
        for relative in ('ASUP Analysis/parse_asup_logs_final.py', 'ASUP Analysis/generate_all_final.py', 'Case Analysis/parse_c1_case.py'):
            with self.subTest(adapter=relative):
                module = runpy.run_path(str(ROOT / '.local/runtime/legacy' / relative))
                self.assertTrue(callable(module['main']))

    def test_network_and_best_practice_real_entry_imports(self):
        for name, filename in (('5-netapp-network-ems', 'parse_network_final.py'), ('6-best-practice-check', 'run.py')):
            with self.subTest(module=name):
                module = runpy.run_path(str(ROOT / '.local/runtime/skills' / name / filename))
                self.assertTrue(callable(module.get('main')))

    def test_legacy_settings_are_explicit_and_generic(self):
        from sam_package_compat import firmware_name, legacy_sites
        self.assertEqual(legacy_sites(), ['demo-site'])
        self.assertEqual(firmware_name('/demo/input'), 'input')

    def test_capacity_parser_entry_import(self):
        path = ROOT / '.local/runtime/skills/2-netapp-capacity-aggr-vol-fg'
        sys.path.insert(0, str(path))
        try:
            module = runpy.run_path(str(path / 'run.py'))
            self.assertTrue(callable(module['main']))
        finally:
            sys.path.remove(str(path))

    def test_profile_and_version_do_not_use_user_installation(self):
        from customer_profiles import profile_path
        self.assertTrue(profile_path().is_relative_to(ROOT / '.local/config'))
        from sam_runtime import SKILLS_ROOT, LEGACY_ROOT
        self.assertTrue(SKILLS_ROOT.is_relative_to(ROOT / '.local/runtime'))
        self.assertTrue(LEGACY_ROOT.is_relative_to(ROOT / '.local/runtime'))

    def test_trend_entry_import(self):
        path = ROOT / '.local/runtime/skills/NetApp Capacity Trend and Case Report'
        sys.path.insert(0, str(path))
        module = runpy.run_path(str(path / 'main.py'))
        self.assertTrue(callable(module['main']))


if __name__ == '__main__':
    unittest.main()
