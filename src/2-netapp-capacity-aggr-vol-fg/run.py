#!/usr/bin/env python3
"""
NetApp ASUP Capacity Check - 主入口

用法:
    python run.py <7z文件所在目录> [输出Excel路径]

示例:
    python run.py ./asup_logs
    python run.py ./asup_logs ./report.xlsx
"""
import sys
import os
import gc
import shutil
import json
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from parsers.archive import ArchiveExtractor
from parsers.sysconfig import extract_node_name, extract_cluster_name, extract_model_name
from parsers.volume import parse_volume_xml, parse_volume_records
from parsers.identity import VolumeIdentityError, VolumeIndex
from parsers.volstatus_f import parse_volstatus_blocks
from parsers.tiering import TieringIndex, local_volume_names
from parsers.bucket import parse_bucket_xml
from parsers.export_policy import parse_export_rules
from parsers.dedupe import parse_dedupe_ratio
from parsers.aggr import parse_df_a_for_capacity, parse_df_a_for_top_aggregates, parse_aggr_status_logical_used
from parsers.df import parse_df_i, parse_df
from exporters.excel import HealthCheckExporter


def scan_7z_files(input_dir: str) -> list:
    """扫描目录下所有 7z 文件"""
    files = []
    for root, _, filenames in os.walk(input_dir):
        for f in filenames:
            if f.endswith('.7z') or f.endswith('.7Z'):
                files.append(os.path.join(root, f))
    return sorted(files)


def _rule_index_key(value):
    """Export Policy 规则序号按数字排序, 非数字排后面"""
    try:
        return (0, int(str(value).strip()))
    except (TypeError, ValueError):
        return (1, str(value))


def _display(value):
    return 'N/A' if value is None or value == '' else value


def _percent(value, decimals=False):
    if value is None:
        return 'N/A'
    if value == 0:
        return '0%'
    return f'{value:.2f}%' if decimals and value else f'{value}%'


def _files(value):
    return 'N/A' if value is None else f'{value:.2f}'


def _constituent_key(archive, node, name, local_names, audit):
    candidates = local_names.get(name, {})
    if len(candidates) != 1:
        audit.findings.append({'kind': 'unresolved_constituent_df', 'archive': archive,
                               'block': name, 'candidate_count': len(candidates)})
        return None
    identity = next(iter(candidates))
    return (node,) + identity


def process_directory(input_dir: str, output_path: str) -> dict:
    """处理目录下所有 ASUP 日志并生成报告"""
    extractor = ArchiveExtractor()

    # 扫描 7z 文件
    seven_zip_files = scan_7z_files(input_dir)
    if not seven_zip_files:
        raise FileNotFoundError(f'未找到 7z 文件: {input_dir}')
    print(f'找到 {len(seven_zip_files)} 个 7z 文件')

    # ============ 阶段1: 解析 volume.xml + bucket.xml ============
    print('\n阶段1: 解析 volume.xml 和 object-store-bucket.xml ...')
    volume_list_dict = VolumeIndex()
    oss_volume_dict = VolumeIndex()
    export_rule_dict = {}
    all_volume_index = VolumeIndex()
    archive_volumes = {}
    bucket_dict = {}

    cluster_names = {}
    model_names = {}

    for archive_path in seven_zip_files:
        try:
            result = extractor.extract_files(archive_path, [
                'volume.xml', 'object-store-bucket.xml', 'export_rule_table.xml',
                'SYSCONFIG-A.txt', 'CLUSTER-INFO.xml'
            ])

            node = extract_node_name(result.get('SYSCONFIG-A.txt', ''))
            if not node:
                node = os.path.basename(archive_path).split('.')[0]
            
            cluster_name = extract_cluster_name(result.get('CLUSTER-INFO.xml', ''))
            if cluster_name:
                cluster_names[node] = cluster_name
            
            model_name = extract_model_name(result.get('SYSCONFIG-A.txt', ''))
            if model_name:
                model_names[node] = model_name

            vol_xml_path = result.get('volume.xml', '')
            if vol_xml_path and os.path.exists(vol_xml_path):
                with open(vol_xml_path, 'r', encoding='utf-8', errors='ignore') as f:
                    xml_content = f.read()
                records = parse_volume_records(xml_content, cluster_name)
                vols, _, oss_vols = parse_volume_xml(xml_content, cluster_name, records=records)
                # 集群名缺失时用节点名兜底; 不同集群/节点的同名卷必须分开保留, 不能按卷名去重
                vol_group = cluster_name or node or os.path.basename(archive_path).split('.')[0]
                for record in records:
                    record['cluster_name'] = vol_group
                    all_volume_index.add(record)
                archive_volumes[archive_path] = records
                for vol in vols:
                    vol['cluster_name'] = vol_group
                    volume_list_dict.add(vol)
                # fg_oss 卷(Bucket 对应卷)及其子卷单独收集, 不参与 Volume List 去重
                for vol in oss_vols:
                    vol['cluster_name'] = vol_group
                    oss_volume_dict.add(vol)
                os.remove(vol_xml_path)

            export_xml_path = result.get('export_rule_table.xml', '')
            if export_xml_path and os.path.exists(export_xml_path):
                with open(export_xml_path, 'r', encoding='utf-8', errors='ignore') as f:
                    xml_content = f.read()
                rules = parse_export_rules(xml_content)
                # export_rule_table.xml 为 cluster 级数据, A/B 节点内容重复, 需去重
                rule_group = cluster_name or node or os.path.basename(archive_path).split('.')[0]
                for rule in rules:
                    rule['cluster_name'] = rule_group
                    key = (rule_group, rule['vserver'], rule['policy'], rule['rule_index'],
                           rule['protocol'], rule['client_match'], rule['ro_rule'], rule['rw_rule'])
                    export_rule_dict[key] = rule
                os.remove(export_xml_path)

            bucket_xml_path = result.get('object-store-bucket.xml', '')
            if bucket_xml_path and os.path.exists(bucket_xml_path):
                with open(bucket_xml_path, 'r', encoding='utf-8', errors='ignore') as f:
                    xml_content = f.read()
                buckets = parse_bucket_xml(xml_content, cluster_name)
                for b in buckets:
                    key = (b['cluster_name'], b['vserver'], b['bucket'])
                    if key not in bucket_dict or (b['size_bytes'] is not None and
                            (bucket_dict[key]['size_bytes'] is None or b['size_bytes'] > bucket_dict[key]['size_bytes'])):
                        bucket_dict[key] = b
                os.remove(bucket_xml_path)

            sysconfig_path = result.get('SYSCONFIG-A.txt', '')
            if sysconfig_path and os.path.exists(sysconfig_path):
                os.remove(sysconfig_path)
            
            cluster_info_path = result.get('CLUSTER-INFO.xml', '')
            if cluster_info_path and os.path.exists(cluster_info_path):
                os.remove(cluster_info_path)
        except VolumeIdentityError:
            extractor.cleanup()
            raise
        except Exception as e:
            print(f'  处理 {os.path.basename(archive_path)} 时出错: {e}')

    extractor.cleanup()
    print(f'  Volume List: {len(volume_list_dict)} 个有效卷')
    print(f'  Bucket对应fg_oss卷: {len(oss_volume_dict)} 个')
    print(f'  Export Policy 规则: {len(export_rule_dict)} 条')
    tier_index = TieringIndex(all_volume_index.values())
    print(f'  FlexGroup子卷统计: { {key: len(children) for key, children in tier_index.children.items()} }')
    print(f'  Bucket: {len(bucket_dict)} 条记录')

    # ============ 阶段1.5: 解析 VOL-STATUS-F.txt 分层容量 ============
    print('\n阶段1.5: 解析 VOL-STATUS-F.txt (性能层/容量层占用)...')
    extractor_tier = ArchiveExtractor()
    for archive_path in seven_zip_files:
        try:
            result = extractor_tier.extract_files(archive_path, ['VOL-STATUS-F.txt'])
            vsf_path = result.get('VOL-STATUS-F.txt', '')
            if vsf_path and os.path.exists(vsf_path):
                with open(vsf_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                tier_index.add_archive(archive_path, archive_volumes.get(archive_path, []), parse_volstatus_blocks(content))
                os.remove(vsf_path)
                gc.collect()
        except VolumeIdentityError:
            extractor_tier.cleanup()
            raise
        except Exception as e:
            tier_index.findings.append({'kind': 'tier_parse_failed', 'archive': archive_path, 'error': str(e)})
            print(f'  解析 VOL-STATUS-F 出错 {os.path.basename(archive_path)}: {e}')
    extractor_tier.cleanup()
    print(f'  分层容量已核实对象数: {len(tier_index.samples)}')

    # ============ 阶段2: 解析重删压缩比 ============
    print('\n阶段2: 处理重删压缩比数据...')
    node_dedupe_ratios = {}
    extractor2 = ArchiveExtractor()
    for archive_path in seven_zip_files:
        try:
            result = extractor2.extract_files(archive_path, ['aggr-efficiency.xml'])
            xml_path = result.get('aggr-efficiency.xml', '')
            if xml_path and os.path.exists(xml_path):
                with open(xml_path, 'r', encoding='utf-8') as f:
                    xml_content = f.read()
                ratios = parse_dedupe_ratio(xml_content)
                node_dedupe_ratios.update(ratios)
                os.remove(xml_path)
                gc.collect()
        except Exception as e:
            print(f'  重删数据出错 {os.path.basename(archive_path)}: {e}')
    extractor2.cleanup()

    # ============ 阶段3: 解析容量数据 (DF-A, DF, DF-I, AGGR-STATUS) ============
    print('\n阶段3: 处理容量数据...')
    capacity_rows = []
    top_aggregate_rows = []
    top_inode_rows = []
    top_volume_rows = []
    flexgroup_data = defaultdict(lambda: {'node': None, 'df_i': None, 'df': None})

    extractor3 = ArchiveExtractor()
    for archive_path in seven_zip_files:
        try:
            result = extractor3.extract_files(archive_path, [
                'DF-A.txt', 'SYSCONFIG-A.txt', 'DF.txt', 'AGGR-STATUS-S.txt', 'DF-I.txt', 'aggr-info.xml'
            ])

            df_a_path = result.get('DF-A.txt', '')
            sysconfig_path = result.get('SYSCONFIG-A.txt', '')
            df_path = result.get('DF.txt', '')
            aggr_path = result.get('AGGR-STATUS-S.txt', '')
            df_i_path = result.get('DF-I.txt', '')
            aggr_info_path = result.get('aggr-info.xml', '')

            if not sysconfig_path or not os.path.exists(sysconfig_path):
                for p in [df_a_path, sysconfig_path, df_path, aggr_path, df_i_path, aggr_info_path]:
                    if p and os.path.exists(p):
                        os.remove(p)
                continue

            node = extract_node_name(sysconfig_path)
            if not node:
                for p in [df_a_path, sysconfig_path, df_path, aggr_path, df_i_path, aggr_info_path]:
                    if p and os.path.exists(p):
                        os.remove(p)
                continue

            local_names = local_volume_names(archive_volumes.get(archive_path, []))
            has_df_a = df_a_path and os.path.exists(df_a_path)
            total_tb = 0
            used_tb = 0
            used_pct = 0

            if has_df_a:
                # Normal path: use DF-A.txt for aggregate-level capacity
                # TOP aggregate
                top_aggs = parse_df_a_for_top_aggregates(df_a_path, node)
                for aggr in top_aggs:
                    top_aggregate_rows.append([
                        cluster_names.get(node, ''), aggr['node'], aggr['aggregate'], 
                        aggr['total_tb'], aggr['used_tb'], aggr['avail_tb'], f"{aggr['capacity']}%"
                    ])
                # Capacity
                total_kbytes, total_used = parse_df_a_for_capacity(df_a_path)
                total_tb = total_kbytes / (1024 ** 3)
                used_tb = total_used / (1024 ** 3)
                used_pct = int((total_used / total_kbytes) * 100) if total_kbytes > 0 else 0
            elif aggr_path and os.path.exists(aggr_path):
                # Fallback 1: use AGGR-STATUS-S.txt Total Physical Used + percentage
                from parsers.aggr import parse_aggr_status_physical_used
                phy_used_tb, phy_total_tb = parse_aggr_status_physical_used(aggr_path)
                if phy_total_tb > 0:
                    total_tb = phy_total_tb
                    used_tb = phy_used_tb
                    used_pct = int((used_tb / total_tb) * 100)
                    print(f"    [Fallback1] {node}: capacity from AGGR-STATUS-S ({used_tb}/{total_tb}TB, {used_pct}%)")
                else:
                    print(f"    [Fallback1] {node}: AGGR-STATUS-S also empty")
                    # Fallback 2: use aggr-info.xml
                    if aggr_info_path and os.path.exists(aggr_info_path):
                        from parsers.aggr import parse_aggr_info_xml_for_capacity
                        total_tb, used_tb, used_pct = parse_aggr_info_xml_for_capacity(aggr_info_path)
                        if total_tb > 0:
                            print(f"    [Fallback2] {node}: capacity from aggr-info.xml ({used_tb}/{total_tb}TB, {used_pct}%)")
                        else:
                            print(f"    [Fallback2] {node}: aggr-info.xml also empty, skipping capacity data")
            elif aggr_info_path and os.path.exists(aggr_info_path):
                # Fallback 2: use aggr-info.xml
                from parsers.aggr import parse_aggr_info_xml_for_capacity
                total_tb, used_tb, used_pct = parse_aggr_info_xml_for_capacity(aggr_info_path)
                if total_tb > 0:
                    print(f"    [Fallback2] {node}: capacity from aggr-info.xml ({used_tb}/{total_tb}TB, {used_pct}%)")
                else:
                    print(f"    [Fallback2] {node}: aggr-info.xml empty, skipping capacity data")

            # DF.txt - 已分配vol size + TOP volume + FlexGroup capacity
            allocated_tb = 0
            if df_path and os.path.exists(df_path):
                top_vols_df, flex_cap, total_kbytes_df = parse_df(df_path, node)
                allocated_tb = total_kbytes_df / (1024 ** 3)
                for vol in top_vols_df:
                    top_volume_rows.append([
                        cluster_names.get(node, ''), vol['node'], vol['volume_name'], 
                        vol['size_tb'], vol['used_tb'], f"{vol['capacity_percent']}%"
                    ])
                for vname, cdata in flex_cap.items():
                    key = _constituent_key(archive_path, node, vname, local_names, tier_index)
                    if key is None:
                        continue
                    flexgroup_data[key]['node'] = node
                    flexgroup_data[key]['df'] = cdata

            # AGGR-STATUS - 下沉空间
            logical_used_tb = 0
            if aggr_path and os.path.exists(aggr_path):
                logical_used_tb = parse_aggr_status_logical_used(aggr_path)

            # DF-I.txt
            if df_i_path and os.path.exists(df_i_path):
                top_inodes, flex_inode = parse_df_i(df_i_path, node)
                for vol in top_inodes:
                    top_inode_rows.append([
                        cluster_names.get(node, ''), vol['node'], vol['volume_name'], 
                        vol['total_inode_yi'], vol['iused_yi'], f"{vol['percent_iused']}%"
                    ])
                for vname, idata in flex_inode.items():
                    key = _constituent_key(archive_path, node, vname, local_names, tier_index)
                    if key is None:
                        continue
                    flexgroup_data[key]['node'] = node
                    flexgroup_data[key]['df_i'] = idata

            # 超分比
            dedupe = node_dedupe_ratios.get(node, 1.0)
            denom = total_tb + logical_used_tb
            over = int((allocated_tb / denom) * 100) if denom > 0 else 0

            capacity_rows.append([
                cluster_names.get(node, ''), node, model_names.get(node, ''), 
                round(total_tb, 2), round(used_tb, 2),
                f'{used_pct}%', round(allocated_tb, 2), logical_used_tb,
                f'{over}%' if denom > 0 else 'N/A', dedupe
            ])

            for p in [df_a_path, sysconfig_path, df_path, aggr_path, df_i_path, aggr_info_path]:
                if p and os.path.exists(p):
                    os.remove(p)
            gc.collect()
        except Exception as e:
            print(f'  处理 {os.path.basename(archive_path)} 容量时出错: {e}')

    extractor3.cleanup()

    # ============ 汇总 FlexGroup 数据 ============
    flex_rows = []
    for key, data in flexgroup_data.items():
        if data['df_i'] or data['df']:
            node = data['node']
            vname = key[3]
            inode = data['df_i']
            cap = data['df']
            flex_rows.append((node, vname, inode, cap))
    flex_rows.sort(key=lambda x: (x[1].lower(), x[0].lower()))
    flexgroup_sheet_rows = []
    for node, vname, inode, cap in flex_rows:
        flexgroup_sheet_rows.append([
            cluster_names.get(node, ''), vname, node,
            round(inode['total_inode'] / 100000000, 2) if inode else 'N/A',
            round(inode['iused'] / 100000000, 2) if inode else 'N/A',
            f"{inode['percent_iused']}%" if inode else 'N/A',
            cap['size_tb'] if cap else 'N/A', cap['used_tb'] if cap else 'N/A',
            f"{cap['capacity_percent']}%" if cap else 'N/A'
        ])

    # ============ 汇总 Volume List ============
    vol_list_items = list(volume_list_dict.values())
    for vol in vol_list_items:
        fg_key = (vol.get('cluster_name', ''), vol.get('site_name', ''), vol['volume_name'])
        fg_cnt = len(tier_index.children.get(fg_key, set()))
        if fg_cnt:
            vol['volume_style'] = f"FlexGroup-{fg_cnt}"
    vol_list_items.sort(key=lambda x: (x.get('site_name', ''), x.get('size_tb') is None, -(x.get('size_tb') or 0)))
    volume_list_rows = []
    for vol in vol_list_items:
        used_pct = vol.get('used_percent')
        tiering_policy = vol.get('tiering_policy', '') or ''
        tiering_min_cooling_days = vol.get('tiering_min_cooling_days', '') or ''
        perf_tier = 'N/A'
        cap_tier = 'N/A'
        if tiering_policy and tiering_policy.lower() not in ('none',):
            tu = tier_index.usage(vol)
            if tu:
                if tu.get('perf_tb') is not None:
                    perf_tier = round(tu['perf_tb'], 2)
                if tu.get('bucket_tb') is not None:
                    cap_tier = round(tu['bucket_tb'], 2)
        inactive_raw = vol.get('inactive_user_data_tb')
        inactive_user_data_tb = round(inactive_raw, 2) if inactive_raw is not None else 'N/A'
        tiering_min_cooling_days = tiering_min_cooling_days if tiering_min_cooling_days else 'N/A'
        aggr_name = vol.get('aggr_name', '') or ''
        volume_list_rows.append([
            vol.get('cluster_name', ''), vol['volume_name'], vol.get('site_name', ''), aggr_name if aggr_name else 'N/A', vol.get('volume_style', ''),
            _display(vol.get('size_tb')), _display(vol.get('avail_tb')), _display(vol.get('used_tb')),
            _percent(used_pct), _percent(vol.get('logical_used_percent'), decimals=True),
            _files(vol.get('total_files_yi')), _files(vol.get('used_files_yi')),
            _display(vol.get('space_reporting_logical')), _display(vol.get('space_enforcement_logical')), _display(vol.get('snapshot_policy')),
            tiering_policy, tiering_min_cooling_days, perf_tier, cap_tier, inactive_user_data_tb
        ])

    # ============ 汇总 Bucket ============
    bucket_rows = []
    for b in bucket_dict.values():
        size_bytes = b.get('size_bytes')
        logical_bytes = b.get('logical_bytes')
        object_count = b.get('object_count')
        size_tb = int(size_bytes / (1024**4)) if size_bytes is not None else None
        logical_tb = int(logical_bytes / (1024**4)) if logical_bytes is not None else None
        used_pct = int(round(logical_tb / size_tb * 100, 0)) if size_tb is not None and size_tb > 0 and logical_tb is not None else None
        obj_yi = round(object_count / 100000000, 2) if object_count is not None else None
        bucket_rows.append([
            b.get('cluster_name', ''), b.get('vserver', ''), b.get('bucket', ''), b.get('volume', ''),
            _display(size_tb), _display(obj_yi), _display(logical_tb), _percent(used_pct)
        ])

    # ============ 汇总 Bucket 对应的 fg_oss FlexGroup 卷(含子卷) ============
    # Bucket 实际落在 fg_oss 前缀的 FlexGroup 卷上, 两者 size 口径不同, 需单独列出对照
    bucket_volume_rows = []
    oss_items = sorted(oss_volume_dict.values(),
                       key=lambda x: (x.get('volume_name', '').lower(), x.get('cluster_name', '')))
    for vol in oss_items:
        used_pct = vol.get('used_percent')
        aggr_name = vol.get('aggr_name', '') or ''
        bucket_volume_rows.append([
            vol.get('cluster_name', ''), vol['volume_name'], vol.get('site_name', ''),
            aggr_name if aggr_name else 'N/A',
            _display(vol.get('size_tb')), _display(vol.get('avail_tb')), _display(vol.get('used_tb')),
            _percent(used_pct), _percent(vol.get('logical_used_percent'), decimals=True),
            _files(vol.get('total_files_yi')), _files(vol.get('used_files_yi')),
            _display(vol.get('space_reporting_logical')), _display(vol.get('space_enforcement_logical')),
            _display(vol.get('snapshot_policy'))
        ])

    # ============ 汇总 Export Policy 规则与对应卷 ============
    policy_volumes = defaultdict(list)
    for vol in volume_list_dict.values():
        policy_name = (vol.get('export_policy') or '').strip()
        if not policy_name:
            continue
        key = (vol.get('cluster_name', ''), vol.get('site_name', ''), policy_name)
        policy_volumes[key].append(vol)

    export_rule_rows = []
    for rule in sorted(export_rule_dict.values(), key=lambda x: (
            x.get('cluster_name', '').lower(), x.get('vserver', '').lower(),
            x.get('policy', '').lower(), _rule_index_key(x.get('rule_index', '')))):
        key = (rule.get('cluster_name', ''), rule.get('vserver', ''), rule.get('policy', ''))
        export_rule_rows.append([
            rule.get('cluster_name', ''), rule.get('vserver', ''), rule.get('policy', ''),
            rule.get('rule_index', ''), rule.get('protocol', ''), rule.get('client_match', ''),
            rule.get('ro_rule', ''), rule.get('rw_rule', ''), rule.get('anon', ''),
            rule.get('superuser', ''), rule.get('allow_suid', ''), rule.get('allow_dev', ''),
            len(policy_volumes.get(key, [])),
        ])

    export_volume_rows = []
    for key in sorted(policy_volumes, key=lambda k: (k[0].lower(), k[1].lower(), k[2].lower())):
        for vol in sorted(policy_volumes[key], key=lambda v: v.get('volume_name', '').lower()):
            used_pct = vol.get('used_percent')
            export_volume_rows.append([
                key[0], key[1], key[2], _display(vol.get('size_tb')),
                _percent(used_pct),
                vol.get('volume_name', ''),
                _percent(vol.get('logical_used_percent'), decimals=True),
            ])
    print(f'  Export Policy 规则: {len(export_rule_rows)} 条, Policy->Volume: {len(export_volume_rows)} 条')

    # ============ 导出 Excel ============
    print('\n导出 Excel ...')
    all_data = {
        'total_7z': len(seven_zip_files),
        'capacity': capacity_rows,
        'top_aggregate': top_aggregate_rows,
        'top_inode': top_inode_rows,
        'top_volume': top_volume_rows,
        'flexgroup': flexgroup_sheet_rows,
        'volume_list': volume_list_rows,
        'bucket': bucket_rows,
        'bucket_volumes': bucket_volume_rows,
        'export_rules': export_rule_rows,
        'export_volumes': export_volume_rows,
    }

    exporter = HealthCheckExporter()
    dir_name = os.path.basename(input_dir.rstrip('/\\'))
    if not dir_name:
        dir_name = input_dir
    exporter.export(output_path, all_data, dir_name)
    audit = tier_index.audit(volume_list_dict.values())
    audit['missing_constituent_sources'] = [{'identity': list(key[1:]), 'node': key[0],
                                            'missing_sources': [name for name in ('df', 'df_i') if not data[name]]}
                                           for key, data in flexgroup_data.items() if not data['df'] or not data['df_i']]
    audit['missing_bucket_fields'] = [{'identity': [bucket['cluster_name'], bucket['vserver'], bucket['bucket']],
                                      'fields': [field for field in ('size_bytes', 'logical_bytes', 'object_count')
                                                 if bucket.get(field) is None]}
                                     for bucket in bucket_dict.values()
                                     if any(bucket.get(field) is None for field in ('size_bytes', 'logical_bytes', 'object_count'))]
    numeric_fields = ('size_tb', 'avail_tb', 'used_tb', 'used_percent', 'logical_used_percent',
                      'total_files_yi', 'used_files_yi', 'inactive_user_data_tb')
    audit['missing_volume_fields'] = [{'identity': [vol['cluster_name'], vol['site_name'], vol['volume_name']],
                                       'fields': [field for field in numeric_fields if vol.get(field) is None]}
                                      for vol in list(volume_list_dict.values()) + list(oss_volume_dict.values())
                                      if any(vol.get(field) is None for field in numeric_fields)]
    if audit['missing_volume_fields'] or audit['missing_constituent_sources'] or audit['missing_bucket_fields']:
        audit['status'] = 'needs_review'
    Path(output_path).with_suffix('.capacity-audit.json').write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')

    return {
        'total_7z': len(seven_zip_files),
        'nodes': len(capacity_rows),
        'top_inode': len(top_inode_rows),
        'top_capacity': len(top_volume_rows),
        'flexgroup': len(flexgroup_sheet_rows),
        'hot_aggr': len(top_aggregate_rows),
        'volume_list': len(volume_list_rows),
        'bucket': len(bucket_rows),
        'bucket_volumes': len(bucket_volume_rows),
        'export_rules': len(export_rule_rows),
        'export_volumes': len(export_volume_rows),
        'output': output_path,
    }


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print('错误: 请指定输入目录')
        sys.exit(1)

    input_dir = sys.argv[1]
    if len(sys.argv) >= 3:
        output_path = sys.argv[2]
    else:
        output_path = os.path.join(input_dir, 'NetApp_Capacity_Check.xlsx')

    if not os.path.isdir(input_dir):
        print(f'错误: 目录不存在: {input_dir}')
        sys.exit(1)

    print('=' * 60)
    print('NetApp ASUP Capacity Check 工具')
    print('=' * 60)
    print(f'输入目录: {input_dir}')
    print(f'输出文件: {output_path}')
    print('-' * 60)

    try:
        result = process_directory(input_dir, output_path)
        print('-' * 60)
        print('处理完成!')
        print(f'  节点数: {result["nodes"]}')
        print(f'  TOP inode: {result["top_inode"]}')
        print(f'  TOP容量: {result["top_capacity"]}')
        print(f'  FlexGroup: {result["flexgroup"]}')
        print(f'  聚合: {result["hot_aggr"]}')
        print(f'  卷清单: {result["volume_list"]}')
        print(f'  Bucket: {result["bucket"]}')
        print(f'  Bucket对应fg_oss卷: {result["bucket_volumes"]}')
        print(f'  Export Policy 规则: {result["export_rules"]}')
        print(f'  Export Policy->Volume: {result["export_volumes"]}')
        print(f'  输出: {result["output"]}')
        print('=' * 60)
    except Exception as e:
        print(f'\n错误: {e}')
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
