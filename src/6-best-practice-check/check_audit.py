import json
import os
import re
import sys
from contextvars import ContextVar
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'NetApp-SAM-Report'))
from sam_coverage import build_inventory, save_json
from customer_profiles import load_profiles, check_enabled, target_clusters


ACTIVE_CHECK = ContextVar('sam_active_check', default=None)
LABELS = {'passed': '通过', 'violations': '发现违规', 'not_applicable': '不适用',
          'missing_data': '缺少数据', 'failed': '解析失败'}
SOURCES = {
    'check1': ('options.txt',), 'check2': ('volume.xml',), 'check3': ('vol-status-v.txt',),
    'check4': ('volume.xml', 'sysconfig-a.txt'), 'check5': ('var-etc-ntp-conf.txt',),
    'check6': ('dns.xml',), 'check7': ('data-object-store-config.xml', 'volume.xml'),
    'check8': ('nfs_servers_byname.xml',), 'check9': ('volume.xml',),
    'check10': ('volume.xml', 'df.txt', 'df-i.txt'), 'check10_convert': ('volume.xml',),
    'check11': ('sysconfig-a.txt', 'sis-config.xml'), 'check12': ('network-routes.xml',),
    'check13': ('snapshot.xml', 'volume.xml'), 'check14': ('volume.xml',),
}
FIELDS = {'check2': ('vol', 'autosz'), 'check4': ('vol', 'logicalSpaceReporting', 'logicalSpaceEnforcement'),
          'check10_convert': ('vol', 'vs', 'styleEx'),
          'check6': ('vserver', 'nameservers'), 'check8': ('vserver', 'v4.0', 'v41'),
          'check9': ('vol', 'nearly_full_threshold', 'full_threshold'),
          'check11': ('vol', 'bg_cmpr', 'in_comp', 'in_dedp', 'compxn'),
          'check12': ('route_vserver',), 'check13': ('s',), 'check14': ('vol', 'snap_policy')}


def record_error(archive, error):
    active = ACTIVE_CHECK.get()
    if active is not None:
        active['errors'].append({'archive': str(Path(archive).resolve()) if archive != '*' else '*',
                                 'error': f'{type(error).__name__}: {error}'})


def observe_extraction(archive, target_files, result):
    active = ACTIVE_CHECK.get()
    if active is None:
        return
    resolved = str(Path(archive).resolve())
    requested = active['visited'].setdefault(resolved, set())
    requested.update(name.lower() for name in target_files)
    for target, path in result.items():
        if path and Path(path).name.lower() != target.lower():
            active['errors'].append({'archive': resolved, 'error': f'文件名模糊匹配: {target} -> {Path(path).name}'})


def record_snapshot_duplicates(count):
    active = ACTIVE_CHECK.get()
    if active is not None:
        active['snapshot_duplicates_removed'] = count


def eligible_volume(volume):
    name = volume.get('vol', '')
    lower = name.lower()
    return bool(name) and not (re.search(r'__\d+$', name) or lower in ('vol0', 'root')
                              or lower.startswith(('root_', 'mdv_crs')) or lower.endswith('_root'))


class BestPracticeAudit:
    def __init__(self, input_dir, customer_name):
        inventory_path = os.environ.get('SAM_SOURCE_INVENTORY')
        self.inventory = json.loads(Path(inventory_path).read_text(encoding='utf-8')) if inventory_path else build_inventory(input_dir)
        if Path(self.inventory['input_dir']).resolve() != Path(input_dir).resolve():
            raise ValueError('覆盖清单与当前日志目录不一致')
        self.profiles = load_profiles()
        self.customer_name = customer_name
        self.target_clusters = target_clusters(self.profiles)
        self.checks = {}

    def applicability(self, key, archive):
        if key in ('check4', 'check9', 'check14') and not check_enabled(key, self.customer_name, self.profiles):
            return False
        if key == 'check14' and archive['cluster'] not in self.target_clusters:
            return False
        if key in ('check4', 'check11') and archive.get('is_aff') is False:
            return False
        if key == 'check7':
            config = archive['files'].get('data-object-store-config.xml', {})
            if config.get('status') == 'ok' and config.get('row_count') == 0:
                return False
        if key == 'check10':
            volumes = archive['files'].get('volume.xml', {})
            if volumes.get('status') == 'ok' and volumes.get('row_count', 0) > 0:
                if not any(re.search(r'__\d+$', row.get('vol', '')) for row in volumes.get('volumes', [])):
                    return False
        if key == 'check10_convert':
            volumes = archive['files'].get('volume.xml', {})
            if volumes.get('status') == 'ok' and volumes.get('row_count', 0) > 0 and all(row.get('styleEx') for row in volumes.get('volumes', [])):
                if not any(row.get('styleEx', '').lower() == 'flexvol' and row.get('vol', '').lower() != 'vol0'
                           and not row.get('vol', '').lower().startswith('fg_oss') and 'bucket' not in row.get('vol', '').lower()
                           for row in volumes.get('volumes', [])):
                    return False
        return True

    def source_state(self, key, archive, active):
        problems = []
        failed = list(archive.get('errors', []))
        visited = active['visited'].get(archive['archive'], set())
        for name in SOURCES[key]:
            info = archive['files'].get(name, {})
            if info.get('status') != 'ok':
                (failed if info.get('status') == 'failed' else problems).append(f'{name}: {info.get("reason", "未采集或为空")}')
                continue
            if name not in visited:
                problems.append(f'{name}: 业务检查未读取')
            if name.endswith('.xml'):
                count = info.get('row_count', 0)
                if count != info.get('legacy_row_count', count) or info.get('qualified_fields'):
                    failed.append(f'{name}: XML格式未被原解析规则完整支持')
                if info.get('row_field_gaps'):
                    problems.append(f'{name}: 存在字段缺失的数据行 {info["row_field_gaps"]}')
                if count == 0:
                    problems.append(f'{name}: 无可核对数据行，不能确认为通过')
                field_source = 'sis-config.xml' if key == 'check11' else SOURCES[key][0]
                if name == field_source:
                    fields = info.get('field_presence', info.get('fields', {}))
                    for field in FIELDS.get(key, ()):
                        if field not in fields:
                            problems.append(f'{name}: 缺少字段 {field}')
                    if name == 'volume.xml':
                        for volume in info.get('volumes', []):
                            if eligible_volume(volume):
                                absent = [field for field in FIELDS.get(key, ())
                                          if field not in volume or not volume[field] and field != 'snap_policy']
                                if absent:
                                    problems.append(f'{volume.get("vs", "")}/{volume.get("vol", "")}: 缺少字段 {", ".join(absent)}')
            if name == 'options.txt' and len(info.get('parsed_options', [])) != 2:
                problems.append('OPTIONS.txt: 两项giveback设置未全部识别')
            if name == 'vol-status-v.txt' and (not info.get('volume_blocks') or info.get('volume_blocks') != info.get('autodelete_values')):
                problems.append('VOL-STATUS-V.txt: 卷段数与autodelete配置数不一致')
            if name == 'snapshot.xml' and info.get('invalid_creation_times'):
                problems.append(f'snapshot.xml: {info["invalid_creation_times"]}条创建时间缺失或无法识别')
            if name in ('df.txt', 'df-i.txt') and not info.get('table_header_recognized', False):
                problems.append(f'{name}: 未识别到原检查支持的表头，平衡性未完整评估')
        failures = [item['error'] for item in active['errors'] if item['archive'] == archive['archive']]
        failed.extend(failures)
        if not archive['node'] or not archive['cluster']:
            problems.append('配置中的节点或集群身份缺失')
        return {'archive': archive['archive'], 'cluster': archive['cluster'], 'node': archive['node'],
                'status': 'failed' if failed else 'missing_data' if problems else 'checked',
                'reasons': failed + problems}

    def run(self, key, function, *arguments):
        active = {'visited': {}, 'errors': []}
        token = ACTIVE_CHECK.set(active)
        try:
            results = function(*arguments)
        except Exception as error:
            active['errors'].append({'archive': '*', 'error': f'{type(error).__name__}: {error}'})
            results = [{'note': True, 'message': f'{key}执行失败: {error}'}]
        finally:
            ACTIVE_CHECK.reset(token)
        coverage = [self.source_state(key, archive, active) for archive in self.inventory['archives']
                    if self.applicability(key, archive)]
        issues = [row for row in results if not row.get('note')]
        notes = [row.get('message', '') for row in results if row.get('note')]
        missing = any(row['status'] == 'missing_data' for row in coverage) or any(row.get('incomplete') for row in results)
        failed = any(row['status'] == 'failed' for row in coverage) or bool(active['errors'])
        if key == 'check13' and any(str(row.get('volume_name', '')).startswith('N/A') for row in issues):
            missing = True
            notes.append('存在尚未匹配卷名的老旧快照，不能视为完整检查')
        if key == 'check14' and check_enabled(key, self.customer_name, self.profiles):
            present = {archive['cluster'] for archive in self.inventory['archives']}
            absent = sorted(self.target_clusters - present)
            if absent:
                missing = True
                notes.append('目标集群未采集: ' + ', '.join(absent))
        status = ('failed' if failed else 'missing_data' if missing else 'not_applicable' if not coverage
                  else 'violations' if issues else 'passed')
        record = {'status': status, 'label': LABELS[status], 'issue_count': len(issues), 'archives': coverage,
                  'errors': active['errors'], 'notes': notes,
                  'duplicate_snapshot_rows_removed': active.get('snapshot_duplicates_removed', 0),
                  'findings': issues}
        self.checks[key] = record
        if status in ('failed', 'missing_data'):
            results.append({'note': True, 'message': f'覆盖检查：{LABELS[status]}；已列出的{len(issues)}项仍保留，未检查部分不能视为通过。详情见同名coverage.json。'})
            reasons = {}
            for row in coverage:
                for reason in row['reasons']:
                    reasons.setdefault(reason, []).append(row['node'] or Path(row['archive']).name)
            for reason, nodes in list(reasons.items())[:8]:
                results.append({'note': True, 'message': f'未完整检查 {", ".join(sorted(set(nodes))[:5])}: {reason}'})
            if len(reasons) > 8:
                results.append({'note': True, 'message': f'另有{len(reasons) - 8}类缺口，详见覆盖记录。'})
        return results

    def save(self, report_path):
        save_json(Path(report_path).with_suffix('.coverage.json'), {'schema_version': 1, 'checks': self.checks})
