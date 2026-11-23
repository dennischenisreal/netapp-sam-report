"""Exercise an unmodified shared candidate using only fabricated local inputs."""
import argparse
from datetime import datetime, timedelta
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import re
from xml.sax.saxutils import escape
import zipfile

import py7zr
from openpyxl import load_workbook

from acceptance_support import PROJECT, TEMPLATES, digest, save, extract


TB = 1024 ** 4
ASUP = 'http://asup_search.netapp.com/ns/ASUP/1.1'


def xml_rows(records):
    return '<asup:root xmlns:asup="' + ASUP + '">' + ''.join(
        '<asup:ROW>' + ''.join('<' + key + '>' + escape(str(value)) + '</' + key + '>'
                              for key, value in record.items()) + '</asup:ROW>' for record in records) + '</asup:root>'


def qualified_rows(namespace, records):
    return '<asup:root xmlns:asup="' + ASUP + '" xmlns:f="' + namespace + '">' + ''.join(
        '<asup:ROW>' + ''.join('<f:' + key + '>' + escape(str(value)) + '</f:' + key + '>' for key, value in record.items()) + '</asup:ROW>' for record in records) + '</asup:root>'


def fixture_workbook(path, sheets):
    """Write minimal OOXML fixtures, not production report formatting or formulas."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    relationships = 'http://schemas.openxmlformats.org/package/2006/relationships'
    files = {
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' + ''.join('<Override PartName="/xl/worksheets/sheet' + str(index) + '.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for index in range(1, len(sheets) + 1)) + '</Types>',
        '_rels/.rels': '<Relationships xmlns="' + relationships + '"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml': '<workbook xmlns="' + ns + '" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + ''.join('<sheet name="' + escape(name) + '" sheetId="' + str(index) + '" r:id="rId' + str(index) + '"/>' for index, (name, _) in enumerate(sheets, 1)) + '</sheets></workbook>',
        'xl/_rels/workbook.xml.rels': '<Relationships xmlns="' + relationships + '">' + ''.join('<Relationship Id="rId' + str(index) + '" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet' + str(index) + '.xml"/>' for index in range(1, len(sheets) + 1)) + '</Relationships>',
    }
    for index, (_, rows) in enumerate(sheets, 1):
        body = []
        for row_index, values in enumerate(rows, 1):
            cells = []
            for column_index, value in enumerate(values, 1):
                column = ''
                number = column_index
                while number:
                    number, remainder = divmod(number - 1, 26)
                    column = chr(65 + remainder) + column
                coordinate = column + str(row_index)
                if isinstance(value, (int, float)):
                    cells.append('<c r="' + coordinate + '"><v>' + str(value) + '</v></c>')
                elif value is not None:
                    cells.append('<c r="' + coordinate + '" t="inlineStr"><is><t>' + escape(str(value)) + '</t></is></c>')
            body.append('<row r="' + str(row_index) + '">' + ''.join(cells) + '</row>')
        files['xl/worksheets/sheet' + str(index) + '.xml'] = '<worksheet xmlns="' + ns + '"><sheetData>' + ''.join(body) + '</sheetData></worksheet>'
    with zipfile.ZipFile(path, 'x', zipfile.ZIP_DEFLATED) as archive:
        for name, text in files.items():
            archive.writestr(name, text.encode('utf-8'))


def prepare(root, complete=False, scenario='complete'):
    data = root / '.local/data'
    logs = data / 'asup/demo-special/11月'
    logs.mkdir(parents=True)
    knowledge = data / 'knowledge'
    fixture_workbook(knowledge / 'ONTAP EOS date/example.xlsx', [('EOS', [['Synthetic fixture'], ['Version', 'End of Limited Support'], ['9.16', '2030-01-01']])])
    fixture_workbook(knowledge / 'Platform EOS date/example.xlsx', [('EOS', [['设备型号', 'EOS日期'], ['AFF-A250', '2030-01-01']])])
    fixture_workbook(knowledge / 'MA date/example.xlsx', [('MA', [['Demo', 'DEMO-SN-1', '2026-01-01', '2030-01-01', 'Synthetic'], ['Demo', 'DEMO-SN-2', '2026-01-01', '2030-01-01', 'Synthetic']])])
    fixture_workbook(knowledge / 'BMC firmware/example.xlsx', [('Firmware', [['Synthetic fixture'], ['Major', 'Latest'], ['BMC 15.x', '15.99']])])
    (knowledge / 'JV Case 数据源').mkdir(parents=True)
    case_header = ['Case ID', 'Case创建时间', '四级产品树', '主机编号', '故障二级分类', '故障三级分类', '故障四级分类', '问题描述', '客户姓名', '企业名称', '是否有上门单']
    case_rows = [case_header]
    for index, category in enumerate(('硬件故障', '软件故障', '非技术类'), 1):
        case_rows.append(['DEMO-CASE-' + str(index), '2026-11-01', 'AFF-A250', 'DEMO-SN-1', category, 'Synthetic', 'Synthetic', 'Fabricated test case', 'Demo', 'Example', '否'])
    case_rows.append(['UNMATCHED-CASE', '2026-11-01', 'AFF-A250', 'UNRELATED-SERIAL', '硬件故障', 'Synthetic', 'Synthetic', 'Must not match', 'Demo', 'Example', '否'])
    fixture_workbook(knowledge / 'JV Case 数据源/Case查询-2026年-demo.xlsx', [('Cases', case_rows)])
    for month, used in [(5, 16), (7, 18), (8, 20), (9, 21)]:
        history_rows = [['Cluster Name', 'SVM名字', 'Volume Name', 'Volume Size (TB)', 'Used Size (TB)']]
        history_rows.extend([[cluster, 'svm-one', 'vol0045_SHARED', 40, used] for cluster in ('demo-cluster-01', 'demo-cluster-02')])
        fixture_workbook(data / 'history/Demo Special' / ('SAM_Report_Demo_2026年' + str(month) + '月.xlsx'), [('Volume List', history_rows)])
    expected = []
    for index, cluster in enumerate(('demo-cluster-01', 'demo-cluster-02'), 1):
        node = 'demo-node-' + str(index)
        records = []
        for name, svm, size, used in [('vol0045_SHARED', 'svm-one', 40, 24), ('vol0045_SHARED', 'svm-two', 10, 7), ('PROJRUN_demo', 'svm-one', 8, 4), ('PROJ_demo', 'svm-one', 8, 4), ('FG', 'svm-one', 20, 12), ('FG__0001', 'svm-one', 10, 6), ('FG__0002', 'svm-one', 10, 6), ('fg_oss_demo', 'svm-one', 50, 30), ('fg_oss_demo__0001', 'svm-one', 25, 15), ('zero', 'svm-one', 0, 0), ('unknown', 'svm-one', None, None), ('vol0', 'svm-one', 1, 0)]:
            if complete and (svm == 'svm-two' or name == 'unknown'):
                continue
            record = {'vol': name, 'vs': svm, 'vol_uuid': cluster + '-' + svm + '-' + name, 'aggr': 'aggr_data', 'styleEx': 'flexgroup-constituent' if '__000' in name else 'flexgroup' if name in ('FG', 'fg_oss_demo') else 'flexvol', 'autosz': 'false', 'logicalSpaceReporting': 'false', 'logicalSpaceEnforcement': 'false', 'snap_policy': 'daily-demo' if 'PROJRUN' in name else 'none', 'policy': 'export-demo', 'tiering_policy': 'auto', 'tiering_min_cooling_days': 31, 'nearly_full_threshold': 85, 'full_threshold': 95}
            if size is not None:
                record.update(size=size * TB, avail=(size - used) * TB, used=used * TB, pcnt_used=str(round(used / size * 100) if size else 0) + '%', logicalUsedPercent=96 if name == 'vol0045_SHARED' else 0, perfTierInactiveUserData=TB if name == 'vol0045_SHARED' else 0, files=1000000000, files_used=200000000)
            if name in ('FG', 'fg_oss_demo') or '__000' in name:
                record['fgUuid'] = cluster + '-svm-one-' + name.split('__')[0]
            records.append(record)
            if name not in ('vol0',) and not name.startswith('fg_oss') and '__000' not in name:
                expected.append({'cluster': cluster, 'svm': svm, 'volume': name, 'size_tb': size, 'used_tb': used})
        snapshots = []
        for age, name, volume in [(121, 'old-demo', 'vol0045_SHARED'), (120, 'boundary-demo', 'vol0045_SHARED'), (119, 'recent-demo', 'vol0045_SHARED'), (200, 'child-demo', 'FG__0001')]:
            snapshots.append({'s': name, 'vo': volume, 'vs': 'svm-one', 'ct': (datetime(2026, 11, 23, 12) - timedelta(days=age)).strftime('%a %b %d %H:%M:%S %Y'), 'sz': 2 * 1024 ** 3, 'i': cluster + '-' + name})
        files = {
            'CLUSTER-INFO.xml': xml_rows([{'cluster-name': cluster, 'node': node, 'is-self': 'true'}]).replace('<asup:root ', '<asup:root xmlns="http://asup_search.netapp.com/ns/CLUSTER_INFO/1.0" '),
            'SYSCONFIG-A.txt': 'NetApp Release 9.16.1\nSystem Serial Number: DEMO-SN-' + str(index) + ' (' + node + ')\nSystem ID: ' + str(1000 + index) + ' (' + node + ')\nModel Name: AFF-A250\nAll-Flash Optimized: true\nCluster Name: ' + cluster + '\n',
            'volume.xml': xml_rows(records),
            'snapshot.xml': xml_rows(snapshots),
            'snapshot_policy.xml': xml_rows([{'n': 'daily-demo', 's': '', 'c': ''}]).replace('<s></s>', '<s><asup:li>daily</asup:li></s>').replace('<c></c>', '<c><asup:li>7</asup:li></c>'),
            'DF-A.txt': 'Aggregate kbytes used avail capacity\naggr_data ' + str(100 * 1024 ** 3) + ' ' + str(60 * 1024 ** 3) + ' ' + str(40 * 1024 ** 3) + ' 60%\n',
            'AGGR-STATUS-S.txt': 'Aggregate : aggr_data\nFeature Used Used%\nVolume Footprints 60TB 60%\nTotal Used 60TB 60%\nDemo_Bucket\nFeature Used Used%\nLogical Used 30TB -\nSpace Saved by Storage Efficiency 10TB -\n',
            'VOL-STATUS-F.txt': '\n'.join('Volume : ' + record['vol'] + '\nVserver : ' + record['vs'] + '\nFootprint in Performance Tier ' + ('6TB' if record['vol'] == 'FG' else '0B' if record['vol'] == 'zero' else '3TB') + '\nFootprint in demo-bucket ' + ('4TB' if record['vol'] == 'FG' else '0B' if record['vol'] == 'zero' else '2TB') + '\n' for record in records),
            'DF.txt': 'Filesystem kbytes used avail capacity Mounted on\n' + '\n'.join('/vol/' + record['vol'] + '/ ' + str(record.get('size', 0) // 1024) + ' ' + str(record.get('used', 0) // 1024) + ' ' + str(record.get('avail', 0) // 1024) + ' ' + str(record.get('pcnt_used', '0%')) + ' /vol/' + record['vol'] + '/' for record in records if record['vol'] != 'unknown'),
            'DF-I.txt': 'Filesystem iused ifree %iused\n' + '\n'.join('/vol/' + record['vol'] + '/ 200000000 800000000 20%' for record in records if record['vol'] != 'unknown'),
            'options.txt': 'cf.giveback.auto.enable off\ncf.giveback.auto.after.panic.takeover off\n',
            'VOL-STATUS-V.txt': '\n'.join('Volume name: ' + record['vol'] + '\nSnapshot autodelete settings: state=off' for record in records),
            'var-etc-ntp-conf.txt': 'server 192.0.2.1\nserver 192.0.2.2\nserver 192.0.2.3\n',
            'dns.xml': xml_rows([{'vserver': 'svm-one', 'nameservers': '192.0.2.1,192.0.2.2,192.0.2.3'}]),
            'data-object-store-config.xml': xml_rows([{'object-store-name': 'demo-bucket'}]),
            'nfs_servers_byname.xml': xml_rows([{'vserver': 'svm-one', 'v4.0': 'enabled', 'v41': 'enabled'}]),
            'network-routes.xml': xml_rows([{'route_vserver': 'svm-one', 'route_destination': '0.0.0.0/0', 'route_gateway': gateway, 'route_metric': 20} for gateway in ('192.0.2.1', '192.0.2.2')]),
            'sis-config.xml': xml_rows([{'vol': record['vol'], 'bg_cmpr': 'true', 'in_comp': 'true', 'in_dedp': 'true', 'compxn': 'true'} for record in records]),
            'object-store-bucket.xml': xml_rows([{'vserver': 'svm-one', 'bucket': 'demo-bucket', 'volume': 'fg_oss_demo', 'size_of_bucket': 50 * TB, 'logical_used_size': 20 * TB, 'object_count': 100000000}]),
            'export_rule_table.xml': xml_rows([{'vserver': 'svm-one', 'policyname': 'export-demo', 'ruleindex': 1, 'protocol': 'nfs', 'clientmatch': '192.0.2.0/24', 'rorule': 'sys', 'rwrule': 'sys'}]),
        }
        for name in ('storage-disk.xml', 'storage-shelf.xml', 'sp-info.xml', 'cshm-switch-config.xml', 'network-interface.xml', 'network-ports.xml', 'ifgrps.xml', 'device-discovery.xml'):
            files[name] = xml_rows([{'fixture': 'synthetic source present'}])
        files['storage-disk.xml'] = qualified_rows('http://asup_search.netapp.com/ns/T_DISK_INFO/1.0', [{'model': 'DEMO-DISK', 'disk-type': 'SSD', 'physical-size-mb': 1048576, 'firmware-revision': 'DEMO-FW'}])
        files['storage-shelf.xml'] = qualified_rows('http://asup_search.netapp.com/ns/T_STORAGE_SHELF/1.0', [{'shelf_name': 'demo-shelf-' + str(index), 'serial_number': 'DEMO-SHELF-' + str(index), 'product_id': 'DEMO-SHELF', 'disk_count': 12}])
        files['sp-info.xml'] = qualified_rows('http://asup_search.netapp.com/ns/T_SPINFO/1.0', [{'service-processor-type': 'BMC', 'status': 'online', 'public-ip-address': '192.0.2.' + str(index), 'firmware-version': '15.1'}])
        files['cshm-switch-config.xml'] = qualified_rows('http://asup_search.netapp.com/ns/T_cshm_switch_config/1.0', [{'Switch_Network': 'cluster', 'Device_Name': 'demo-switch-' + str(index), 'Model': 'DEMO-SWITCH'}])
        files['network-interface.xml'] = qualified_rows('http://asup_search.netapp.com/ns/T_VIF/1.0', [{'vserver': 'svm-one', 'vif': 'demo-lif-' + str(index), 'home_node': node, 'home_port': 'e0a', 'is_home': 'true', 'address': '192.0.2.' + str(10 + index), 'netmask': '255.255.255.0', 'role': 'data'}])
        files['network-ports.xml'] = qualified_rows('http://asup_search.netapp.com/ns/T_PORT/1.0', [{'node': node, 'port': 'e0a', 'link': 'up', 'mtu': 9000, 'role': 'data', 'type': 'physical', 'speed-actual': 10000}])
        files['ifgrps.xml'] = qualified_rows('http://asup_search.netapp.com/ns/T_IFGRP/1.0', [{'node': node, 'ifgrp-name': 'a0a', 'distr-func': 'ip', 'lacp': 'true', 'activeports': 'full'}])
        files['device-discovery.xml'] = qualified_rows('http://asup_search.netapp.com/ns/T_NET_DEVICE_DISCOVERY/1.0', [{'node': node, 'protocol': 'lldp', 'port': 'e0a', 'discovered-device': 'demo-switch-' + str(index), 'interface': 'Ethernet1'}])
        if scenario == 'missing-switch':
            del files['cshm-switch-config.xml']
        elif scenario == 'broken-switch':
            files['cshm-switch-config.xml'] = '<invalid'
        source = data / ('fixture-source-' + str(index))
        source.mkdir()
        for name, content in files.items():
            (source / name).write_text(content, encoding='utf-8')
        with gzip.open(source / 'EMS-LOG-FILE.gz', 'wt', encoding='utf-8') as stream:
            stream.write('<LR p="2" d="2026-11-23 12:00:00" n="' + node + '" o="demo">Synthetic critical alert</LR>')
        with py7zr.SevenZipFile(logs / ('unrelated-filename-' + str(index) + '-20261123.7z'), 'w') as archive:
            for path in source.iterdir():
                archive.write(path, arcname=path.name)
    save(data / 'expected.json', {'volumes': expected, 'nodes': ['demo-node-1', 'demo-node-2'], 'node_total_tb': 100, 'node_used_tb': 60, 'node_tiered_used_tb': 20, 'formula': '(30 - 10) TB from fabricated AGGR-STATUS-S; DF-A total=100TB, used=60TB', 'old_snapshot': {'included': 'old-demo', 'excluded': ['boundary-demo', 'recent-demo', 'child-demo']}})
    return logs


def review_report(root, destination, entry, scenario):
    import xml.etree.ElementTree as ET
    assertions = []
    def check(name, condition):
        assertions.append({'name': name, 'passed': bool(condition)})
    run_path = next((root / '.local/runs').glob('*/run.json'))
    run = json.loads(run_path.read_text(encoding='utf-8'))
    if scenario == 'broken-switch':
        check('invalid firmware XML stops actual normal entry', entry['status'] == 'failed')
        check('firmware failure is not treated as missing source', run['modules'].get('Firmware', {}).get('status') == 'failed' and not run.get('firmware_skipped_sheets'))
        check('no report is delivered after firmware parsing failure', not list((root / '.local/reports').rglob('*.xlsx')))
    elif entry['status'] == 'passed':
        workbook = load_workbook(entry['report'], data_only=False)
        try:
            check('six real subprocess modules completed', len(run['modules']) == 6 and all(value['status'] == 'success' for value in run['modules'].values()))
            check('exact final worksheet count', len(workbook.sheetnames) == (24 if scenario == 'missing-switch' else 25))
            check('export policy remains last', workbook.sheetnames[-1] == 'export-policy')
            check('best practice follows controller', workbook.sheetnames[workbook.sheetnames.index('Controller Report') + 1] == '最佳实践规范检查')
            coverage = json.loads(Path(run['coverage']['path']).read_text(encoding='utf-8'))
            expected_status = 'needs_review' if scenario in ('missing-switch', 'identity-gap') else 'ok'
            check('coverage state is honest for input quality', coverage['status'] == expected_status)
            checks = coverage['best_practice']
            check('all fourteen checks and Convert have audit records', set(checks) == {'check' + str(index) for index in range(1, 15)} | {'check10_convert'})
            if scenario != 'identity-gap':
                check('no silently missing or failed applicable check', not coverage['incomplete_checks'])
            expected = json.loads((root / '.local/data/expected.json').read_text(encoding='utf-8'))
            sheet = workbook['Volume List']
            headers = list(next(sheet.values))
            rows = [dict(zip(headers, row)) for row in list(sheet.values)[1:] if row[1]]
            actual = {(row['Cluster Name'], row['SVM名字'], row['Volume Name']): row for row in rows}
            check('full cluster SVM volume identities preserved', len(actual) == len(rows) == len(expected['volumes']) and set(actual) == {(row['cluster'], row['svm'], row['volume']) for row in expected['volumes']})
            for source in expected['volumes']:
                key = (source['cluster'], source['svm'], source['volume'])
                row = actual[key]
                for column, field in [('Volume Size (TB)', 'size_tb'), ('Used Size (TB)', 'used_tb')]:
                    check(str(key) + ' ' + column, row[column] == ('N/A' if source[field] is None else source[field]))
                if source['volume'] == 'zero':
                    check(str(key) + ' physical zero remains zero', row['Performance Tier (TB)'] == row['Capacity Tier (TB)'] == row['待下沉数据 (TB)'] == 0)
                elif source['volume'] == 'unknown':
                    check(str(key) + ' unknown inactive capacity is not zero', row['待下沉数据 (TB)'] == 'N/A')
                elif source['volume'] == 'vol0045_SHARED' and scenario == 'identity-gap':
                    check(str(key) + ' ambiguous tier identity is not guessed', row['Performance Tier (TB)'] == row['Capacity Tier (TB)'] == 'N/A')
                else:
                    check(str(key) + ' independent tier capacities', (row['Performance Tier (TB)'], row['Capacity Tier (TB)']) == ((6, 4) if source['volume'] == 'FG' else (3, 2)))
                if source['volume'] == 'vol0045_SHARED':
                    check(str(key) + ' logical usage and inactive bytes conversion', row['Logical Used Percent'] == '96.00%' and row['待下沉数据 (TB)'] == 1)
            capacity_rows = list(workbook['Controller Capacity'].values)[1:]
            check('all node capacities present', {row[1] for row in capacity_rows} == set(expected['nodes']))
            source_tree = ET.parse(root / '.local/data/fixture-source-1/volume.xml')
            allocated = sum(int(row.findtext('size') or 0) for row in source_tree.getroot()) / TB
            for row in capacity_rows:
                check(row[1] + ' raw source capacity arithmetic', row[3:9] == (100, 60, '60%', allocated, 30 - 10, str(int(allocated / (100 + 30 - 10) * 100)) + '%'))
            bucket_rows = list(workbook['Bucket'].values)
            check('bucket size and used independent from backing volume', all(row[4] == 50 and row[6] == 20 and row[7] == '40%' for row in bucket_rows[1:3]))
            check('backing FlexGroup and constituents retained', sum(row[1] in ('fg_oss_demo', 'fg_oss_demo__0001') for row in bucket_rows) == 4)
            bp = workbook['最佳实践规范检查']
            titles = {int(match.group(1)) for row in bp for cell in row[:1] if isinstance(cell.value, str) and (match := re.match(r'^Check (\d+):', cell.value))}
            check('fourteen visible check sections', titles == set(range(1, 15)))
            start = next(row[0].row for row in bp if str(row[0].value).startswith('Check 13:'))
            end = next(row[0].row for row in bp if str(row[0].value).startswith('Check 14:'))
            old = [tuple(cell.value for cell in row) for row in bp.iter_rows(min_row=start + 1, max_row=end - 1) if row[0].value in ('demo-cluster-01', 'demo-cluster-02')]
            check('strict greater than 120 days and child exclusion', len(old) == 2 and all(row[3:8] == ('vol0045_SHARED', 'old-demo', 2, 'Sat Jul 25 12:00:00 2026', 121) for row in old))
            check('configured snapshot-policy scope', checks['check14']['issue_count'] == 2 and all(row['cluster'] == 'demo-cluster-01' for row in checks['check14']['archives']))
            convert = json.loads((run_path.parent / 'BestPractice/convert_evidence.json').read_text(encoding='utf-8'))
            recommendations = [row for row in convert['evaluations'] if row['eligible']]
            check('Convert six-month independent growth arithmetic', len(recommendations) == 2 and all(row['elapsed_months'] == 6 and row['growth_percent'] == (24 - 16) / 16 * 100 and row['growth_tb'] == 8 and row['start_date'] == '2026-05-31' for row in recommendations))
            check('latest BMC firmware from synthetic knowledge base', all(row[4] == 'BMC' and row[8] == '15.99' for row in list(workbook['BMC&SP'].values)[1:]))
            check('network cluster identity from source configuration', {row[0] for row in list(workbook['Network Interfaces'].values)[1:]} == {'demo-cluster-01', 'demo-cluster-02'})
            check('network and EMS fabricated records preserved', all(workbook[name].max_row >= 3 for name in ('Network Interfaces', 'Network Port', 'IFGRP', 'Network Discovery', 'EMS告警')))
            case_values = [cell.value for name in ('硬件case', '软件case', '非技术case') for row in workbook[name] for cell in row]
            check('case matching retains only matching serials', all('DEMO-CASE-' + str(index) in case_values for index in range(1, 4)) and 'UNMATCHED-CASE' not in case_values)
            bad_alignment = [cell.coordinate for sheet in workbook if sheet.title not in ('Cover', 'Index') for row in sheet for cell in row if isinstance(cell.value, (int, float)) and cell.alignment.horizontal != 'center']
            check('every numeric cell is centered', not bad_alignment)
            links = [cell.hyperlink.location or cell.hyperlink.target for row in workbook['Index'] for cell in row if cell.hyperlink]
            targets = [link.lstrip('#').split('!')[0].strip("'") for link in links]
            check('index hyperlinks point to existing sheets', len(targets) >= 6 and all(target in workbook.sheetnames for target in targets))
            check('fourteen-item cover text updated', any('14 items' in str(cell.value) for row in workbook['Cover'] for cell in row))
            if scenario == 'missing-switch':
                check('only Switch skipped when its source is absent', 'Switch' not in workbook.sheetnames and run['firmware_skipped_sheets'] == ['Switch'])
            check('all check section titles are merged', all(any(area.min_row == row[0].row and area.max_row == row[0].row and area.min_col == 1 and area.max_col > 1 for area in bp.merged_cells.ranges) for row in bp if re.match(r'^Check \d+:', str(row[0].value))))
        finally:
            workbook.close()
    else:
        check('actual normal entry completed', False)
    result = {'status': 'passed' if all(row['passed'] for row in assertions) else 'failed', 'assertions': len(assertions), 'failures': [row['name'] for row in assertions if not row['passed']], 'checks': assertions}
    save(destination / 'quality.json', result)
    return result


CHILD = r'''
import importlib.util,json,os,sys,traceback
from pathlib import Path
root=Path(sys.argv[1]); logs=Path(sys.argv[2]); destination=Path(sys.argv[3])
shared=root/'.local/runtime/skills/NetApp-SAM-Report'
sys.path.insert(0,str(shared))
spec=importlib.util.spec_from_file_location('candidate_report',shared/'run.py')
report=importlib.util.module_from_spec(spec); spec.loader.exec_module(report)
results={}
try:
    path=report.process_directory(str(logs),str(root/'.local/reports'),True)
    results['normal_entry']={'status':'passed','report':path}
except Exception as error:
    results['normal_entry']={'status':'failed','error_type':type(error).__name__,'error':str(error),'traceback':traceback.format_exc()}
(destination/'normal-entry.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
'''


def validate(build, destination, scenario='complete', archive_path=None):
    destination = destination.resolve()
    destination.relative_to((PROJECT / '.local').resolve())
    destination.mkdir(parents=True, exist_ok=False)
    if archive_path is None:
        receipt = json.loads((build / 'build.json').read_text(encoding='utf-8'))
        archive = Path(receipt['archive'])
    else:
        archive = Path(archive_path).resolve()
        receipt = {'archive_sha256': digest(archive)}
    if digest(archive) != receipt['archive_sha256']:
        raise ValueError('Candidate archive fingerprint changed')
    root = destination / 'independent project'
    extract(archive, root)
    home = destination / 'empty-home'
    home.mkdir()
    temporary = destination / 'temp'
    temporary.mkdir()
    guard = destination / 'guard'
    guard.mkdir()
    shutil.copy2(TEMPLATES / 'isolation_guard.py', guard / 'sitecustomize.py')
    environment = {key: value for key, value in os.environ.items() if not key.startswith(('SAM_', 'PYTHON', 'CANDIDATE_'))}
    environment.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home), LOCALAPPDATA=str(home),
                       TEMP=str(temporary), TMP=str(temporary), PYTHONPATH=str(guard), PYTHONIOENCODING='utf-8',
                       PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', CANDIDATE_ALLOWED_ROOT=str(destination),
                       CANDIDATE_GUARD_LOG=str(destination / 'access.jsonl'))
    def execute(label, command):
        result = subprocess.run([sys.executable, '-B'] + [str(value) for value in command], cwd=home,
                                env=environment, capture_output=True, encoding='utf-8', timeout=900)
        (destination / (label + '.stdout.log')).write_text(result.stdout, encoding='utf-8')
        (destination / (label + '.stderr.log')).write_text(result.stderr, encoding='utf-8')
        if result.returncode:
            raise RuntimeError('Validation command failed: ' + label)
    execute('install', [root / 'tools/candidate_runtime.py', 'install'])
    logs = prepare(root, complete=scenario != 'identity-gap', scenario=scenario)
    environment.update(SAM_PROJECT_ROOT=str(root), SAM_PROJECT_CONFIG=str(root / '.local/config/project.json'),
                       SAM_SHARED_SKILL_DIR=str(root / '.local/runtime/skills/NetApp-SAM-Report'),
                       SAM_EVALUATION_TIME='2026-11-23T12:00:00+08:00')
    execute('normal-entry', ['-c', CHILD, root, logs, destination])
    entry = json.loads((destination / 'normal-entry.json').read_text(encoding='utf-8'))['normal_entry']
    quality = review_report(root, destination, entry, scenario)
    access = [json.loads(line) for line in (destination / 'access.jsonl').read_text(encoding='utf-8').splitlines()]
    denied = [row for row in access if row.get('allowed') is False]
    passed = quality['status'] == 'passed' and not denied
    status = ('negative_scenario_passed' if scenario == 'broken-switch' else 'numerical_and_structural_validation_passed') if passed else 'validation_failed'
    summary = {'status': status,
               'archive_sha256': receipt['archive_sha256'], 'normal_entry': entry,
               'synthetic_archives': 2, 'fixtures': 'entirely fabricated; no production ASUP or knowledge-base reads',
               'unexpected_external_accesses': len(denied), 'production_written': False,
               'full_synthetic_report_accepted': False,
               'not_yet_verified': ['rendered workbook quality', 'production deployment', 'clean Git checkout'],
               'safety': 'No compatibility shim, home-directory Skill copy, patched function or lowered gate was used to bypass the real entrypoint.'}
    summary.update(scenario=scenario, quality=quality, numerical_and_structural_validation_passed=passed,
                   visual_review_pending=True)
    save(destination / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--build', type=Path)
    source.add_argument('--archive', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scenario', choices=('complete', 'missing-switch', 'broken-switch', 'identity-gap'), default='complete')
    arguments = parser.parse_args()
    result = validate(arguments.build.resolve() if arguments.build else None, arguments.output, arguments.scenario, arguments.archive)
    raise SystemExit(0 if result['numerical_and_structural_validation_passed'] else 1)
