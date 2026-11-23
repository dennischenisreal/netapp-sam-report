from pathlib import Path
import os
import sys
_package_shared = os.environ.get('SAM_SHARED_SKILL_DIR') or str(Path(__file__).resolve().parents[1] / 'NetApp-SAM-Report')
if not Path(_package_shared).is_absolute() or not (Path(_package_shared) / 'sam_package_compat.py').is_file():
    raise FileNotFoundError('Explicit candidate shared runtime directory is required')
sys.path.insert(0, _package_shared)
from sam_package_compat import package_path

#!/usr/bin/env python3
"""
NetApp ASUP Best Practice Check

检查NetApp存储的最佳实践配置，包括：
1. cf.giveback.auto.enable 和 cf.giveback.auto.after.panic.takeover
2. volume autosize
3. snapshot autodelete
4. AFF volume logical space reporting/enforcement
5. NTP server数量
6. DNS server数量
7. tiering policy
8. NFSv4/NFSv4.1
9. volume space thresholds
10. FlexGroup平衡性
11. AFF去重压缩
12. 网络路由配置
13. 老旧Snapshot清理
14. PROJ/PROJRUN卷Snapshot Policy合规

用法:
    python run.py <7z文件所在目录或父目录> [输出目录]
"""
import sys
import os
import re
import gc
from copy import copy
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import py7zr
except ImportError:
    print("错误: 需要安装 py7zr 库")
    print("请运行: pip install py7zr")
    sys.exit(1)

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
from check_audit import BestPracticeAudit, observe_extraction, record_error, record_snapshot_duplicates
from customer_profiles import customer_name as configured_customer_name, check_enabled, check_message, target_clusters as configured_target_clusters


class ArchiveExtractor:
    """ASUP 7z 压缩包解压器"""
    def __init__(self):
        self._temp_dirs = []

    def cleanup(self):
        for d in self._temp_dirs:
            if os.path.exists(d):
                import shutil
                shutil.rmtree(d, ignore_errors=True)
        self._temp_dirs.clear()

    def extract_files(self, archive_path, target_files):
        import tempfile
        temp_dir = tempfile.mkdtemp(prefix='bp_')
        self._temp_dirs.append(temp_dir)
        result = {f: '' for f in target_files}

        try:
            with py7zr.SevenZipFile(archive_path, mode='r') as archive:
                file_list = archive.getnames()
                to_extract = []
                for target in target_files:
                    for f in file_list:
                        if target.lower() in f.lower():
                            to_extract.append(f)
                            break
                if not to_extract:
                    return result
                archive.extract(targets=to_extract, path=temp_dir)

            for root, _, files in os.walk(temp_dir):
                for fname in files:
                    for target in target_files:
                        if target.lower() in fname.lower() and not result[target]:
                            result[target] = os.path.join(root, fname)
        except Exception as e:
            record_error(archive_path, e)
        finally:
            observe_extraction(archive_path, target_files, result)

        return result


def extract_node_and_cluster(archive_path, extractor):
    """从归档内的配置文件提取node name和cluster name

    优先从 CLUSTER-INFO.xml 中 is-self=true 的行获取本归档对应的节点名，
    回退到 SYSCONFIG-A.txt 的 System ID 行；不使用归档文件名推断。
    """
    result = extractor.extract_files(archive_path, ['SYSCONFIG-A.txt', 'CLUSTER-INFO.xml'])

    node_name = ''
    cluster_name = ''

    cluster_info_path = result.get('CLUSTER-INFO.xml', '')
    if cluster_info_path and os.path.exists(cluster_info_path):
        with open(cluster_info_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        rows = re.findall(r'<asup:ROW[^>]*>(.*?)</asup:ROW>', content, re.DOTALL)
        if not rows:
            rows = re.findall(r'<ROW[^>]*>(.*?)</ROW>', content, re.DOTALL)
        for row in rows:
            m_clu = re.search(r'<cluster-name[^>]*>(.*?)</cluster-name>', row, re.DOTALL)
            if m_clu and not cluster_name:
                cluster_name = m_clu.group(1).strip()
            m_self = re.search(r'<is-self>(.*?)</is-self>', row, re.DOTALL)
            m_node = re.search(r'<node[^>]*>(.*?)</node>', row, re.DOTALL)
            if m_self and m_self.group(1).strip().lower() == 'true' and m_node:
                node_name = m_node.group(1).strip()
        os.remove(cluster_info_path)

    sysconfig_path = result.get('SYSCONFIG-A.txt', '')
    if not node_name and sysconfig_path and os.path.exists(sysconfig_path):
        with open(sysconfig_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        m = re.search(r'System ID:\s*\S+\s*\(([^)]+)\)', content)
        if not m:
            m = re.search(r'System Serial Number:\s*\S+\s*\(([^)]+)\)', content)
        if m:
            node_name = m.group(1).strip()
    if sysconfig_path and os.path.exists(sysconfig_path):
        os.remove(sysconfig_path)

    
    if not cluster_name and node_name:
        m = re.match(r'^(.*?)[-_]?\d+$', node_name)
        if m:
            cluster_name = m.group(1)
        else:
            m = re.match(r'^(.*?)[_\-]?([A-Za-z])$', node_name)
            if m:
                cluster_name = m.group(1)
            else:
                cluster_name = node_name

    return node_name, cluster_name


def should_exclude_volume(vol_name):
    """判断是否需要排除该volume（FlexGroup子卷、系统/根卷、MDV_CRS系统卷）
    注意: 只精确排除系统根卷(vol0 / root / root_* / *_root)，禁止用'vol0'/'root'
    子串匹配，否则 vol00xx_* 等客户数据卷(如vol0045_SHPROJ_SDCPB)会被误过滤。"""
    if not vol_name:
        return True
    vol_lower = vol_name.lower()
    if re.search(r'__\d+$', vol_name):
        return True
    if vol_lower in ('vol0', 'root') or vol_lower.startswith('root_') or vol_lower.endswith('_root'):
        return True
    if vol_lower.startswith('mdv_crs'):
        return True
    return False


def deduplicate_results(results, key_fields):
    """对结果进行去重"""
    if not results:
        return results
    seen = set()
    unique_results = []
    for r in results:
        key = tuple(str(r.get(k, '')) for k in key_fields)
        if key not in seen:
            seen.add(key)
            unique_results.append(r)
    return unique_results


def check1_cf_giveback(input_dir):
    """检查 cf.giveback.auto.enable 和 cf.giveback.auto.after.panic.takeover"""
    print("\n  [Check 1] cf.giveback建议关闭...")
    extractor = ArchiveExtractor()
    results = []
    for archive_path in scan_7z_files(input_dir):
        try:
            result = extractor.extract_files(archive_path, ['OPTIONS.txt'])
            node_name, cluster_name = extract_node_and_cluster(archive_path, extractor)
            opts_path = result.get('OPTIONS.txt', '')
            if opts_path and os.path.exists(opts_path):
                with open(opts_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                giveback_auto = None
                giveback_panic = None
                for line in content.split('\n'):
                    line_clean = line.strip()
                    if 'cf.giveback.auto.enable' in line.lower() and not line_clean.startswith('#'):
                        m = re.search(r'cf\.giveback\.auto\.enable\s+(\w+)', line, re.IGNORECASE)
                        if m:
                            giveback_auto = m.group(1).lower()
                    if 'cf.giveback.auto.after.panic.takeover' in line.lower() and not line_clean.startswith('#'):
                        m = re.search(r'cf\.giveback\.auto\.after\.panic\.takeover\s+(\w+)', line, re.IGNORECASE)
                        if m:
                            giveback_panic = m.group(1).lower()
                if giveback_auto == 'on' or giveback_panic == 'on':
                    results.append({
                        'cluster_name': cluster_name,
                        'node_name': node_name,
                        'giveback_auto': giveback_auto or '',
                        'giveback_panic': giveback_panic or ''
                    })
                os.remove(opts_path)
        except Exception as e:
            record_error(archive_path, e)
    extractor.cleanup()
    return deduplicate_results(results, ['cluster_name'])


def check2_volume_autosize(input_dir):
    """检查 volume autosize 是否关闭"""
    print("  [Check 2] volume autosize建议关闭...")
    extractor = ArchiveExtractor()
    results = []
    for archive_path in scan_7z_files(input_dir):
        try:
            result = extractor.extract_files(archive_path, ['volume.xml'])
            node_name, cluster_name = extract_node_and_cluster(archive_path, extractor)
            vol_path = result.get('volume.xml', '')
            if vol_path and os.path.exists(vol_path):
                with open(vol_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                rows = re.findall(r'<ROW[^>]*>(.*?)</ROW>', content, re.DOTALL)
                if not rows:
                    rows = re.findall(r'<asup:ROW[^>]*>(.*?)</asup:ROW>', content, re.DOTALL)
                for row in rows:
                    vol_name = ''
                    autosize = ''
                    m = re.search(r'<vol>(.*?)</vol>', row, re.DOTALL)
                    if m:
                        vol_name = m.group(1).strip()
                    m = re.search(r'<autosz>(.*?)</autosz>', row, re.DOTALL)
                    if m:
                        autosize = m.group(1).strip()
                    if should_exclude_volume(vol_name):
                        continue
                    if autosize.lower() == 'true':
                        results.append({
                            'cluster_name': cluster_name,
                            'node_name': node_name,
                            'volume_name': vol_name,
                            'value': 'autosize=on'
                        })
                os.remove(vol_path)
        except Exception as e:
            record_error(archive_path, e)
    extractor.cleanup()
    return deduplicate_results(results, ['cluster_name', 'node_name', 'volume_name'])



def check12_network_routes(input_dir):
    """检查 network-routes.xml：每个SVM应该只有一条默认路由(0.0.0.0/0)"""
    print("  [Check 12] 网络路由配置检查（默认路由/相同Metric）...")
    extractor = ArchiveExtractor()
    results = []
    for archive_path in scan_7z_files(input_dir):
        try:
            result = extractor.extract_files(archive_path, ['network-routes.xml'])
            node_name, cluster_name = extract_node_and_cluster(archive_path, extractor)
            routes_path = result.get('network-routes.xml', '')
            if routes_path and os.path.exists(routes_path):
                with open(routes_path, 'r', encoding='utf-8', errors='ignore') as f:
                    con = f.read()
                rows = re.findall(r'<asup:ROW[^>]*>(.*?)</asup:ROW>', con, re.DOTALL)
                if not rows:
                    rows = re.findall(r'<ROW[^>]*>(.*?)</ROW>', con, re.DOTALL)
                svm_routes = defaultdict(list)
                for row in rows:
                    svm = ''
                    dest = ''
                    gateway = ''
                    metric = ''
                    for tag, val in re.findall(r'<([^>]+)>(.*?)<\/\1>', row):
                        tag = tag.strip()
                        if tag in ('route_vserver',):
                            svm = val.strip()
                        elif tag in ('route_destination', 'destination'):
                            dest = val.strip()
                        elif tag in ('route_gateway', 'gateway'):
                            gateway = val.strip()
                        elif tag in ('route_metric', 'metric'):
                            metric = val.strip()
                    if svm:
                        svm_routes[svm].append({'destination': dest, 'gateway': gateway, 'metric': metric})
                for svm, routes in sorted(svm_routes.items()):
                    problems = []
                    default_routes = [r for r in routes if r['destination'] == '0.0.0.0/0']
                    if len(default_routes) > 1:
                        gw_list = ', '.join([f"{r['gateway']}(metric={r['metric']})" for r in default_routes])
                        problems.append(f"多条默认路由: {gw_list}")
                    metric_groups = defaultdict(list)
                    for r in routes:
                        if r['destination'] != '0.0.0.0/0':
                            metric_groups[r['metric']].append(r)
                    for m_metric, m_routes in metric_groups.items():
                        if len(m_routes) > 1:
                            route_list = ', '.join([f"{r['destination']}->{r['gateway']}" for r in m_routes])
                            problems.append(f"相同Metric({m_metric})的多条路由: {route_list}")
                    if problems:
                        results.append({
                            'cluster_name': cluster_name,
                            'node_name': node_name,
                            'svm_name': svm,
                            'total_routes': len(routes),
                            'default_routes': len(default_routes),
                            'problem': '; '.join(problems),
                            'value': '; '.join(problems)
                        })
                os.remove(routes_path)
        except Exception as error:
            record_error(archive_path, error)
    extractor.cleanup()
    return deduplicate_results(results, ['cluster_name', 'node_name', 'svm_name'])


def check3_snapshot_autodelete(input_dir):
    """检查 snapshot autodelete 是否关闭"""
    print("  [Check 3] snapshot autodelete建议关闭...")
    extractor = ArchiveExtractor()
    results = []
    for archive_path in scan_7z_files(input_dir):
        try:
            result = extractor.extract_files(archive_path, ['VOL-STATUS-V.txt'])
            node_name, cluster_name = extract_node_and_cluster(archive_path, extractor)
            vol_status_path = result.get('VOL-STATUS-V.txt', '')
            if vol_status_path and os.path.exists(vol_status_path):
                with open(vol_status_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                current_vol = None
                for line in content.split('\n'):
                    m = re.match(r'^Volume name:\s*(\S+)', line)
                    if m:
                        current_vol = m.group(1)
                    if current_vol and 'Snapshot autodelete settings:' in line:
                        m = re.search(r'state=(on|off)', line, re.IGNORECASE)
                        if m and m.group(1).lower() == 'on' and not should_exclude_volume(current_vol):
                            results.append({
                                'cluster_name': cluster_name,
                                'node_name': node_name,
                                'volume_name': current_vol,
                                'value': 'snapshot autodelete=on'
                            })
                        current_vol = None
                os.remove(vol_status_path)
        except Exception as e:
            record_error(archive_path, e)
    extractor.cleanup()
    return deduplicate_results(results, ['cluster_name', 'node_name', 'volume_name'])



def check12_network_routes(input_dir):
    """检查 network-routes.xml：每个SVM应该只有一条默认路由(0.0.0.0/0)"""
    print("  [Check 12] 网络路由配置检查（默认路由/相同Metric）...")
    extractor = ArchiveExtractor()
    results = []
    for archive_path in scan_7z_files(input_dir):
        try:
            result = extractor.extract_files(archive_path, ['network-routes.xml'])
            node_name, cluster_name = extract_node_and_cluster(archive_path, extractor)
            routes_path = result.get('network-routes.xml', '')
            if routes_path and os.path.exists(routes_path):
                with open(routes_path, 'r', encoding='utf-8', errors='ignore') as f:
                    con = f.read()
                rows = re.findall(r'<asup:ROW[^>]*>(.*?)</asup:ROW>', con, re.DOTALL)
                if not rows:
                    rows = re.findall(r'<ROW[^>]*>(.*?)</ROW>', con, re.DOTALL)
                svm_routes = defaultdict(list)
                for row in rows:
                    svm = ''
                    dest = ''
                    gateway = ''
                    metric = ''
                    for tag, val in re.findall(r'<([^>]+)>(.*?)<\/\1>', row):
                        tag = tag.strip()
                        if tag in ('route_vserver',):
                            svm = val.strip()
                        elif tag in ('route_destination', 'destination'):
                            dest = val.strip()
                        elif tag in ('route_gateway', 'gateway'):
                            gateway = val.strip()
                        elif tag in ('route_metric', 'metric'):
                            metric = val.strip()
                    if svm:
                        svm_routes[svm].append({'destination': dest, 'gateway': gateway, 'metric': metric})
                for svm, routes in sorted(svm_routes.items()):
                    problems = []
                    default_routes = [r for r in routes if r['destination'] == '0.0.0.0/0']
                    if len(default_routes) > 1:
                        gw_list = ', '.join([f"{r['gateway']}(metric={r['metric']})" for r in default_routes])
                        problems.append(f"多条默认路由: {gw_list}")
                    metric_groups = defaultdict(list)
                    for r in routes:
                        if r['destination'] != '0.0.0.0/0':
                            metric_groups[r['metric']].append(r)
                    for m_metric, m_routes in metric_groups.items():
                        if len(m_routes) > 1:
                            route_list = ', '.join([f"{r['destination']}->{r['gateway']}" for r in m_routes])
                            problems.append(f"相同Metric({m_metric})的多条路由: {route_list}")
                    if problems:
                        results.append({
                            'cluster_name': cluster_name,
                            'node_name': node_name,
                            'svm_name': svm,
                            'total_routes': len(routes),
                            'default_routes': len(default_routes),
                            'problem': '; '.join(problems),
                            'value': '; '.join(problems)
                        })
                os.remove(routes_path)
        except Exception as error:
            record_error(archive_path, error)
    extractor.cleanup()
    return deduplicate_results(results, ['cluster_name', 'node_name', 'svm_name'])


def check4_aff_logical_space(input_dir, customer_name):
    """按本地客户适用范围检查 AFF volume logical space。"""
    print("  [Check 4] AFF volume logical space建议打开...")
    if not check_enabled('check4', customer_name):
        return []
    extractor = ArchiveExtractor()
    results = []
    for archive_path in scan_7z_files(input_dir):
        try:
            result = extractor.extract_files(archive_path, ['volume.xml', 'SYSCONFIG-A.txt'])
            node_name, cluster_name = extract_node_and_cluster(archive_path, extractor)
            
            is_aff = False
            sysconfig_path = result.get('SYSCONFIG-A.txt', '')
            if sysconfig_path and os.path.exists(sysconfig_path):
                with open(sysconfig_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                if 'All-Flash Optimized: true' in content or 'AFF' in content:
                    is_aff = True
            
            vol_path = result.get('volume.xml', '')
            if vol_path and os.path.exists(vol_path):
                with open(vol_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                rows = re.findall(r'<ROW[^>]*>(.*?)</ROW>', content, re.DOTALL)
                if not rows:
                    rows = re.findall(r'<asup:ROW[^>]*>(.*?)</asup:ROW>', content, re.DOTALL)
                for row in rows:
                    vol_name = ''
                    space_reporting = ''
                    space_enforcement = ''
                    size_bytes = ''
                    avail_bytes = ''
                    used_pct = ''
                    logical_pct = ''
                    m = re.search(r'<vol>(.*?)</vol>', row, re.DOTALL)
                    if m:
                        vol_name = m.group(1).strip()
                    m = re.search(r'<logicalSpaceReporting>(.*?)</logicalSpaceReporting>', row, re.DOTALL)
                    if m:
                        space_reporting = m.group(1).strip()
                    m = re.search(r'<logicalSpaceEnforcement>(.*?)</logicalSpaceEnforcement>', row, re.DOTALL)
                    if m:
                        space_enforcement = m.group(1).strip()
                    m = re.search(r'<size>(.*?)</size>', row, re.DOTALL)
                    if m:
                        size_bytes = m.group(1).strip()
                    m = re.search(r'<avail>(.*?)</avail>', row, re.DOTALL)
                    if m:
                        avail_bytes = m.group(1).strip()
                    m = re.search(r'<pcnt_used>(.*?)</pcnt_used>', row, re.DOTALL)
                    if m:
                        used_pct = m.group(1).strip()
                    m = re.search(r'<logicalUsedPercent>(.*?)</logicalUsedPercent>', row, re.DOTALL)
                    if m:
                        logical_pct = m.group(1).strip()
                    if should_exclude_volume(vol_name):
                        continue
                    if is_aff:
                        if space_reporting.lower() == 'false' or space_enforcement.lower() == 'false':
                            results.append({
                                'cluster_name': cluster_name,
                                'node_name': node_name,
                                'volume_name': vol_name,
                                'space_reporting': space_reporting,
                                'space_enforcement': space_enforcement,
                                'volume_size_tb': _to_tb_float(size_bytes),
                                'avail_tb': _to_tb_float(avail_bytes),
                                'used_percent': used_pct,
                                'logical_used_percent': logical_pct,
                                'value': f'reporting={space_reporting}, enforcement={space_enforcement}'
                            })
                os.remove(vol_path)
            if sysconfig_path and os.path.exists(sysconfig_path):
                os.remove(sysconfig_path)
        except Exception as e:
            record_error(archive_path, e)
    extractor.cleanup()
    return deduplicate_results(results, ['cluster_name', 'node_name', 'volume_name'])



def check12_network_routes(input_dir):
    """检查 network-routes.xml：每个SVM应该只有一条默认路由(0.0.0.0/0)"""
    print("  [Check 12] 网络路由配置检查（默认路由/相同Metric）...")
    extractor = ArchiveExtractor()
    results = []
    for archive_path in scan_7z_files(input_dir):
        try:
            result = extractor.extract_files(archive_path, ['network-routes.xml'])
            node_name, cluster_name = extract_node_and_cluster(archive_path, extractor)
            routes_path = result.get('network-routes.xml', '')
            if routes_path and os.path.exists(routes_path):
                with open(routes_path, 'r', encoding='utf-8', errors='ignore') as f:
                    con = f.read()
                rows = re.findall(r'<asup:ROW[^>]*>(.*?)</asup:ROW>', con, re.DOTALL)
                if not rows:
                    rows = re.findall(r'<ROW[^>]*>(.*?)</ROW>', con, re.DOTALL)
                svm_routes = defaultdict(list)
                for row in rows:
                    svm = ''
                    dest = ''
                    gateway = ''
                    metric = ''
                    for tag, val in re.findall(r'<([^>]+)>(.*?)<\/\1>', row):
                        tag = tag.strip()
                        if tag in ('route_vserver',):
                            svm = val.strip()
                        elif tag in ('route_destination', 'destination'):
                            dest = val.strip()
                        elif tag in ('route_gateway', 'gateway'):
                            gateway = val.strip()
                        elif tag in ('route_metric', 'metric'):
                            metric = val.strip()
                    if svm:
                        svm_routes[svm].append({'destination': dest, 'gateway': gateway, 'metric': metric})
                for svm, routes in sorted(svm_routes.items()):
                    problems = []
                    default_routes = [r for r in routes if r['destination'] == '0.0.0.0/0']
                    if len(default_routes) > 1:
                        gw_list = ', '.join([f"{r['gateway']}(metric={r['metric']})" for r in default_routes])
                        problems.append(f"多条默认路由: {gw_list}")
                    metric_groups = defaultdict(list)
                    for r in routes:
                        if r['destination'] != '0.0.0.0/0':
                            metric_groups[r['metric']].append(r)
                    for m_metric, m_routes in metric_groups.items():
                        if len(m_routes) > 1:
                            route_list = ', '.join([f"{r['destination']}->{r['gateway']}" for r in m_routes])
                            problems.append(f"相同Metric({m_metric})的多条路由: {route_list}")
                    if problems:
                        results.append({
                            'cluster_name': cluster_name,
                            'node_name': node_name,
                            'svm_name': svm,
                            'total_routes': len(routes),
                            'default_routes': len(default_routes),
                            'problem': '; '.join(problems),
                            'value': '; '.join(problems)
                        })
                os.remove(routes_path)
        except Exception as error:
            record_error(archive_path, error)
    extractor.cleanup()
    return deduplicate_results(results, ['cluster_name', 'node_name', 'svm_name'])


def check5_ntp_servers(input_dir):
    """检查 NTP server 数量"""
    print("  [Check 5] NTP server建议至少2个...")
    extractor = ArchiveExtractor()
    results = []
    for archive_path in scan_7z_files(input_dir):
        try:
            result = extractor.extract_files(archive_path, ['var-etc-ntp-conf.txt'])
            node_name, cluster_name = extract_node_and_cluster(archive_path, extractor)
            ntp_path = result.get('var-etc-ntp-conf.txt', '')
            if ntp_path and os.path.exists(ntp_path):
                with open(ntp_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                servers = set()
                for line in content.split('\n'):
                    m = re.search(r'server\s+(\d+\.\d+\.\d+\.\d+)', line)
                    if m:
                        servers.add(m.group(1))
                if len(servers) < 2:
                    results.append({
                        'cluster_name': cluster_name,
                        'node_name': node_name,
                        'ntp_count': len(servers),
                        'ntp_servers': ', '.join(servers) if servers else '无',
                        'value': f'NTP={len(servers)}个 ({", ".join(servers) if servers else "无"})'
                    })
                os.remove(ntp_path)
        except Exception as e:
            record_error(archive_path, e)
    extractor.cleanup()
    return deduplicate_results(results, ['cluster_name'])


def check6_dns_servers(input_dir):
    """检查 DNS server 数量（按SVM分别统计nameservers，每个SVM建议至少2个）"""
    print("  [Check 6] DNS server建议至少2个(按SVM统计)...")
    extractor = ArchiveExtractor()
    results = []
    noted_clusters = set()
    for archive_path in scan_7z_files(input_dir):
        try:
            result = extractor.extract_files(archive_path, ['dns.xml'])
            node_name, cluster_name = extract_node_and_cluster(archive_path, extractor)
            dns_path = result.get('dns.xml', '')
            if dns_path and os.path.exists(dns_path):
                with open(dns_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                rows = re.findall(r'<asup:ROW[^>]*>(.*?)</asup:ROW>', content, re.DOTALL)
                if not rows:
                    rows = re.findall(r'<ROW[^>]*>(.*?)</ROW>', content, re.DOTALL)
                for row in rows:
                    svm_name = ''
                    m = re.search(r'<vserver>(.*?)</vserver>', row, re.DOTALL)
                    if m:
                        svm_name = m.group(1).strip()
                    ns_block = re.search(r'<nameservers>(.*?)</nameservers>', row, re.DOTALL)
                    servers = []
                    if ns_block:
                        servers = [s.strip() for s in re.findall(r'<asup:li>(.*?)</asup:li>', ns_block.group(1), re.DOTALL) if s.strip()]
                    if len(servers) < 2:
                        results.append({
                            'cluster_name': cluster_name,
                            'node_name': node_name,
                            'svm_name': svm_name,
                            'dns_count': len(servers),
                            'dns_servers': ', '.join(servers) if servers else '无',
                            'value': f'DNS={len(servers)}个 ({", ".join(servers) if servers else "无"})'
                        })
                os.remove(dns_path)
            elif cluster_name and cluster_name not in noted_clusters:
                noted_clusters.add(cluster_name)
                results.append({
                    'note': True,
                    'cluster_name': cluster_name,
                    'message': f'集群 {cluster_name} 无 dns.xml（未配置DNS或ASUP采集缺失），DNS检查未执行'
                })
        except Exception as e:
            record_error(archive_path, e)
    extractor.cleanup()
    return deduplicate_results(results, ['cluster_name', 'svm_name'])


def check7_tiering_policy(input_dir):
    """检查 tiering policy"""
    print("  [Check 7] tiering policy建议配置...")
    extractor = ArchiveExtractor()
    results = []
    noted_clusters = set()
    for archive_path in scan_7z_files(input_dir):
        try:
            result = extractor.extract_files(archive_path, ['data-object-store-config.xml', 'volume.xml'])
            node_name, cluster_name = extract_node_and_cluster(archive_path, extractor)
            has_object_store = False
            obj_path = result.get('data-object-store-config.xml', '')
            if obj_path and os.path.exists(obj_path):
                has_object_store = True
                os.remove(obj_path)
            elif cluster_name and cluster_name not in noted_clusters:
                noted_clusters.add(cluster_name)
                results.append({
                    'note': True,
                    'cluster_name': cluster_name,
                    'message': f'集群 {cluster_name} 无 data-object-store-config.xml，无法确认对象存储配置，Tiering检查未执行'
                })
            if has_object_store:
                vol_path = result.get('volume.xml', '')
                if vol_path and os.path.exists(vol_path):
                    with open(vol_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    rows = re.findall(r'<ROW[^>]*>(.*?)</ROW>', content, re.DOTALL)
                    if not rows:
                        rows = re.findall(r'<asup:ROW[^>]*>(.*?)</asup:ROW>', content, re.DOTALL)
                    for row in rows:
                        vol_name = ''
                        tiering_policy = ''
                        m = re.search(r'<vol>(.*?)</vol>', row, re.DOTALL)
                        if m:
                            vol_name = m.group(1).strip()
                        m = re.search(r'<tiering_policy>(.*?)</tiering_policy>', row, re.DOTALL)
                        if m:
                            tiering_policy = m.group(1).strip()
                        if should_exclude_volume(vol_name):
                            continue
                        if tiering_policy and tiering_policy.lower() in ['none', '', '-']:
                            results.append({
                                'cluster_name': cluster_name,
                                'node_name': node_name,
                                'volume_name': vol_name,
                                'tiering_policy': tiering_policy,
                                'value': f'tiering policy={tiering_policy}'
                            })
                    os.remove(vol_path)
        except Exception as e:
            record_error(archive_path, e)
    extractor.cleanup()
    return deduplicate_results(results, ['cluster_name', 'node_name', 'volume_name'])



def check12_network_routes(input_dir):
    """检查 network-routes.xml：每个SVM应该只有一条默认路由(0.0.0.0/0)"""
    print("  [Check 12] 网络路由配置检查（默认路由/相同Metric）...")
    extractor = ArchiveExtractor()
    results = []
    for archive_path in scan_7z_files(input_dir):
        try:
            result = extractor.extract_files(archive_path, ['network-routes.xml'])
            node_name, cluster_name = extract_node_and_cluster(archive_path, extractor)
            routes_path = result.get('network-routes.xml', '')
            if routes_path and os.path.exists(routes_path):
                with open(routes_path, 'r', encoding='utf-8', errors='ignore') as f:
                    con = f.read()
                rows = re.findall(r'<asup:ROW[^>]*>(.*?)</asup:ROW>', con, re.DOTALL)
                if not rows:
                    rows = re.findall(r'<ROW[^>]*>(.*?)</ROW>', con, re.DOTALL)
                svm_routes = defaultdict(list)
                for row in rows:
                    svm = ''
                    dest = ''
                    gateway = ''
                    metric = ''
                    for tag, val in re.findall(r'<([^>]+)>(.*?)<\/\1>', row):
                        tag = tag.strip()
                        if tag in ('route_vserver',):
                            svm = val.strip()
                        elif tag in ('route_destination', 'destination'):
                            dest = val.strip()
                        elif tag in ('route_gateway', 'gateway'):
                            gateway = val.strip()
                        elif tag in ('route_metric', 'metric'):
                            metric = val.strip()
                    if svm:
                        svm_routes[svm].append({'destination': dest, 'gateway': gateway, 'metric': metric})
                for svm, routes in sorted(svm_routes.items()):
                    problems = []
                    default_routes = [r for r in routes if r['destination'] == '0.0.0.0/0']
                    if len(default_routes) > 1:
                        gw_list = ', '.join([f"{r['gateway']}(metric={r['metric']})" for r in default_routes])
                        problems.append(f"多条默认路由: {gw_list}")
                    metric_groups = defaultdict(list)
                    for r in routes:
                        if r['destination'] != '0.0.0.0/0':
                            metric_groups[r['metric']].append(r)
                    for m_metric, m_routes in metric_groups.items():
                        if len(m_routes) > 1:
                            route_list = ', '.join([f"{r['destination']}->{r['gateway']}" for r in m_routes])
                            problems.append(f"相同Metric({m_metric})的多条路由: {route_list}")
                    if problems:
                        results.append({
                            'cluster_name': cluster_name,
                            'node_name': node_name,
                            'svm_name': svm,
                            'total_routes': len(routes),
                            'default_routes': len(default_routes),
                            'problem': '; '.join(problems),
                            'value': '; '.join(problems)
                        })
                os.remove(routes_path)
        except Exception as error:
            record_error(archive_path, error)
    extractor.cleanup()
    return deduplicate_results(results, ['cluster_name', 'node_name', 'svm_name'])


def check8_nfs_v4(input_dir):
    """检查 NFSv4 和 NFSv4.1 是否关闭"""
    print("  [Check 8] NFSv4/NFSv4.1建议关闭...")
    extractor = ArchiveExtractor()
    results = []
    noted_clusters = set()
    for archive_path in scan_7z_files(input_dir):
        try:
            result = extractor.extract_files(archive_path, ['nfs_servers_byname.xml'])
            node_name, cluster_name = extract_node_and_cluster(archive_path, extractor)
            nfs_path = result.get('nfs_servers_byname.xml', '')
            if nfs_path and os.path.exists(nfs_path):
                with open(nfs_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                rows = re.findall(r'<ROW[^>]*>(.*?)</ROW>', content, re.DOTALL)
                if not rows:
                    rows = re.findall(r'<asup:ROW[^>]*>(.*?)</asup:ROW>', content, re.DOTALL)
                for row in rows:
                    svm_name = ''
                    nfs_v4 = ''
                    nfs_v41 = ''
                    m = re.search(r'<vserver>(.*?)</vserver>', row, re.DOTALL)
                    if m:
                        svm_name = m.group(1).strip()
                    m = re.search(r'<v4\.0>(.*?)</v4\.0>', row, re.DOTALL)
                    if m:
                        nfs_v4 = m.group(1).strip()
                    m = re.search(r'<v41>(.*?)</v41>', row, re.DOTALL)
                    if m:
                        nfs_v41 = m.group(1).strip()
                    if nfs_v4.lower() == 'enabled' or nfs_v41.lower() == 'enabled':
                        results.append({
                            'cluster_name': cluster_name,
                            'node_name': node_name,
                            'svm_name': svm_name,
                            'nfs_v4': nfs_v4,
                            'nfs_v41': nfs_v41,
                            'value': f'v4.0={nfs_v4}, v4.1={nfs_v41}'
                        })
                os.remove(nfs_path)
            elif cluster_name and cluster_name not in noted_clusters:
                noted_clusters.add(cluster_name)
                results.append({
                    'note': True,
                    'cluster_name': cluster_name,
                    'message': f'集群 {cluster_name} 无 nfs_servers_byname.xml（未启用NFS或ASUP采集缺失），NFS v4检查未执行'
                })
        except Exception as e:
            record_error(archive_path, e)
    extractor.cleanup()
    return deduplicate_results(results, ['cluster_name', 'svm_name'])


def check9_volume_thresholds(input_dir, dir_name):
    """按本地客户适用范围检查 volume space thresholds。"""
    print(check_message('log_check9'))
    if not check_enabled('check9', dir_name):
        return []
    extractor = ArchiveExtractor()
    results = []
    for archive_path in scan_7z_files(input_dir):
        try:
            result = extractor.extract_files(archive_path, ['volume.xml'])
            node_name, cluster_name = extract_node_and_cluster(archive_path, extractor)
            vol_path = result.get('volume.xml', '')
            if vol_path and os.path.exists(vol_path):
                with open(vol_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                rows = re.findall(r'<ROW[^>]*>(.*?)</ROW>', content, re.DOTALL)
                if not rows:
                    rows = re.findall(r'<asup:ROW[^>]*>(.*?)</asup:ROW>', content, re.DOTALL)
                for row in rows:
                    vol_name = ''
                    nearly_full = ''
                    full = ''
                    m = re.search(r'<vol>(.*?)</vol>', row, re.DOTALL)
                    if m:
                        vol_name = m.group(1).strip()
                    m = re.search(r'<nearly_full_threshold>(.*?)</nearly_full_threshold>', row, re.DOTALL)
                    if m:
                        nearly_full = m.group(1).strip().replace('%', '')
                    m = re.search(r'<full_threshold>(.*?)</full_threshold>', row, re.DOTALL)
                    if m:
                        full = m.group(1).strip().replace('%', '')
                    if should_exclude_volume(vol_name):
                        continue
                    if nearly_full and nearly_full != '80':
                        results.append({
                            'cluster_name': cluster_name,
                            'node_name': node_name,
                            'volume_name': vol_name,
                            'nearly_full': nearly_full,
                            'full': full,
                            'value': f'nearly_full={nearly_full}%, full={full}%'
                        })
                    elif full and full != '90':
                        results.append({
                            'cluster_name': cluster_name,
                            'node_name': node_name,
                            'volume_name': vol_name,
                            'nearly_full': nearly_full,
                            'full': full,
                            'value': f'nearly_full={nearly_full}%, full={full}%'
                        })
                os.remove(vol_path)
        except Exception as e:
            record_error(archive_path, e)
    extractor.cleanup()
    return deduplicate_results(results, ['cluster_name', 'node_name', 'volume_name'])



def check12_network_routes(input_dir):
    """检查 network-routes.xml：每个SVM应该只有一条默认路由(0.0.0.0/0)"""
    print("  [Check 12] 网络路由配置检查（默认路由/相同Metric）...")
    extractor = ArchiveExtractor()
    results = []
    for archive_path in scan_7z_files(input_dir):
        try:
            result = extractor.extract_files(archive_path, ['network-routes.xml'])
            node_name, cluster_name = extract_node_and_cluster(archive_path, extractor)
            routes_path = result.get('network-routes.xml', '')
            if routes_path and os.path.exists(routes_path):
                with open(routes_path, 'r', encoding='utf-8', errors='ignore') as f:
                    con = f.read()
                rows = re.findall(r'<asup:ROW[^>]*>(.*?)</asup:ROW>', con, re.DOTALL)
                if not rows:
                    rows = re.findall(r'<ROW[^>]*>(.*?)</ROW>', con, re.DOTALL)
                svm_routes = defaultdict(list)
                for row in rows:
                    svm = ''
                    dest = ''
                    gateway = ''
                    metric = ''
                    for tag, val in re.findall(r'<([^>]+)>(.*?)<\/\1>', row):
                        tag = tag.strip()
                        if tag in ('route_vserver',):
                            svm = val.strip()
                        elif tag in ('route_destination', 'destination'):
                            dest = val.strip()
                        elif tag in ('route_gateway', 'gateway'):
                            gateway = val.strip()
                        elif tag in ('route_metric', 'metric'):
                            metric = val.strip()
                    if svm:
                        svm_routes[svm].append({'destination': dest, 'gateway': gateway, 'metric': metric})
                for svm, routes in sorted(svm_routes.items()):
                    problems = []
                    default_routes = [r for r in routes if r['destination'] == '0.0.0.0/0']
                    if len(default_routes) > 1:
                        gw_list = ', '.join([f"{r['gateway']}(metric={r['metric']})" for r in default_routes])
                        problems.append(f"多条默认路由: {gw_list}")
                    metric_groups = defaultdict(list)
                    for r in routes:
                        if r['destination'] != '0.0.0.0/0':
                            metric_groups[r['metric']].append(r)
                    for m_metric, m_routes in metric_groups.items():
                        if len(m_routes) > 1:
                            route_list = ', '.join([f"{r['destination']}->{r['gateway']}" for r in m_routes])
                            problems.append(f"相同Metric({m_metric})的多条路由: {route_list}")
                    if problems:
                        results.append({
                            'cluster_name': cluster_name,
                            'node_name': node_name,
                            'svm_name': svm,
                            'total_routes': len(routes),
                            'default_routes': len(default_routes),
                            'problem': '; '.join(problems),
                            'value': '; '.join(problems)
                        })
                os.remove(routes_path)
        except Exception as error:
            record_error(archive_path, error)
    extractor.cleanup()
    return deduplicate_results(results, ['cluster_name', 'node_name', 'svm_name'])


def check10_flexgroup_balance(input_dir):
    """检查 FlexGroup 平衡性 - 参考 Capacity Check 的解析方式"""
    print("  [Check 10] FlexGroup不平衡检查...")
    PATTERN_SUBVOLUME = re.compile(r'.*__000\d$|.*__00\d{2}$|.*__\d{4}$')
    
    def extract_volume_name(filesystem_path):
        match = re.search(r'/vol/([^/]+)', filesystem_path)
        if match:
            return match.group(1)
        return os.path.basename(filesystem_path.rstrip('/'))
    
    def is_flexgroup_volume(volume_name):
        if 'bucket' in volume_name.lower():
            return False
        if volume_name.lower().startswith('fg_oss'):
            return False
        return bool(PATTERN_SUBVOLUME.match(volume_name))
    
    extractor = ArchiveExtractor()
    fg_subvolume_count = defaultdict(lambda: defaultdict(int))
    fg_capacity_data = defaultdict(lambda: defaultdict(list))
    fg_inode_data = defaultdict(lambda: defaultdict(list))
    
    for archive_path in scan_7z_files(input_dir):
        try:
            result = extractor.extract_files(archive_path, ['DF-I.txt', 'DF.txt', 'volume.xml'])
            node_name, cluster_name = extract_node_and_cluster(archive_path, extractor)
            
            vol_path = result.get('volume.xml', '')
            if vol_path and os.path.exists(vol_path):
                with open(vol_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                content = re.sub(r'xmlns(:[^=]*)?="[^"]*"', '', content)
                rows = re.findall(r'<ROW[^>]*>(.*?)</ROW>', content, re.DOTALL)
                if not rows:
                    rows = re.findall(r'<asup:ROW[^>]*>(.*?)</asup:ROW>', content, re.DOTALL)
                
                for row in rows:
                    def get_field(tag):
                        patterns = [rf'<{tag}>(.*?)</{tag}>', rf'<asup:{tag}>(.*?)</asup:{tag}>']
                        for pat in patterns:
                            m = re.search(pat, row, re.DOTALL)
                            if m:
                                return m.group(1).strip()
                        return ""
                    
                    vol_name = get_field('vol')
                    if not vol_name:
                        continue
                    
                    if is_flexgroup_volume(vol_name):
                        parent_name = re.sub(r'__\d{4}$', '', vol_name)
                        if parent_name:
                            fg_subvolume_count[cluster_name][parent_name] += 1
                os.remove(vol_path)
            
            df_i_path = result.get('DF-I.txt', '')
            if df_i_path and os.path.exists(df_i_path):
                with open(df_i_path, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                start_reading = False
                for line in lines:
                    line = line.strip()
                    if re.match(r'Filesystem\s+iused\s+ifree\s+%iused', line):
                        start_reading = True
                        continue
                    if start_reading and line and not line.startswith('/proc'):
                        cols = re.split(r'\s+', line)
                        if len(cols) >= 4:
                            try:
                                percent_iused = float(cols[3].replace('%', ''))
                                volume_name = extract_volume_name(cols[0])
                                if is_flexgroup_volume(volume_name):
                                    parent_name = re.sub(r'__\d{4}$', '', volume_name)
                                    fg_inode_data[cluster_name][parent_name].append(percent_iused)
                            except (ValueError, IndexError) as error:
                                if re.search(r'__\d{4}', cols[0]):
                                    record_error(archive_path, error)
                                continue
                os.remove(df_i_path)
            
            df_path = result.get('DF.txt', '')
            if df_path and os.path.exists(df_path):
                with open(df_path, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                start_reading = False
                for line in lines:
                    line = line.strip()
                    if re.match(r'Filesystem\s+kbytes\s+used\s+avail\s+capacity\s+Mounted on', line):
                        start_reading = True
                        continue
                    if start_reading and line and not line.startswith('/proc'):
                        cols = re.split(r'\s+', line)
                        if len(cols) >= 6 and '.snapshot' not in cols[0]:
                            try:
                                capacity_percent = float(cols[4].replace('%', ''))
                                volume_name = extract_volume_name(cols[0])
                                if is_flexgroup_volume(volume_name):
                                    parent_name = re.sub(r'__\d{4}$', '', volume_name)
                                    fg_capacity_data[cluster_name][parent_name].append(capacity_percent)
                            except (ValueError, IndexError) as error:
                                if re.search(r'__\d{4}', cols[0]):
                                    record_error(archive_path, error)
                                continue
                os.remove(df_path)
            
            sysconfig_path = result.get('SYSCONFIG-A.txt', '')
            if sysconfig_path and os.path.exists(sysconfig_path):
                os.remove(sysconfig_path)
        except Exception as e:
            record_error(archive_path, e)
    extractor.cleanup()
    
    results = []
    for cluster_name, parent_counts in fg_subvolume_count.items():
        for parent_name, sub_count in parent_counts.items():
            cap_values = fg_capacity_data.get(cluster_name, {}).get(parent_name, [])
            inode_values = fg_inode_data.get(cluster_name, {}).get(parent_name, [])
            
            problems = []
            
            if sub_count % 2 == 1:
                problems.append(f'子卷数量={sub_count}个(单数)')
            
            if len(cap_values) >= 2:
                cap_diff = max(cap_values) - min(cap_values)
                if cap_diff > 30:
                    problems.append(f'Capacity不平衡 差值{cap_diff:.1f}%')
            
            if len(inode_values) >= 2:
                inode_diff = max(inode_values) - min(inode_values)
                if inode_diff > 30:
                    problems.append(f'%iused不平衡 差值{inode_diff:.1f}%')
            
            if problems:
                results.append({
                    'cluster_name': cluster_name,
                    'node_name': '',
                    'volume_name': parent_name,
                    'subvolume_count': sub_count,
                    'problem': ', '.join(problems),
                    'value': ', '.join(problems)
                })
    
    return deduplicate_results(results, ['cluster_name', 'volume_name'])

TREND_REPORT_ROOT = str(package_path('trend_output_root'))


def check10_convert_suggestion(input_dir, customer_name):
    """保留原检查入口；按完整身份和已用容量的真实历史窗口进行评估。"""
    from convert_check import check
    return check(input_dir, customer_name, ArchiveExtractor, scan_7z_files, extract_node_and_cluster)



def check11_aff_dedup_compression(input_dir):
    """检查 AFF 系列 volume 的去重压缩设置"""
    print("  [Check 11] AFF去重压缩建议开启...")
    extractor = ArchiveExtractor()
    results = []
    noted_clusters = set()
    for archive_path in scan_7z_files(input_dir):
        try:
            result = extractor.extract_files(archive_path, ['sis-config.xml', 'SYSCONFIG-A.txt', 'volume.xml'])
            node_name, cluster_name = extract_node_and_cluster(archive_path, extractor)
            is_aff = False
            sysconfig_path = result.get('SYSCONFIG-A.txt', '')
            if sysconfig_path and os.path.exists(sysconfig_path):
                with open(sysconfig_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                if 'All-Flash Optimized: true' in content or 'AFF' in content:
                    is_aff = True
            if not is_aff:
                vol_path = result.get('volume.xml', '')
                if vol_path and os.path.exists(vol_path):
                    with open(vol_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    first_row = re.search(r'<(?:asup:)?ROW[^>]*>(.*?)</(?:asup:)?ROW>', content, re.DOTALL)
                    if first_row:
                        row_content = first_row.group(1)
                        m = re.search(r'<styleEx>(.*?)</styleEx>', row_content, re.DOTALL)
                        if m and 'aff' in m.group(1).lower():
                            is_aff = True
                        m = re.search(r'<style>(.*?)</style>', row_content, re.DOTALL)
                        if m and 'aff' in m.group(1).lower():
                            is_aff = True
                    os.remove(vol_path)
            if not is_aff:
                if sysconfig_path and os.path.exists(sysconfig_path):
                    os.remove(sysconfig_path)
                continue
            sis_path = result.get('sis-config.xml', '')
            if sis_path and os.path.exists(sis_path):
                with open(sis_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                rows = re.findall(r'<ROW[^>]*>(.*?)</ROW>', content, re.DOTALL)
                if not rows:
                    rows = re.findall(r'<asup:ROW[^>]*>(.*?)</asup:ROW>', content, re.DOTALL)
                for row in rows:
                    vol_name = ''
                    bg_cmpr = ''
                    in_comp = ''
                    in_dedp = ''
                    compxn = ''
                    m = re.search(r'<vol>(.*?)</vol>', row, re.DOTALL)
                    if m:
                        vol_name = m.group(1).strip()
                    m = re.search(r'<bg_cmpr>(.*?)</bg_cmpr>', row, re.DOTALL)
                    if m:
                        bg_cmpr = m.group(1).strip()
                    m = re.search(r'<in_comp>(.*?)</in_comp>', row, re.DOTALL)
                    if m:
                        in_comp = m.group(1).strip()
                    m = re.search(r'<in_dedp>(.*?)</in_dedp>', row, re.DOTALL)
                    if m:
                        in_dedp = m.group(1).strip()
                    m = re.search(r'<compxn>(.*?)</compxn>', row, re.DOTALL)
                    if m:
                        compxn = m.group(1).strip()
                    if should_exclude_volume(vol_name):
                        continue
                    false_fields = []
                    if bg_cmpr and bg_cmpr.lower() == 'false':
                        false_fields.append('bg_cmpr=false')
                    if in_comp and in_comp.lower() == 'false':
                        false_fields.append('in_comp=false')
                    if in_dedp and in_dedp.lower() == 'false':
                        false_fields.append('in_dedp=false')
                    if compxn and compxn.lower() == 'false':
                        false_fields.append('compxn=false')
                    if false_fields:
                        results.append({
                            'cluster_name': cluster_name,
                            'node_name': node_name,
                            'volume_name': vol_name,
                            'compression': bg_cmpr,
                            'inline_compression': in_comp,
                            'inline_dedup': in_dedp,
                            'data_compaction': compxn,
                            'value': ', '.join(false_fields)
                        })
                os.remove(sis_path)
            elif is_aff:
                if cluster_name and cluster_name not in noted_clusters:
                    noted_clusters.add(cluster_name)
                    results.append({
                        'note': True,
                        'cluster_name': cluster_name,
                        'message': f'集群 {cluster_name} 为AFF但缺少 sis-config.xml，去重压缩检查无法执行（ASUP采集可能不完整）'
                    })
            if sysconfig_path and os.path.exists(sysconfig_path):
                os.remove(sysconfig_path)
        except Exception as e:
            record_error(archive_path, e)
    extractor.cleanup()
    return deduplicate_results(results, ['cluster_name', 'node_name', 'volume_name'])



def check12_network_routes(input_dir):
    """检查 network-routes.xml：每个SVM应该只有一条默认路由(0.0.0.0/0)"""
    print("  [Check 12] 网络路由配置检查（默认路由/相同Metric）...")
    extractor = ArchiveExtractor()
    results = []
    for archive_path in scan_7z_files(input_dir):
        try:
            result = extractor.extract_files(archive_path, ['network-routes.xml'])
            node_name, cluster_name = extract_node_and_cluster(archive_path, extractor)
            routes_path = result.get('network-routes.xml', '')
            if routes_path and os.path.exists(routes_path):
                with open(routes_path, 'r', encoding='utf-8', errors='ignore') as f:
                    con = f.read()
                rows = re.findall(r'<asup:ROW[^>]*>(.*?)</asup:ROW>', con, re.DOTALL)
                if not rows:
                    rows = re.findall(r'<ROW[^>]*>(.*?)</ROW>', con, re.DOTALL)
                svm_routes = defaultdict(list)
                for row in rows:
                    svm = ''
                    dest = ''
                    gateway = ''
                    metric = ''
                    for tag, val in re.findall(r'<([^>]+)>(.*?)<\/\1>', row):
                        tag = tag.strip()
                        if tag in ('route_vserver',):
                            svm = val.strip()
                        elif tag in ('route_destination', 'destination'):
                            dest = val.strip()
                        elif tag in ('route_gateway', 'gateway'):
                            gateway = val.strip()
                        elif tag in ('route_metric', 'metric'):
                            metric = val.strip()
                    if svm:
                        svm_routes[svm].append({'destination': dest, 'gateway': gateway, 'metric': metric})
                for svm, routes in sorted(svm_routes.items()):
                    problems = []
                    default_routes = [r for r in routes if r['destination'] == '0.0.0.0/0']
                    if len(default_routes) > 1:
                        gw_list = ', '.join([f"{r['gateway']}(metric={r['metric']})" for r in default_routes])
                        problems.append(f"多条默认路由: {gw_list}")
                    metric_groups = defaultdict(list)
                    for r in routes:
                        if r['destination'] != '0.0.0.0/0':
                            metric_groups[r['metric']].append(r)
                    for m_metric, m_routes in metric_groups.items():
                        if len(m_routes) > 1:
                            route_list = ', '.join([f"{r['destination']}->{r['gateway']}" for r in m_routes])
                            problems.append(f"相同Metric({m_metric})的多条路由: {route_list}")
                    if problems:
                        results.append({
                            'cluster_name': cluster_name,
                            'node_name': node_name,
                            'svm_name': svm,
                            'total_routes': len(routes),
                            'default_routes': len(default_routes),
                            'problem': '; '.join(problems),
                            'value': '; '.join(problems)
                        })
                os.remove(routes_path)
        except Exception as error:
            record_error(archive_path, error)
    extractor.cleanup()
    return deduplicate_results(results, ['cluster_name', 'svm_name'])



def check13_old_snapshots(input_dir, evaluation_time=None):
    """检查老旧snapshot(120天以上)，提醒客户清理"""
    from datetime import datetime, timedelta
    print("  [Check 13] 老旧Snapshot检查(>120天)...")
    extractor = ArchiveExtractor()

    # 第一遍：收集所有volume UUID -> (vserver, volume名)映射
    vol_uuid_map = {}
    for archive_path in scan_7z_files(input_dir):
        try:
            result = extractor.extract_files(archive_path, ["volume.xml"])
            vol_xml_path = result.get("volume.xml", "")
            if vol_xml_path and os.path.exists(vol_xml_path):
                with open(vol_xml_path, "r", encoding="utf-8", errors="ignore") as f2:
                    vcontent = f2.read()
                vrows = re.findall(r"<(?:asup:)?ROW\b[^>]*>(.*?)</(?:asup:)?ROW>", vcontent, re.DOTALL)
                for vrow in vrows:
                    vs_m = re.search(r"<vs>(.*?)</vs>", vrow)
                    vol_m = re.search(r"<vol>(.*?)</vol>", vrow)
                    uuid_m = re.search(r"<vol_uuid>(.*?)</vol_uuid>", vrow)
                    iuuid_m = re.search(r"<i_uuid>(.*?)</i_uuid>", vrow)
                    if uuid_m and uuid_m.group(1).strip():
                        uuid_val = uuid_m.group(1).strip()
                        if uuid_val not in vol_uuid_map:
                            vol_uuid_map[uuid_val] = {
                                'vserver': vs_m.group(1) if vs_m else '',
                                'volume': vol_m.group(1) if vol_m else ''
                            }
                    if iuuid_m and iuuid_m.group(1).strip():
                        iuuid_val = iuuid_m.group(1).strip()
                        if iuuid_val not in vol_uuid_map:
                            vol_uuid_map[iuuid_val] = {
                                'vserver': vs_m.group(1) if vs_m else '',
                                'volume': vol_m.group(1) if vol_m else ''
                            }
                os.remove(vol_xml_path)
        except Exception as error:
            record_error(archive_path, error)

    print(f"    收集到 {len(vol_uuid_map)} 个卷UUID映射")

    # 第二遍：解析snapshot.xml，检查创建时间
    reference_time = datetime.fromisoformat(evaluation_time).replace(tzinfo=None) if evaluation_time else datetime.now()
    ref_date = reference_time - timedelta(days=120)
    results = []

    for archive_path in scan_7z_files(input_dir):
        try:
            node_name, cluster_name = extract_node_and_cluster(archive_path, extractor)
            result = extractor.extract_files(archive_path, ["snapshot.xml", "snapshot_analytics.xml", "snap-list-n.txt"])
            snap_xml_path = result.get("snapshot.xml", "")
            snap_an_path = result.get("snapshot_analytics.xml", "")
            snap_list_path = result.get("snap-list-n.txt", "")

            # 旧格式下无卷名字段: 用 snapshot_analytics.xml 的 快照UUID(<i>) -> 卷UUID(<v>) 辅助匹配
            snap_i_to_vol_uuid = {}
            if snap_an_path and os.path.exists(snap_an_path):
                with open(snap_an_path, "r", encoding="utf-8", errors="ignore") as f2:
                    acontent = f2.read()
                for arow in re.findall(r"<(?:asup:)?ROW\b[^>]*>(.*?)</(?:asup:)?ROW>", acontent, re.DOTALL):
                    ai_m = re.search(r"<i>(.*?)</i>", arow, re.DOTALL)
                    av_m = re.search(r"<v>(.*?)</v>", arow, re.DOTALL)
                    if ai_m and av_m and ai_m.group(1).strip() and av_m.group(1).strip():
                        snap_i_to_vol_uuid[ai_m.group(1).strip()] = av_m.group(1).strip()
                os.remove(snap_an_path)

            # snap-list-n.txt: Volume <name> 段 -> 快照名, 用于卷UUID缺失(如vol0)时回退
            snap_name_to_vol = {}
            if snap_list_path and os.path.exists(snap_list_path):
                with open(snap_list_path, "r", encoding="utf-8", errors="ignore") as f2:
                    sl_content = f2.read()
                current_vol = ''
                for sl_line in sl_content.splitlines():
                    vm = re.match(r'^Volume\s+(\S+)', sl_line)
                    if vm:
                        current_vol = vm.group(1)
                        continue
                    cols = sl_line.strip().split()
                    if current_vol and len(cols) >= 2 and re.match(r'^[A-Z][a-z]{2}\s+\d{1,2}', sl_line.strip()):
                        snap_name_to_vol.setdefault(cols[-1], current_vol)
                os.remove(snap_list_path)

            if snap_xml_path and os.path.exists(snap_xml_path):
                with open(snap_xml_path, "r", encoding="utf-8", errors="ignore") as f2:
                    scontent = f2.read()
                srows = re.findall(r"<(?:asup:)?ROW\b[^>]*>(.*?)</(?:asup:)?ROW>", scontent, re.DOTALL)

                for row in srows:
                    # 兼容两种 snapshot.xml 格式:
                    #   新格式: <vo>卷名 <vs>vserver <ct>时间 <s>快照名 <sz>大小 <i>快照UUID
                    #   旧格式: <i>快照UUID <t>时间 <s>快照名 <z>大小 (无卷名字段)
                    vo_m = re.search(r'<vo>(.*?)</vo>', row, re.DOTALL)
                    vs_m = re.search(r'<vs>(.*?)</vs>', row, re.DOTALL)
                    ct_m = re.search(r'<ct>(.*?)</ct>', row, re.DOTALL)
                    t_m = re.search(r'<t>(.*?)</t>', row, re.DOTALL)
                    s_m = re.search(r'<s>(.*?)</s>', row, re.DOTALL)
                    z_m = re.search(r'<sz>(.*?)</sz>', row, re.DOTALL)
                    if not z_m:
                        z_m = re.search(r'<z>(.*?)</z>', row, re.DOTALL)
                    i_m = re.search(r'<i>(.*?)</i>', row, re.DOTALL)

                    snap_name = s_m.group(1).strip() if s_m else ''
                    time_str = (ct_m.group(1).strip() if ct_m else '') or (t_m.group(1).strip() if t_m else '')
                    snap_size = z_m.group(1).strip() if z_m else '0'
                    vol_name = vo_m.group(1).strip() if vo_m else ''
                    vserver = vs_m.group(1).strip() if vs_m else ''
                    snap_uuid = i_m.group(1).strip() if i_m else ''

                    if not snap_name or not time_str:
                        continue

                    try:
                        snap_time = datetime.strptime(time_str, "%a %b %d %H:%M:%S %Y")
                    except ValueError:
                        continue

                    if snap_time < ref_date:
                        # 无卷名字段时尝试用UUID匹配volume.xml(旧格式)
                        if not vol_name and snap_uuid:
                            # 1) 快照UUID -> 卷UUID(snapshot_analytics.xml) -> 卷名(volume.xml)
                            vol_uuid = snap_i_to_vol_uuid.get(snap_uuid, '')
                            if vol_uuid:
                                vol_info = vol_uuid_map.get(vol_uuid, {})
                                vol_name = vol_info.get('volume', '')
                                vserver = vol_info.get('vserver', '') or vserver
                        if not vol_name and snap_name:
                            # 2) 卷UUID缺失时(如vol0根卷)用 snap-list-n.txt 按快照名回退
                            vol_name = snap_name_to_vol.get(snap_name, '')
                        size_bytes = int(snap_size) if snap_size.isdigit() else 0
                        age_days = (reference_time - snap_time).days

                        if not vol_name:
                            vol_name = 'N/A (UUID: ' + (snap_uuid[:8] if snap_uuid else '?') + ')'
                        else:
                            # FlexGroup子卷(如VOL__0001)直接跳过，不显示
                            import re as _re2
                            m = _re2.match(r'^(.+)__\d+$', vol_name)
                            if m:
                                continue  # 跳过子卷，不显示
                        results.append({
                            'cluster_name': cluster_name,
                            'node_name': node_name,
                            'vserver': vserver or 'N/A',
                            'volume_name': vol_name,
                            'snapshot_name': snap_name,
                            'snapshot_uuid': snap_uuid,
                            "size_gb": round(size_bytes / (1024**3), 2),
                            'creation_time': time_str,
                            'age_days': age_days,
                        })

                os.remove(snap_xml_path)
        except Exception as error:
            record_error(archive_path, error)

    results = unique_snapshots(results)
    results.sort(key=lambda x: (x.get('cluster_name', ''), x.get('volume_name', ''), x.get('creation_time', '')))
    extractor.cleanup()
    print(f"    发现 {len(results)} 个老旧snapshot")
    return results




def unique_snapshots(results):
    unique = []
    seen = set()
    for record in results:
        known = all(record.get(field) and not str(record[field]).startswith('N/A')
                    for field in ('cluster_name', 'vserver', 'volume_name'))
        key = tuple(record.get(field, '') for field in ('cluster_name', 'vserver', 'volume_name',
                    'snapshot_uuid', 'snapshot_name', 'creation_time', 'size_gb'))
        if not known or key not in seen:
            unique.append(record)
            if known:
                seen.add(key)
    record_snapshot_duplicates(len(results) - len(unique))
    return unique


def check14_snapshot_policy(input_dir, customer_name):
    """检查本地客户规则指定集群中PROJRUN/PROJ卷的snapshot policy配置是否合规。

    规则:
      a) 名称包含 PROJRUN 的volume: 不应配置snapshot policy
      b) 名称包含 PROJ(但不含PROJRUN) 的volume: 应配置snapshot policy
    客户范围和集群名单来自本地私有配置。
    """
    if not check_enabled('check14', customer_name):
        print(check_message('log_check14_skip'))
        return []
    print(check_message('log_check14_start'))
    target_clusters = configured_target_clusters()
    extractor = ArchiveExtractor()
    results = []
    seen = set()
    global_policy_map = {}

    for archive_path in scan_7z_files(input_dir):
        try:
            node_name, cluster_name = extract_node_and_cluster(archive_path, extractor)
            if cluster_name.lower() not in target_clusters:
                continue

            # ---- 解析 snapshot_policy.xml, 构建 策略名 -> 保留份数详情 ----
            pol_map = {}
            pres = extractor.extract_files(archive_path, ['snapshot_policy.xml'])
            pol_xml_path = pres.get('snapshot_policy.xml', '')
            if pol_xml_path and os.path.exists(pol_xml_path):
                with open(pol_xml_path, 'r', encoding='utf-8', errors='ignore') as f2:
                    pcontent = f2.read()
                prows = re.findall(r'<asup:ROW[^>]*>(.*?)</asup:ROW>', pcontent, re.DOTALL)
                if not prows:
                    prows = re.findall(r'<ROW[^>]*>(.*?)</ROW>', pcontent, re.DOTALL)
                for prow in prows:
                    m_n = re.search(r'<n>(.*?)</n>', prow, re.DOTALL)
                    if not m_n:
                        continue
                    pname = m_n.group(1).strip()
                    m_s = re.search(r'<s>(.*?)</s>', prow, re.DOTALL)
                    m_c = re.search(r'<c>(.*?)</c>', prow, re.DOTALL)
                    scheds = re.findall(r'<asup:li>(.*?)</asup:li>', m_s.group(1), re.DOTALL) if m_s else []
                    counts = re.findall(r'<asup:li>(.*?)</asup:li>', m_c.group(1), re.DOTALL) if m_c else []
                    scheds = [x.strip() for x in scheds]
                    counts = [x.strip() for x in counts]
                    detail = ', '.join(f'{s} {cnt}份' for s, cnt in zip(scheds, counts))
                    if pname and detail:
                        pol_map.setdefault(pname, detail)
                        global_policy_map.setdefault(pname, detail)
                os.remove(pol_xml_path)

            # ---- 解析 volume.xml, 匹配PROJ/PROJRUN卷 ----
            result = extractor.extract_files(archive_path, ['volume.xml'])
            vol_xml_path = result.get('volume.xml', '')
            if not vol_xml_path or not os.path.exists(vol_xml_path):
                continue
            with open(vol_xml_path, 'r', encoding='utf-8', errors='ignore') as f2:
                vcontent = f2.read()
            vrows = re.findall(r'<asup:ROW[^>]*>(.*?)</asup:ROW>', vcontent, re.DOTALL)
            if not vrows:
                vrows = re.findall(r'<ROW[^>]*>(.*?)</ROW>', vcontent, re.DOTALL)
            for vrow in vrows:
                m_vol = re.search(r'<vol>(.*?)</vol>', vrow, re.DOTALL)
                if not m_vol:
                    continue
                vol_name = m_vol.group(1).strip()
                vol_lower = vol_name.lower()
                if 'proj' not in vol_lower:
                    continue
                m_pol = re.search(r'<snap_policy>(.*?)</snap_policy>', vrow, re.DOTALL)
                m_vs = re.search(r'<vs>(.*?)</vs>', vrow, re.DOTALL)
                policy = m_pol.group(1).strip() if m_pol else ''
                vserver = m_vs.group(1).strip() if m_vs else ''
                key = (cluster_name, vserver, vol_name, policy)
                if key in seen:
                    continue
                seen.add(key)
                has_policy = bool(policy) and policy.lower() != 'none'
                if 'projrun' in vol_lower:
                    # a) PROJRUN类卷: 不应配置snapshot policy
                    if has_policy:
                        policy_detail = pol_map.get(policy) or global_policy_map.get(policy) or ''
                        results.append({
                            'cluster_name': cluster_name,
                            'node_name': node_name,
                            'vserver': vserver,
                            'volume_name': vol_name,
                            'snapshot_policy': policy,
                            'rule': 'PROJRUN卷不应配置Snapshot Policy',
                            'value': f'当前Policy={policy}, 应为none',
                            'policy_detail': policy_detail
                        })
                else:
                    # b) PROJ类卷: 应配置snapshot policy
                    if not has_policy:
                        results.append({
                            'cluster_name': cluster_name,
                            'node_name': node_name,
                            'vserver': vserver,
                            'volume_name': vol_name,
                            'snapshot_policy': policy,
                            'rule': 'PROJ卷应配置Snapshot Policy',
                            'value': f'当前Policy={policy or "none"}, 应配置Snapshot Policy',
                            'policy_detail': ''
                        })
            os.remove(vol_xml_path)
        except Exception as error:
            record_error(archive_path, error)
    results.sort(key=lambda x: (x.get('cluster_name', ''), x.get('volume_name', '')))
    extractor.cleanup()
    print(f"    发现 {len(results)} 个违规volume")
    return results


def scan_7z_files(input_dir):
    """扫描目录下的7z文件"""
    files = []
    for root, _, filenames in os.walk(input_dir):
        for f in filenames:
            if f.endswith('.7z') or f.endswith('.7Z'):
                files.append(os.path.join(root, f))
    return sorted(files)


def _table_rows(results):
    """过滤掉说明(note)行，返回表格数据行"""
    return [r for r in results if not r.get('note')]


def _note_messages(results):
    """提取说明(note)行文本（数据缺失/未检查原因）"""
    return [r.get('message', '') for r in results if r.get('note') and r.get('message')]


def coverage_status(all_results, key, combine_convert=False):
    checks = all_results.get('_audit', {})
    records = [checks.get(key, {})]
    if combine_convert:
        records.append(checks.get('check10_convert', {}))
    for status in ('failed', 'missing_data', 'violations', 'passed', 'not_applicable'):
        for record in records:
            if record.get('status') == status:
                return record['label']
    return '缺少数据'


def create_merged_report(all_results, output_path, customer_name):
    """生成单Sheet合并的Excel报告"""
    wb = Workbook()
    ws = wb.active
    ws.title = 'BestPracticeCheck'

    title_fill = PatternFill('solid', fgColor='1F4E78')
    section_fill = PatternFill('solid', fgColor='4472C4')
    header_fill = PatternFill('solid', fgColor='4472C4')
    alt_fill = PatternFill('solid', fgColor='D9E2F3')
    green_fill = PatternFill('solid', fgColor='C6EFCE')
    warn_fill = PatternFill('solid', fgColor='FFE699')

    thin_border = Border(
        left=Side('thin', color='D9DEE7'),
        right=Side('thin', color='D9DEE7'),
        top=Side('thin', color='D9DEE7'),
        bottom=Side('thin', color='D9DEE7')
    )

    def style_cell(cell, value, fill=None, font=None, align=None, border=thin_border):
        cell.value = value
        if fill:
            cell.fill = fill
        if font:
            cell.font = font
        if align:
            cell.alignment = align
        cell.border = border
        return cell

    white_bold = Font(name='微软雅黑', size=12, bold=True, color='FFFFFF')
    white_title = Font(name='微软雅黑', size=16, bold=True, color='FFFFFF')
    normal_font = Font(name='微软雅黑', size=10)
    bold_font = Font(name='微软雅黑', size=10, bold=True)

    center_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
    left_align = Alignment(horizontal='left', vertical='center', wrap_text=True)

    current_row = 1

    ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=8)
    cell = ws.cell(row=current_row, column=1, value=f'NetApp 最佳实践检查报告 - {customer_name}')
    cell.font = white_title
    cell.fill = title_fill
    cell.alignment = center_align
    cell.border = thin_border
    ws.row_dimensions[current_row].height = 35
    for col in range(2, 9):
        ws.cell(row=current_row, column=col).border = thin_border
        ws.cell(row=current_row, column=col).fill = title_fill
    current_row += 2

    # Check 14 策略详情说明行(去重, 每个违规policy一行)
    check14_notes = []
    _p14_seen = set()
    for _r in all_results.get('check14', []):
        _pol = _r.get('snapshot_policy', '')
        _det = _r.get('policy_detail', '')
        if _pol and _pol.lower() != 'none' and _det and (_pol, _det) not in _p14_seen:
            _p14_seen.add((_pol, _det))
            check14_notes.append(f"Snapshot Policy [{_pol}] 内容: {_det}")

    check_configs = [
        ('Check 1: cf.giveback.auto.enable / cf.giveback.auto.after.panic.takeover 建议关闭',
         ['Cluster Name', 'Node Name', 'cf.giveback.auto.enable', 'cf.giveback.auto.after.panic.takeover', '值 / Value'],
         [[r['cluster_name'], r['node_name'], r.get('giveback_auto', ''), r.get('giveback_panic', ''),
           f"auto.enable={r.get('giveback_auto', '')}, panic.takeover={r.get('giveback_panic', '')}"]
          for r in _table_rows(all_results.get('check1', []))],
         [18, 20, 22, 30, 38],
         _note_messages(all_results.get('check1', []))),

        ('Check 2: Volume autosize 建议关闭',
         ['Cluster Name', 'Node Name', 'Volume Name', '值 / Value'],
         [[r['cluster_name'], r['node_name'], r['volume_name'], r.get('value', 'autosize=on')]
          for r in _table_rows(all_results.get('check2', []))],
         [18, 20, 30, 25],
         _note_messages(all_results.get('check2', []))),

        ('Check 3: Snapshot autodelete 建议关闭',
         ['Cluster Name', 'Node Name', 'Volume Name', '值 / Value'],
         [[r['cluster_name'], r['node_name'], r['volume_name'], r.get('value', 'autodelete=on')]
          for r in _table_rows(all_results.get('check3', []))],
         [18, 20, 30, 25],
         _note_messages(all_results.get('check3', []))),

        (check_message('title_check4'),
         ['Cluster Name', 'Node Name', 'Volume Name', 'reporting', 'enforcement', 'Volume Size (TB)', 'Available Size (TB)', 'Used Percentage', 'Logical Used Percent', '值 / Value'],
         [[r['cluster_name'], r['node_name'], r['volume_name'], r.get('space_reporting', ''), r.get('space_enforcement', ''), r.get('volume_size_tb', ''), r.get('avail_tb', ''), r.get('used_percent', ''), r.get('logical_used_percent', ''), r.get('value', '')]
          for r in _table_rows(all_results.get('check4', []))],
         [18, 20, 30, 12, 12, 14, 16, 12, 14, 30],
         _note_messages(all_results.get('check4', []))),

        ('Check 5: NTP Server 建议至少2个',
         ['Cluster Name', 'Node Name', 'NTP数量', 'NTP Servers', '值 / Value'],
         [[r['cluster_name'], r['node_name'], r.get('ntp_count', ''), r.get('ntp_servers', ''), r.get('value', '')]
          for r in _table_rows(all_results.get('check5', []))],
         [18, 20, 10, 35, 40],
         _note_messages(all_results.get('check5', []))),

        ('Check 6: DNS Server 建议至少2个 (按SVM分别统计)',
         ['Cluster Name', 'Node Name', 'SVM Name', 'DNS数量', 'DNS Servers', '值 / Value'],
         [[r['cluster_name'], r['node_name'], r.get('svm_name', ''), r.get('dns_count', ''), r.get('dns_servers', ''), r.get('value', '')]
          for r in _table_rows(all_results.get('check6', []))],
         [18, 20, 25, 10, 35, 40],
         _note_messages(all_results.get('check6', []))),

        ('Check 7: Tiering Policy 建议配置 (需配置对象存储)',
         ['Cluster Name', 'Node Name', 'Volume Name', 'Tiering Policy', '值 / Value'],
         [[r['cluster_name'], r['node_name'], r['volume_name'], r.get('tiering_policy', ''), r.get('value', '')]
          for r in _table_rows(all_results.get('check7', []))],
         [18, 20, 30, 15, 25],
         _note_messages(all_results.get('check7', []))),

        ('Check 8: NFS v4.0 / v4.1 建议关闭',
         ['Cluster Name', 'Node Name', 'SVM Name', 'NFS v4.0', 'NFS v4.1', '值 / Value'],
         [[r['cluster_name'], r['node_name'], r.get('svm_name', ''), r.get('nfs_v4', ''), r.get('nfs_v41', ''), r.get('value', '')]
          for r in _table_rows(all_results.get('check8', []))],
         [18, 20, 25, 12, 12, 30],
         _note_messages(all_results.get('check8', []))),

        (check_message('title_check9'),
         ['Cluster Name', 'Node Name', 'Volume Name', 'nearly_full(%)', 'full(%)', '值 / Value'],
         [[r['cluster_name'], r['node_name'], r['volume_name'], r.get('nearly_full', ''), r.get('full', ''), r.get('value', '')]
          for r in _table_rows(all_results.get('check9', []))],
         [18, 20, 30, 15, 10, 30],
         _note_messages(all_results.get('check9', []))),

        {'title': 'Check 10: FlexGroup 最佳实践检查 (子卷单数/不平衡/convert建议)',
         'blocks': [
            ('10-A. FlexGroup 子卷单数 / 不平衡检查',
             ['Cluster Name', 'Node Name', 'Volume Name', '子卷数量', '问题描述', '值 / Value'],
             [[r['cluster_name'], r['node_name'], r['volume_name'], r.get('subvolume_count', ''), r.get('problem', ''), r.get('value', '')]
              for r in _table_rows(all_results.get('check10', []))],
             [18, 20, 30, 10, 30, 40],
             _note_messages(all_results.get('check10', []))),
            ('10-B. FlexGroup Convert 建议 (大容量增长型普通卷 → FlexGroup)',
             ['Cluster Name', 'Vserver', 'Volume Name', '当前大小(TB)', '使用率(%)', '增长趋势(%)', '建议', '评估区间', '有效采样数', '已用量起止(TB)'],
             [[r['cluster_name'], r.get('vserver', ''), r['volume_name'], r.get('size_tb', ''), r.get('used_percent', ''), r.get('growth_percent', ''), r.get('suggestion', ''), r.get('window', ''), r.get('sample_count', ''), r.get('used_range', '')]
              for r in _table_rows(all_results.get('check10_convert', []))],
             [18, 20, 30, 14, 12, 14, 46, 24, 12, 30],
             _note_messages(all_results.get('check10_convert', []))),
         ]},

        ('Check 11: AFF 去重压缩 建议开启',
         ['Cluster Name', 'Node Name', 'Volume Name', 'Compression', 'Inline Compression', 'Inline Dedupe', 'Data Compaction', '值 / Value'],
         [[r['cluster_name'], r['node_name'], r['volume_name'], r.get('compression', ''), r.get('inline_compression', ''), r.get('inline_dedup', ''), r.get('data_compaction', ''), r.get('value', '')]
          for r in _table_rows(all_results.get('check11', []))],
         [18, 20, 30, 15, 18, 15, 15, 50],
         _note_messages(all_results.get('check11', []))),

        ('Check 12: 网络路由配置 - 每个SVM应只有一条默认路由(0.0.0.0/0)，且避免相同Metric的多条路由',
         ['Cluster Name', 'Node Name', 'SVM Name', '路由总数', '默认路由数', '问题描述', '值 / Value'],
         [[r['cluster_name'], r['node_name'], r['svm_name'], r.get('total_routes', ''), r.get('default_routes', ''), r.get('problem', ''), r.get('value', '')]
          for r in _table_rows(all_results.get('check12', []))],
         [18, 20, 25, 10, 10, 50, 50],
         _note_messages(all_results.get('check12', []))),

        ('Check 13: 老旧Snapshot检查(>120天) - 提醒清理',
         ['Cluster Name', 'Node Name', 'Vserver', 'Volume Name', 'Snapshot Name', 'Size(GB)', 'Creation Time', 'Age(Days)'],
         [[r['cluster_name'], r['node_name'], r.get('vserver', ''), r['volume_name'], r['snapshot_name'], r.get('size_gb', ''), r.get('creation_time', ''), r.get('age_days', '')]
          for r in _table_rows(all_results.get('check13', []))],
         [18, 20, 22, 28, 25, 12, 25, 10],
         _note_messages(all_results.get('check13', []))),

        (check_message('title_check14'),
         ['Cluster Name', 'Node Name', 'Vserver', 'Volume Name', 'Snapshot Policy', '规则', '值 / Value'],
         [[r['cluster_name'], r['node_name'], r.get('vserver', ''), r['volume_name'], r.get('snapshot_policy', ''), r.get('rule', ''), r.get('value', '')]
          for r in _table_rows(all_results.get('check14', []))],
         [18, 20, 24, 32, 18, 30, 30],
         _note_messages(all_results.get('check14', [])) + check14_notes),
    ]


    def _cfg_blocks(cfg):
        """将cfg归一为渲染块列表: [(subtitle, headers, rows, widths, notes)]"""
        if isinstance(cfg, dict):
            return cfg['blocks']
        title, headers, data, widths = cfg[0], cfg[1], cfg[2], cfg[3]
        notes = cfg[4] if len(cfg) > 4 else []
        return [(None, headers, data, widths, notes)]

    total_issues = sum(len(b[2]) for cfg in check_configs for b in _cfg_blocks(cfg))

    summary_header = ['检查项', '描述', '问题数量', '状态']
    summary_data = []
    for idx, cfg in enumerate(check_configs, 1):
        title = cfg['title'] if isinstance(cfg, dict) else cfg[0]
        n_issues = sum(len(b[2]) for b in _cfg_blocks(cfg))
        desc = title.split(':')[1].strip() if ':' in title else title
        status = coverage_status(all_results, f'check{idx}', combine_convert=idx == 10)
        summary_data.append([f'Check {idx}', desc, n_issues, status])

    ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=8)
    cell = ws.cell(row=current_row, column=1, value='汇总 Summary')
    cell.font = white_bold
    cell.fill = section_fill
    cell.alignment = center_align
    cell.border = thin_border
    for col in range(2, 9):
        c = ws.cell(row=current_row, column=col)
        c.fill = section_fill
        c.border = thin_border
    ws.row_dimensions[current_row].height = 25
    current_row += 1

    summary_widths = [15, 45, 12, 12]
    for col, (header, width) in enumerate(zip(summary_header, summary_widths), 1):
        ws.column_dimensions[get_column_letter(col)].width = max(ws.column_dimensions[get_column_letter(col)].width or 0, width)
        cell = ws.cell(row=current_row, column=col, value=header)
        cell.font = white_bold
        cell.fill = header_fill
        cell.alignment = center_align
        cell.border = thin_border
    for col in range(5, 9):
        c = ws.cell(row=current_row, column=col)
        c.fill = header_fill
        c.border = thin_border
    ws.row_dimensions[current_row].height = 22
    current_row += 1

    for row_data in summary_data:
        for col, value in enumerate(row_data, 1):
            cell = ws.cell(row=current_row, column=col, value=value)
            cell.font = normal_font
            cell.alignment = center_align if col != 2 else left_align
            cell.border = thin_border
            if (current_row - 4) % 2 == 0:
                cell.fill = alt_fill
            if col == 4 and value == '通过':
                cell.fill = green_fill
            elif col == 4 and value in ('发现违规', '缺少数据', '解析失败'):
                cell.fill = warn_fill
        for col in range(5, 9):
            c = ws.cell(row=current_row, column=col)
            c.border = thin_border
            if (current_row - 4) % 2 == 0:
                c.fill = alt_fill
        ws.row_dimensions[current_row].height = 20
        current_row += 1
    current_row += 1

    sub_fill = PatternFill('solid', fgColor='9DC3E6')
    for idx, cfg in enumerate(check_configs, 1):
        blocks = _cfg_blocks(cfg)
        title = cfg['title'] if isinstance(cfg, dict) else cfg[0]
        ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=8)
        cell = ws.cell(row=current_row, column=1, value=title)
        cell.font = white_bold
        cell.fill = section_fill
        cell.alignment = center_align
        cell.border = thin_border
        for col in range(2, 9):
            c = ws.cell(row=current_row, column=col)
            c.fill = section_fill
            c.border = thin_border
        ws.row_dimensions[current_row].height = 28
        current_row += 1

        for bi, (subtitle, headers, data, widths, notes) in enumerate(blocks):
            if subtitle:
                ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=len(headers))
                cell = ws.cell(row=current_row, column=1, value=subtitle)
                cell.font = Font(name='微软雅黑', size=11, bold=True, color='FFFFFF')
                cell.fill = sub_fill
                cell.alignment = center_align
                cell.border = thin_border
                for col in range(2, len(headers) + 1):
                    c = ws.cell(row=current_row, column=col)
                    c.fill = sub_fill
                    c.border = thin_border
                ws.row_dimensions[current_row].height = 22
                current_row += 1

            for col, (header, width) in enumerate(zip(headers, widths), 1):
                col_letter = get_column_letter(col)
                ws.column_dimensions[col_letter].width = max(ws.column_dimensions[col_letter].width or 0, width)
                cell = ws.cell(row=current_row, column=col, value=header)
                cell.font = white_bold
                cell.fill = header_fill
                cell.alignment = center_align
                cell.border = thin_border
            ws.row_dimensions[current_row].height = 22
            current_row += 1

            if not data:
                ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=len(headers))
                block_key = 'check10_convert' if idx == 10 and bi == 1 else f'check{idx}'
                empty_status = coverage_status(all_results, block_key)
                cell = ws.cell(row=current_row, column=1, value=empty_status)
                cell.font = bold_font
                cell.fill = green_fill if empty_status == '通过' else alt_fill if empty_status == '不适用' else warn_fill
                cell.alignment = center_align
                cell.border = thin_border
                for col in range(2, len(headers) + 1):
                    c = ws.cell(row=current_row, column=col)
                    c.fill = copy(cell.fill)
                    c.border = thin_border
                ws.row_dimensions[current_row].height = 20
                current_row += 1
            else:
                for row_idx, row_data in enumerate(data):
                    for col, value in enumerate(row_data, 1):
                        cell = ws.cell(row=current_row, column=col, value=value)
                        cell.font = normal_font
                        cell.alignment = center_align if col != 3 and col != len(headers) else (left_align if col == 3 else left_align)
                        cell.border = thin_border
                        if row_idx % 2 == 0:
                            cell.fill = alt_fill
                    ws.row_dimensions[current_row].height = 54 if idx == 10 and bi == 1 else 20
                    current_row += 1
            for note in notes:
                ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=len(headers))
                cell = ws.cell(row=current_row, column=1, value=note)
                cell.font = Font(name='微软雅黑', size=9)
                cell.fill = PatternFill('solid', fgColor='EDEDED')
                cell.alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
                cell.border = thin_border
                for col in range(2, len(headers) + 1):
                    c = ws.cell(row=current_row, column=col)
                    c.fill = PatternFill('solid', fgColor='EDEDED')
                    c.border = thin_border
                ws.row_dimensions[current_row].height = 20
                current_row += 1
        current_row += 1


    ws.freeze_panes = 'A1'

    try:
        wb.save(output_path)
        print(f"  报告已保存: {output_path}")
    except Exception as e:
        print(f"  保存报告失败: {e}")



def _to_tb_float(s):
    """将字节数转换为TB(保留2位)"""
    try:
        if s and s.strip():
            num_str = s.replace(',', '')
            if num_str.replace('.', '', 1).isdigit():
                return round(float(num_str) / (1024 ** 4), 2)
    except Exception:
        pass
    return 0


def detect_customer_name(customer_dir):
    """从客户目录(或完整路径)识别客户名, 避免传入月份子目录时客户名被月份覆盖"""
    return configured_customer_name(customer_dir, 'best_practice')


def process_customer(customer_dir, output_dir):
    """处理单个客户目录"""
    customer_name = detect_customer_name(customer_dir)
    print(f"\n{'='*60}")
    print(f"处理客户: {customer_name}")
    print(f"目录: {customer_dir}")
    print(f"{'='*60}")

    audit = BestPracticeAudit(customer_dir, customer_name)
    all_results = {}
    all_results['check1'] = audit.run('check1', check1_cf_giveback, customer_dir)
    print(f"    {len(_table_rows(all_results['check1']))} 个问题 (说明 {len(_note_messages(all_results['check1']))} 条)")

    all_results['check2'] = audit.run('check2', check2_volume_autosize, customer_dir)
    print(f"    {len(_table_rows(all_results['check2']))} 个问题 (说明 {len(_note_messages(all_results['check2']))} 条)")

    all_results['check3'] = audit.run('check3', check3_snapshot_autodelete, customer_dir)
    print(f"    {len(_table_rows(all_results['check3']))} 个问题 (说明 {len(_note_messages(all_results['check3']))} 条)")

    all_results['check4'] = audit.run('check4', check4_aff_logical_space, customer_dir, customer_name)
    print(f"    {len(_table_rows(all_results['check4']))} 个问题 (说明 {len(_note_messages(all_results['check4']))} 条)")

    all_results['check5'] = audit.run('check5', check5_ntp_servers, customer_dir)
    print(f"    {len(_table_rows(all_results['check5']))} 个问题 (说明 {len(_note_messages(all_results['check5']))} 条)")

    all_results['check6'] = audit.run('check6', check6_dns_servers, customer_dir)
    print(f"    {len(_table_rows(all_results['check6']))} 个问题 (说明 {len(_note_messages(all_results['check6']))} 条)")

    all_results['check7'] = audit.run('check7', check7_tiering_policy, customer_dir)
    print(f"    {len(_table_rows(all_results['check7']))} 个问题 (说明 {len(_note_messages(all_results['check7']))} 条)")

    all_results['check8'] = audit.run('check8', check8_nfs_v4, customer_dir)
    print(f"    {len(_table_rows(all_results['check8']))} 个问题 (说明 {len(_note_messages(all_results['check8']))} 条)")

    all_results['check9'] = audit.run('check9', check9_volume_thresholds, customer_dir, customer_name)
    print(f"    {len(_table_rows(all_results['check9']))} 个问题 (说明 {len(_note_messages(all_results['check9']))} 条)")

    all_results['check10'] = audit.run('check10', check10_flexgroup_balance, customer_dir)
    print(f"    {len(_table_rows(all_results['check10']))} 个问题 (说明 {len(_note_messages(all_results['check10']))} 条)")

    all_results['check10_convert'] = audit.run('check10_convert', check10_convert_suggestion, customer_dir, customer_name)
    print(f"    {len(_table_rows(all_results['check10_convert']))} 个建议 (说明 {len(_note_messages(all_results['check10_convert']))} 条)")

    all_results['check11'] = audit.run('check11', check11_aff_dedup_compression, customer_dir)
    print(f"    {len(_table_rows(all_results['check11']))} 个问题 (说明 {len(_note_messages(all_results['check11']))} 条)")

    all_results['check12'] = audit.run('check12', check12_network_routes, customer_dir)
    print(f"    {len(_table_rows(all_results['check12']))} 个问题 (说明 {len(_note_messages(all_results['check12']))} 条)")

    all_results['check13'] = audit.run('check13', check13_old_snapshots, customer_dir, os.environ.get('SAM_EVALUATION_TIME'))
    print(f"    {len(_table_rows(all_results['check13']))}个问题")

    all_results['check14'] = audit.run('check14', check14_snapshot_policy, customer_dir, customer_name)
    print(f"    {len(_table_rows(all_results['check14']))} 个问题 (说明 {len(_note_messages(all_results['check14']))} 条)")

    output_path = os.path.join(output_dir, f'{os.path.basename(customer_dir.rstrip(os.sep))}_最佳实践检查.xlsx')
    all_results['_audit'] = audit.checks
    audit.save(output_path)
    create_merged_report(all_results, output_path, customer_name)
    return output_path


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("\u9519\u8bef: \u8bf7\u6307\u5b9a\u8f93\u5165\u76ee\u5f55")
        sys.exit(1)

    input_dir = sys.argv[1]
    if not os.path.isdir(input_dir):
        print(f"错误: 目录不存在: {input_dir}")
        sys.exit(1)

    if len(sys.argv) >= 3:
        output_dir = sys.argv[2]
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        output_dir = os.path.join(script_dir, 'Best practice Check report')

    os.makedirs(output_dir, exist_ok=True)

    print("=" * 60)
    print("NetApp ASUP Best Practice Check")
    print("=" * 60)
    print(f"输入目录: {input_dir}")
    print(f"输出目录: {output_dir}")

    sub_dirs = []
    for item in sorted(os.listdir(input_dir)):
        item_path = os.path.join(input_dir, item)
        if os.path.isdir(item_path):
            if any(f.lower().endswith('.7z') for _, _, files in os.walk(item_path) for f in files):
                sub_dirs.append(item_path)

    has_direct_7z = any(f.lower().endswith('.7z') for f in os.listdir(input_dir) if os.path.isfile(os.path.join(input_dir, f)))

    if has_direct_7z and not sub_dirs:
        sub_dirs = [input_dir]

    if not sub_dirs:
        print("错误: 未找到包含7z文件的目录")
        sys.exit(1)

    print(f"发现 {len(sub_dirs)} 个客户目录:")
    for d in sub_dirs:
        print(f"  - {os.path.basename(d.rstrip(os.sep))}")

    reports = []
    for customer_dir in sub_dirs:
        try:
            report = process_customer(customer_dir, output_dir)
            reports.append(report)
            gc.collect()
        except Exception as e:
            print(f"  处理 {customer_dir} 出错: {e}")

    print(f"\n{'='*60}")
    print(f"完成! 共生成 {len(reports)} 个报告")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
