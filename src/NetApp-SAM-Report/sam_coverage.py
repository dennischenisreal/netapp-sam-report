import json
import re
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import py7zr
from openpyxl import load_workbook


SOURCE_FILES = (
    'sysconfig-a.txt', 'cluster-info.xml', 'volume.xml', 'options.txt',
    'vol-status-v.txt', 'var-etc-ntp-conf.txt', 'dns.xml', 'data-object-store-config.xml',
    'nfs_servers_byname.xml', 'df.txt', 'df-i.txt', 'df-a.txt', 'sis-config.xml',
    'network-routes.xml', 'snapshot.xml', 'snapshot_analytics.xml', 'snap-list-n.txt',
    'snapshot_policy.xml', 'object-store-bucket.xml', 'export_rule_table.xml',
    'vol-status-f.txt', 'aggr-status-s.txt',
)
VOLUME_FIELDS = ('vol', 'vs', 'vol_uuid', 'i_uuid', 'styleEx', 'autosz',
                 'logicalSpaceReporting', 'logicalSpaceEnforcement', 'tiering_policy',
                 'nearly_full_threshold', 'full_threshold', 'snap_policy', 'policy')


def local_name(tag):
    return tag.rsplit('}', 1)[-1].split(':')[-1]


def read_source(path, name):
    content = Path(path).read_text(encoding='utf-8-sig', errors='strict')
    if not content.strip():
        return {'status': 'missing_data', 'reason': '文件为空', 'row_count': 0}
    result = {'status': 'ok', 'bytes': Path(path).stat().st_size}
    if name.endswith('.xml'):
        root = ET.fromstring(content)
        rows = [item for item in root.iter() if local_name(item.tag) == 'ROW']
        result['row_count'] = len(rows)
        result['fields'] = dict(Counter(local_name(child.tag) for row in rows for child in row
                                        if ''.join(child.itertext()).strip()))
        result['field_presence'] = dict(Counter(local_name(child.tag) for row in rows for child in row))
        legacy_rows = re.findall(r'<(?:asup:)?ROW\b[^>]*>(.*?)</(?:asup:)?ROW>', content, re.DOTALL)
        result['legacy_row_count'] = len(legacy_rows)
        direct_names = {local_name(child.tag) for row in rows for child in row}
        result['qualified_fields'] = sorted({field for row in legacy_rows
                                              for field in re.findall(r'<[A-Za-z_][\w.-]*:([A-Za-z_][\w.-]*)\b[^>]*>', row)} & direct_names)
        required = {'dns.xml': [('vserver',), ('nameservers',)],
                    'nfs_servers_byname.xml': [('vserver',), ('v4.0',), ('v41',)],
                    'sis-config.xml': [('vol',), ('bg_cmpr',), ('in_comp',), ('in_dedp',), ('compxn',)],
                    'network-routes.xml': [('route_vserver',), ('route_destination', 'destination'),
                                           ('route_gateway', 'gateway'), ('route_metric', 'metric')],
                    'snapshot.xml': [('s',), ('ct', 't'), ('sz', 'z')]}.get(name, [])
        gaps = Counter()
        for row in rows:
            present = {local_name(child.tag) for child in row}
            for alternatives in required:
                if not present.intersection(alternatives):
                    gaps['/'.join(alternatives)] += 1
        result['row_field_gaps'] = dict(gaps)
        if name == 'volume.xml':
            result['volumes'] = [{local_name(child.tag): ''.join(child.itertext()).strip()
                                  for child in row if local_name(child.tag) in VOLUME_FIELDS} for row in rows]
        if name == 'cluster-info.xml':
            result['cluster'] = next((''.join(item.itertext()).strip() for item in root.iter()
                                       if local_name(item.tag) == 'cluster-name'), '')
            for row in rows:
                values = {local_name(child.tag): ''.join(child.itertext()).strip() for child in row}
                if values.get('is-self', '').lower() == 'true':
                    result['node'] = values.get('node', '')
                    break
        if name == 'snapshot.xml':
            invalid = 0
            for row in rows:
                values = {local_name(child.tag): ''.join(child.itertext()).strip() for child in row}
                time_text = values.get('ct') or values.get('t', '')
                try:
                    from datetime import datetime
                    datetime.strptime(time_text, '%a %b %d %H:%M:%S %Y')
                except ValueError:
                    invalid += 1
            result['invalid_creation_times'] = invalid
    elif name == 'sysconfig-a.txt':
        match = re.search(r'System (?:ID|Serial Number):\s*\S+\s*\(([^)]+)\)', content)
        result['node'] = match[1].strip() if match else ''
        result['is_aff'] = 'All-Flash Optimized: true' in content or 'AFF' in content
    elif name == 'options.txt':
        result['parsed_options'] = [field for field in ('cf.giveback.auto.enable', 'cf.giveback.auto.after.panic.takeover')
                                    if re.search(re.escape(field) + r'\s+\w+', content, re.IGNORECASE)]
    elif name == 'vol-status-v.txt':
        result['volume_blocks'] = len(re.findall(r'^Volume name:\s*\S+', content, re.MULTILINE))
        result['autodelete_values'] = len(re.findall(r'Snapshot autodelete settings:.*?state=(?:on|off)', content, re.IGNORECASE))
    elif name in ('df.txt', 'df-i.txt'):
        pattern = r'Filesystem\s+kbytes\s+used\s+avail\s+capacity\s+Mounted on' if name == 'df.txt' else r'Filesystem\s+iused\s+ifree\s+%iused'
        result['table_header_recognized'] = bool(re.search(pattern, content))
    return result


def build_inventory(input_dir):
    archives = sorted(path for path in Path(input_dir).rglob('*') if path.is_file() and path.suffix.lower() == '.7z')
    records = []
    for archive_path in archives:
        record = {'archive': str(archive_path.resolve()), 'node': '', 'cluster': '', 'files': {}, 'errors': []}
        with tempfile.TemporaryDirectory(prefix='sam_coverage_') as temporary:
            try:
                with py7zr.SevenZipFile(archive_path, mode='r') as archive:
                    names = archive.getnames()
                    selected = []
                    for name in SOURCE_FILES:
                        members = [member for member in names if Path(member).name.lower() == name]
                        record['files'][name] = {'status': 'pending' if members else 'missing_data', 'members': members}
                        selected.extend(members)
                    if selected:
                        archive.extract(path=temporary, targets=selected)
                for name, info in record['files'].items():
                    if len(info['members']) != 1:
                        if info['members']:
                            info.update(status='failed', reason='同一归档存在多个同名文件，来源有歧义')
                        continue
                    try:
                        info.update(read_source(Path(temporary) / info['members'][0], name))
                    except Exception as error:
                        info.update(status='failed', reason=f'{type(error).__name__}: {error}')
            except Exception as error:
                record['errors'].append(f'{type(error).__name__}: {error}')
        cluster_info = record['files'].get('cluster-info.xml', {})
        sysconfig = record['files'].get('sysconfig-a.txt', {})
        record['cluster'] = cluster_info.get('cluster', '')
        record['node'] = cluster_info.get('node') or sysconfig.get('node', '')
        record['is_aff'] = sysconfig.get('is_aff')
        records.append(record)
    return {'schema_version': 1, 'input_dir': str(Path(input_dir).resolve()), 'archives': records,
            'archive_count': len(records), 'identity_source': 'archive_configuration_not_filename'}


def save_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def expected_volumes(inventory, params=None):
    filters = (params or {}).get('volume_filter', {})
    prefixes = tuple(value.lower() for value in filters.get('skip_prefixes', ['MDV', 'MDV_crs', 'fg_oss', 'fg_oss_']))
    suffixes = tuple(value.lower() for value in filters.get('skip_suffixes', ['_root']))
    names = {value.lower() for value in filters.get('skip_names', ['vol0'])}
    expected, excluded = set(), Counter()
    for archive in inventory['archives']:
        for volume in archive['files'].get('volume.xml', {}).get('volumes', []):
            name = volume.get('vol', '')
            lower = name.lower()
            reason = ('missing_name' if not name else 'bucket_volume' if lower.startswith('fg_oss')
                      else 'constituent' if re.search(r'__\d{4}$', name) else 'system_volume'
                      if lower in names or lower.startswith(prefixes) or lower.endswith(suffixes) else '')
            if reason:
                excluded[reason] += 1
            else:
                expected.add((archive['cluster'], volume.get('vs', ''), name))
    return expected, dict(excluded)


def reconcile_outputs(inventory, paths, params=None):
    expected, exclusions = expected_volumes(inventory, params)
    nodes = {row['node'] for row in inventory['archives'] if row['node']}
    findings = []
    for module, sheet_name in [('Controller', 'Controller Report'), ('Capacity', 'Controller Capacity')]:
        workbook = load_workbook(paths[module], read_only=True, data_only=True)
        try:
            sheet = workbook[sheet_name]
            header = [str(value or '').strip().lower() for value in next(sheet.values)]
            aliases = ('node', 'node name', 'system name', '节点名')
            column = next((header.index(name) for name in aliases if name in header), None)
            if column is None:
                findings.append({'scope': sheet_name, 'status': 'failed', 'reason': '找不到节点标识列'})
                continue
            actual = {str(row[column]).strip() for row in sheet.iter_rows(min_row=2, values_only=True)
                      if len(row) > column and row[column]}
            findings.append({'scope': sheet_name, 'status': 'failed' if nodes != actual and nodes else 'ok',
                             'expected_count': len(nodes), 'actual_count': len(actual),
                             'missing_nodes': sorted(nodes - actual), 'unexpected_nodes': sorted(actual - nodes)})
        finally:
            workbook.close()
    workbook = load_workbook(paths['Capacity'], read_only=True, data_only=True)
    try:
        sheet = workbook['Volume List']
        header = list(next(sheet.values))
        indices = [header.index(name) for name in ('Cluster Name', 'SVM名字', 'Volume Name')]
        actual_rows = Counter(tuple(str(row[index] or '').strip() for index in indices)
                              for row in sheet.iter_rows(min_row=2, values_only=True) if row[indices[2]])
        actual = set(actual_rows)
        duplicates = [{'identity': list(key), 'count': count}
                      for key, count in sorted(actual_rows.items()) if count > 1]
        findings.append({'scope': 'Volume List', 'status': 'failed' if expected != actual or duplicates else 'ok',
                         'expected_count': len(expected), 'actual_count': len(actual),
                         'duplicate_volumes': duplicates,
                         'missing_volumes': [list(key) for key in sorted(expected - actual)],
                         'unexpected_volumes': [list(key) for key in sorted(actual - expected)],
                         'excluded_source_rows': exclusions})
    finally:
        workbook.close()
    unknown = [row['archive'] for row in inventory['archives'] if not row['node'] or not row['cluster']]
    findings.append({'scope': 'Source Identity', 'status': 'missing_data' if unknown else 'ok',
                     'unresolved_archives': unknown})
    return {'status': 'failed' if any(item['status'] == 'failed' for item in findings)
            else 'needs_review' if unknown else 'ok', 'findings': findings}
