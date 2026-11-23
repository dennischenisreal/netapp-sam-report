import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

import py7zr
from openpyxl import load_workbook
from sam_coverage import build_inventory, reconcile_outputs
from sam_settings import CONFIG_PATH, path_setting, configuration, configuration_path, project_root
from customer_profiles import load_profiles, profile_path


from sam_package_compat import require_configuration

VERSION = '0.5.0'
WORKSPACE = project_root().parent
SKILLS_ROOT = path_setting('skills_root', project_root() / '.local/runtime/skills')
LEGACY_ROOT = path_setting('legacy_root', project_root() / '.local/runtime/legacy')
MODULE_SHEETS = {
    'Capacity': ['Controller Capacity', 'TOP aggr', 'TOP inode', 'TOP vol',
                 'FlexGroup', 'Volume List', 'Bucket', 'export-policy'],
    'Controller': ['Controller Report'],
    'Firmware': ['Disk Firmware', 'Shelf Firmware', 'Switch', 'BMC&SP'],
    'Case': ['年度对比', '硬件case', '软件case', '非技术case'],
    'Network': ['Network Interfaces', 'Network Port', 'IFGRP', 'Network Discovery', 'EMS告警'],
    'BestPractice': ['BestPracticeCheck'],
}
FIRMWARE_SOURCE_FILES = {
    'Disk Firmware': 'storage-disk.xml',
    'Shelf Firmware': 'storage-shelf.xml',
    'Switch': 'cshm-switch-config.xml',
    'BMC&SP': 'sp-info.xml',
}
FIRMWARE_STATUS_SHEET = 'Firmware Source Status'


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    os.replace(temporary, path)


def describe_file(path):
    path = Path(path).resolve()
    stat = path.stat()
    return {'path': str(path), 'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
            'sha256': sha256(path)}


def sample_period(input_dir, archives):
    year_match = re.findall(r'(?<!\d)(20\d{2})(?!\d)', str(input_dir))
    archive_dates = []
    for archive in archives:
        for match in re.finditer(r'(?<!\d)(20\d{2})(\d{2})(\d{2})(?=\d|[^\d]|$)', archive.name):
            try:
                archive_dates.append(date(*map(int, match.groups())))
            except ValueError:
                continue
    year = int(year_match[-1]) if year_match else (max(archive_dates).year if archive_dates else None)
    for part in reversed(Path(input_dir).parts):
        match = re.search(r'(?<!\d)(1[0-2]|0?[1-9])月(?:(3[01]|[12]\d|0?[1-9])日?)?', part)
        if match:
            month, day = int(match[1]), int(match[2]) if match[2] else None
            if year and day:
                sampled = date(year, month, day)
                return {'year': year, 'month': month, 'day': day,
                        'sample_date': sampled.isoformat(), 'date_source': 'requested_log_directory'}
            matching = [item for item in archive_dates if item.month == month and (not year or item.year == year)]
            sampled = max(matching) if matching else None
            return {'year': year, 'month': month, 'day': sampled.day if sampled else None,
                    'sample_date': sampled.isoformat() if sampled else None,
                    'date_source': 'archive_filename' if sampled else 'month_only_directory'}
    sampled = max(archive_dates) if archive_dates else None
    return {'year': sampled.year if sampled else year, 'month': sampled.month if sampled else None,
            'day': sampled.day if sampled else None,
            'sample_date': sampled.isoformat() if sampled else None,
            'date_source': 'archive_filename' if sampled else 'unknown'}


class RunContext:
    def __init__(self, input_dir, customer_name, month_part, runs_root=None):
        self.input_dir = Path(input_dir).resolve()
        archives = sorted(path for path in self.input_dir.rglob('*')
                          if path.is_file() and path.suffix.lower() == '.7z')
        if not archives:
            raise FileNotFoundError(f'No ASUP 7z archives found: {self.input_dir}')
        self.run_id = datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '_' + uuid.uuid4().hex[:8]
        root = Path(runs_root) if runs_root else project_root() / '.local' / 'runs'
        self.directory = root / self.run_id
        self.directory.mkdir(parents=True, exist_ok=False)
        self.manifest_path = self.directory / 'run.json'
        self.customer_name = customer_name
        self.paths = {}
        self.manifest = {
            'run_id': self.run_id, 'runtime_version': VERSION, 'status': 'preparing',
            'created_at': datetime.now().astimezone().isoformat(), 'input_dir': str(self.input_dir),
            'customer': customer_name, 'month_label': month_part, 'case_year': datetime.now().year,
            'period': sample_period(self.input_dir, archives),
            'inputs': [describe_file(path) for path in archives], 'modules': {},
            'python_executable': sys.executable, 'sources': [], 'knowledge_base': [],
            'evaluation_time': os.environ.get('SAM_EVALUATION_TIME') or datetime.now().astimezone().isoformat(),
        }
        self.save()

    def save(self):
        write_json(self.manifest_path, self.manifest)

    def capture_versions(self):
        config_path = configuration_path() if 'SAM_PROJECT_CONFIG' in os.environ or 'SAM_PROJECT_ROOT' in os.environ else CONFIG_PATH
        if config_path.is_file():
            configuration()
            if path_setting('skills_root', Path.home() / '.codex' / 'skills') != SKILLS_ROOT or path_setting('legacy_root', Path.home() / 'Desktop' / 'demo' / 'Trae Solo Coder') != LEGACY_ROOT:
                raise RuntimeError('Runtime roots changed after module import; start a new process')
            original_config = describe_file(config_path)
            config_snapshot = self.directory / 'project-config.json'
            with config_snapshot.open('xb') as stream:
                stream.write(config_path.read_bytes())
            captured_config = describe_file(config_snapshot)
            if captured_config['sha256'] != original_config['sha256'] or sha256(config_path) != original_config['sha256']:
                raise RuntimeError('Project configuration changed while capturing the run snapshot')
            self.manifest['project_configuration'] = {
                'original': original_config, 'snapshot': captured_config,
                'project_root': str(project_root())}
        elif 'SAM_PROJECT_CONFIG' in os.environ or 'SAM_PROJECT_ROOT' in os.environ:
            raise FileNotFoundError('Explicit SAM project configuration is missing')
        load_profiles()
        original_profile = describe_file(profile_path())
        snapshot = self.directory / 'customer-profiles.local.json'
        with snapshot.open('xb') as stream:
            stream.write(Path(original_profile['path']).read_bytes())
        snapshot_profile = describe_file(snapshot)
        load_profiles()
        if snapshot_profile['sha256'] != original_profile['sha256'] or sha256(original_profile['path']) != original_profile['sha256']:
            raise RuntimeError('Customer rules changed while capturing the run snapshot')
        self.manifest['customer_profiles'] = {'original': original_profile, 'snapshot': snapshot_profile}
        roots = [SKILLS_ROOT / name for name in (
            'NetApp-SAM-Report', '1-netapp-controller-eos-ma', '2-netapp-capacity-aggr-vol-fg',
            '3-jv-case', '4-netapp-firmware-shelf-io-sp', '5-netapp-network-ems', '6-best-practice-check',
            'NetApp Capacity Trend and Case Report')]
        paths = []
        if config_path.is_file():
            paths.append(config_path)
            paths.append(config_snapshot)
        for root in roots:
            paths.extend(path for path in root.rglob('*') if path.suffix in ('.py', '.json', '.md')
                         and '__pycache__' not in path.parts)
        paths.extend([LEGACY_ROOT / 'ASUP Analysis' / 'parse_asup_logs_final.py',
                      LEGACY_ROOT / 'ASUP Analysis' / 'generate_all_final.py',
                      LEGACY_ROOT / 'Case Analysis' / 'parse_c1_case.py'])
        self.manifest['sources'] = [describe_file(path) for path in sorted(set(paths)) if path.is_file()]
        self.manifest['sources'].extend([original_profile, snapshot_profile])
        kb_root = path_setting('knowledge_root', WORKSPACE / '知识库')
        self.manifest['knowledge_base'] = [describe_file(path) for path in sorted(kb_root.rglob('*.xlsx'))
                                           if not path.name.startswith('~$')]
        self.manifest['status'] = 'running'
        self.save()

    def execute(self, module, script, arguments, expected_name):
        folder = self.directory / module
        folder.mkdir(exist_ok=False)
        expected = folder / expected_name
        log_path = self.directory / (module + '.log')
        record = {'status': 'running', 'script': str(script), 'output': str(expected),
                  'log': str(log_path), 'started_at': datetime.now().astimezone().isoformat()}
        self.manifest['modules'][module] = record
        self.save()
        env = os.environ.copy()
        env.update({'SAM_REPORT_OUTPUT_DIR': str(folder), 'PYTHONIOENCODING': 'utf-8',
                    'PYTHONUTF8': '1', 'SAM_REPORT_RUN_ID': self.run_id,
                    'SAM_REPORT_CUSTOMER': self.customer_name})
        env['SAM_SHARED_SKILL_DIR'] = str(SKILLS_ROOT / 'NetApp-SAM-Report')
        env['SAM_EVALUATION_TIME'] = self.manifest['evaluation_time']
        if self.manifest.get('customer_profiles'):
            profile = self.manifest['customer_profiles']['snapshot']
            env['SAM_CUSTOMER_PROFILES'] = profile['path']
        if self.manifest['period'].get('sample_date'):
            env['SAM_SAMPLE_DATE'] = self.manifest['period']['sample_date']
        if self.manifest.get('source_inventory'):
            env['SAM_SOURCE_INVENTORY'] = self.manifest['source_inventory']['path']
        if self.manifest.get('capacity_params'):
            env['SAM_CAPACITY_PARAMS'] = self.manifest['capacity_params']['path']
        command = [sys.executable, '-u', str(script)] + [str(value).replace('{output}', str(expected)).replace('{folder}', str(folder))
                                                       for value in arguments]
        print(f'  Running {module}; log: {log_path}', flush=True)
        try:
            self.require_configuration()
            if self.manifest.get('project_configuration'):
                settings = self.manifest['project_configuration']
                env['SAM_PROJECT_CONFIG'] = settings['snapshot']['path']
                env['SAM_PROJECT_ROOT'] = settings['project_root']
            if self.manifest.get('customer_profiles'):
                profile = self.manifest['customer_profiles']['snapshot']
                if sha256(profile['path']) != profile['sha256']:
                    raise RuntimeError('Customer rule snapshot changed before module execution')
            with log_path.open('w', encoding='utf-8') as stream:
                result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT,
                                        env=env, check=False)
            record['returncode'] = result.returncode
            if result.returncode:
                raise RuntimeError(f'{module} exited with code {result.returncode}; see {log_path}')
            if module == 'Firmware':
                self.prepare_firmware_sources()
            self.validate_output(module, expected)
            convert_path = folder / 'convert_evidence.json'
            if module == 'BestPractice' and convert_path.is_file():
                evidence = json.loads(convert_path.read_text(encoding='utf-8'))
                for source in evidence['sources']:
                    if sha256(source['path']) != source['sha256']:
                        raise RuntimeError('Convert history changed during processing: ' + source['path'])
                record['convert_evidence'] = describe_file(convert_path)
                record['history_inputs'] = evidence['sources']
            record.update({'status': 'success', 'file': describe_file(expected)})
            self.paths[module] = str(expected)
        except BaseException as error:
            record.update({'status': 'failed', 'error': str(error)})
            self.manifest['status'] = 'failed'
            raise
        finally:
            record['finished_at'] = datetime.now().astimezone().isoformat()
            self.save()

    def validate_output(self, module, path):
        path = Path(path).resolve()
        path.relative_to(self.directory.resolve())
        if not path.is_file():
            raise FileNotFoundError(f'{module} produced no current-run workbook: {path}')
        workbook = load_workbook(path, read_only=True)
        try:
            required = set(MODULE_SHEETS[module])
            if module == 'Firmware':
                skipped = set(self.firmware_skipped_sheets())
                required -= skipped
                expected = required or {FIRMWARE_STATUS_SHEET}
                if set(workbook.sheetnames) != expected:
                    raise ValueError(f'Firmware sheets disagree with source evidence: {workbook.sheetnames}')
            missing = required - set(workbook.sheetnames)
            if missing:
                raise ValueError(f'{module} missing sheets: {sorted(missing)}')
            if module in ('Controller', 'Capacity'):
                sheet = workbook['Controller Report' if module == 'Controller' else 'Controller Capacity']
                if sheet.max_row < 2:
                    raise ValueError(f'{module} contains no node data')
        finally:
            workbook.close()

    def prepare_firmware_sources(self):
        sources = {sheet: {'source_file': filename, 'available_archives': [], 'missing_archives': []}
                   for sheet, filename in FIRMWARE_SOURCE_FILES.items()}
        for record in self.manifest['inputs']:
            if sha256(record['path']) != record['sha256']:
                raise RuntimeError('Firmware input changed before source inspection')
            with py7zr.SevenZipFile(record['path'], 'r') as archive:
                names = {Path(name.replace('\\', '/')).name.lower() for name in archive.getnames()}
            for entry in sources.values():
                key = 'available_archives' if entry['source_file'] in names else 'missing_archives'
                entry[key].append(record['path'])
        for entry in sources.values():
            entry['status'] = ('missing_source' if not entry['available_archives'] else
                               'partial' if entry['missing_archives'] else 'available')
        path = self.directory / 'firmware_sources.json'
        write_json(path, {'schema_version': 1, 'sheets': sources})
        self.manifest['firmware_sources'] = describe_file(path)
        self.manifest['firmware_skipped_sheets'] = self.firmware_skipped_sheets()
        self.save()

    def firmware_skipped_sheets(self):
        record = self.manifest.get('firmware_sources')
        if record is None:
            return []
        path = Path(record['path']).resolve()
        path.relative_to(self.directory.resolve())
        if sha256(path) != record['sha256']:
            raise RuntimeError('Firmware source evidence changed after inspection')
        evidence = json.loads(path.read_text(encoding='utf-8'))
        if evidence.get('schema_version') != 1 or set(evidence['sheets']) != set(FIRMWARE_SOURCE_FILES):
            raise ValueError('Invalid firmware source evidence')
        return [sheet for sheet, entry in evidence['sheets'].items()
                if entry['status'] == 'missing_source' and not entry['available_archives']]

    def require_configuration(self):
        settings = self.manifest.get('project_configuration')
        if settings:
            for source in (settings['original'], settings['snapshot']):
                if sha256(source['path']) != source['sha256']:
                    raise RuntimeError('Project configuration changed during the run')

    def require_complete(self):
        self.require_configuration()
        for module in MODULE_SHEETS:
            if self.manifest['modules'].get(module, {}).get('status') != 'success':
                raise RuntimeError(f'Cannot assemble report: {module} did not succeed in this run')
            path = self.paths[module]
            self.validate_output(module, path)
            if sha256(path) != self.manifest['modules'][module]['file']['sha256']:
                raise RuntimeError(f'{module} output changed after validation')
            record = self.manifest['modules'][module]
            if record.get('convert_evidence') and sha256(record['convert_evidence']['path']) != record['convert_evidence']['sha256']:
                raise RuntimeError('Convert evidence changed after validation')
            for source in record.get('history_inputs', []):
                if sha256(source['path']) != source['sha256']:
                    raise RuntimeError('Convert history changed after validation: ' + source['path'])

    def prepare_coverage(self):
        print('  独立核对归档配置、节点及数据文件覆盖...', flush=True)
        inventory = build_inventory(self.input_dir)
        inventory_path = self.directory / 'source_inventory.json'
        write_json(inventory_path, inventory)
        self.manifest['source_inventory'] = describe_file(inventory_path)
        self.save()
        errors = [record for record in inventory['archives'] if record['errors']]
        if errors:
            raise RuntimeError(f'{len(errors)}个归档读取失败；不能把缺失来源当作无问题，详见 {inventory_path}')

    def reconcile_coverage(self):
        for record in self.manifest['sources'] + self.manifest['knowledge_base']:
            if sha256(record['path']) != record['sha256']:
                raise RuntimeError('代码或知识库在运行期间被改动，需重新运行以保证可追溯: ' + record['path'])
        if sha256(self.manifest['source_inventory']['path']) != self.manifest['source_inventory']['sha256']:
            raise RuntimeError('原始覆盖清单在运行期间被改动')
        for record in self.manifest['inputs']:
            if sha256(record['path']) != record['sha256']:
                raise RuntimeError('输入归档在运行期间被改动: ' + record['path'])
        inventory = json.loads(Path(self.manifest['source_inventory']['path']).read_text(encoding='utf-8'))
        params = json.loads(Path(self.manifest['capacity_params']['path']).read_text(encoding='utf-8'))
        coverage = reconcile_outputs(inventory, self.paths, params)
        capacity_path = Path(self.paths['Capacity']).with_suffix('.capacity-audit.json')
        if not capacity_path.is_file():
            raise FileNotFoundError('缺少容量分层身份及缺失值审计记录，停止报告合并')
        capacity_audit = json.loads(capacity_path.read_text(encoding='utf-8'))
        if capacity_audit.get('schema_version') != 1 or capacity_audit.get('status') not in ('ok', 'needs_review'):
            raise RuntimeError('容量审计格式或状态无效，停止报告合并')
        coverage['capacity_detail'] = capacity_audit
        self.manifest['capacity_audit'] = describe_file(capacity_path)
        bp_path = Path(self.paths['BestPractice']).with_suffix('.coverage.json')
        if not bp_path.is_file():
            raise FileNotFoundError('缺少最佳实践覆盖状态文件，不能确认检查是否完整')
        coverage['best_practice'] = json.loads(bp_path.read_text(encoding='utf-8'))['checks']
        incomplete = [key for key, record in coverage['best_practice'].items()
                      if record['status'] in ('failed', 'missing_data')]
        coverage['incomplete_checks'] = incomplete
        firmware_incomplete = False
        if self.manifest.get('firmware_sources'):
            self.firmware_skipped_sheets()
            firmware = json.loads(Path(self.manifest['firmware_sources']['path']).read_text(encoding='utf-8'))
            coverage['firmware'] = firmware
            firmware_incomplete = any(entry['status'] != 'available' for entry in firmware['sheets'].values())
        coverage['verification_levels'] = {
            'Controller': '原始配置节点与结果节点逐一核对',
            'Capacity': '原始配置节点、合规过滤后的卷身份与结果逐一核对；非容量公式重算',
            'BestPractice': '逐归档检查来源、解析异常及检查状态',
            'Case': '进程、文件及Sheet结构校验；未独立重算所有Case匹配',
            'Firmware': '进程、文件及Sheet结构校验；未逐设备重算固件条目',
            'Network': '进程、文件及Sheet结构校验；未逐接口重算网络条目',
        }
        if coverage['status'] == 'ok' and (incomplete or firmware_incomplete or capacity_audit['status'] == 'needs_review'):
            coverage['status'] = 'needs_review'
        destination = self.directory / 'coverage.json'
        write_json(destination, coverage)
        self.manifest['coverage'] = describe_file(destination)
        self.manifest['quality_status'] = coverage['status']
        self.save()
        if coverage['status'] == 'failed':
            raise RuntimeError(f'节点或卷覆盖核对不完整，停止正式报告合并；缺口详情: {destination}')
        if coverage['status'] == 'needs_review':
            print(f'  注意：{len(incomplete)}项检查存在缺失/失败来源，报告中明确标识；详情: {destination}', flush=True)
            if capacity_audit['status'] == 'needs_review':
                print(f'  容量审计包含缺失字段或待确认身份，未知值显示N/A；详情: {capacity_path}', flush=True)

    def require_coverage(self):
        self.require_configuration()
        audit = self.manifest.get('capacity_audit')
        if not audit or sha256(audit['path']) != audit['sha256']:
            raise RuntimeError('容量审计记录缺失或被改动，禁止合并')
        record = self.manifest.get('coverage')
        if not record or sha256(record['path']) != record['sha256']:
            raise RuntimeError('缺少有效覆盖核对记录，禁止绕过完整性检查直接合并')
        coverage = json.loads(Path(record['path']).read_text(encoding='utf-8'))
        if coverage['status'] == 'failed':
            raise RuntimeError('覆盖核对失败，禁止正式交付')

    def finish(self, path=None, error=None):
        self.manifest['status'] = 'failed' if error else 'success'
        self.manifest['finished_at'] = datetime.now().astimezone().isoformat()
        if error:
            self.manifest['error'] = str(error)
        if path:
            self.manifest['report'] = describe_file(path)
            self.manifest['sample_date'] = self.manifest['period']['sample_date']
            write_json(Path(path).with_suffix('.run.json'), self.manifest)
            if self.manifest.get('coverage'):
                coverage = json.loads(Path(self.manifest['coverage']['path']).read_text(encoding='utf-8'))
                write_json(Path(path).with_suffix('.coverage.json'), coverage)
        self.save()


def validate_final_workbook(workbook, skipped_firmware=()):
    skipped_firmware = set(skipped_firmware)
    if not skipped_firmware.issubset(FIRMWARE_SOURCE_FILES):
        raise ValueError('Only source-missing firmware sheets may be omitted')
    required = {'Cover', 'Index', '最佳实践规范检查'}
    for module, sheets in MODULE_SHEETS.items():
        if module != 'BestPractice':
            required.update(sheets)
    required -= skipped_firmware
    if set(workbook.sheetnames) != required:
        raise ValueError(f'Unexpected report sheets: missing={sorted(required - set(workbook.sheetnames))}, '
                         f'extra={sorted(set(workbook.sheetnames) - required)}')
    for row in workbook['Index']:
        for cell in row:
            if cell.hyperlink and cell.hyperlink.location:
                target = cell.hyperlink.location.split('!')[0].strip("'")
                if target not in workbook.sheetnames:
                    raise ValueError(f'Broken Index hyperlink: {cell.coordinate}')
    return True
