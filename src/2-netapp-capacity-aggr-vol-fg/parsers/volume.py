"""
volume.xml 解析模块
提取 Volume List 和 FlexGroup 子卷统计
"""
import re
import math
import xml.etree.ElementTree as ET
from collections import defaultdict
from config import SKIP_VOLUME_PREFIXES, SKIP_VOLUME_SUFFIXES, SKIP_VOLUME_NAMES, OSS_VOLUME_PREFIX

PATTERN_SUBVOLUME = re.compile(r'__\d{4}$')


def parse_volume_records(xml_content: str, cluster_name: str = '') -> list:
    root = ET.fromstring(xml_content)
    records = []
    for row in root.iter():
        if row.tag.rsplit('}', 1)[-1] != 'ROW':
            continue
        fields = {child.tag.rsplit('}', 1)[-1]: ''.join(child.itertext()).strip() for child in row}
        if fields.get('vol'):
            records.append(_build_volume_record(lambda tag: fields.get(tag, ''), fields['vol'], cluster_name))
    return records


def parse_volume_xml(xml_content: str, cluster_name: str = '', records=None) -> tuple:
    """
    解析 volume.xml 内容
    
    Args:
        xml_content: volume.xml 文件内容
        cluster_name: 集群名称

    Returns:
        tuple: (volume_list, flexgroup_subvolume_count, oss_volume_list)
            volume_list: list of dict - 有效卷信息
            flexgroup_subvolume_count: dict - FlexGroup子卷计数
            oss_volume_list: list of dict - fg_oss 前缀卷(Bucket 对应卷)及其子卷
    """
    volumes = []
    oss_volumes = []
    fg_subvolume_count = defaultdict(int)

    try:
        for record in records if records is not None else parse_volume_records(xml_content, cluster_name):
            vol_name = record['volume_name']

            # fg_oss 前缀卷 = Bucket(object store) 对应的 FlexGroup 卷及其子卷
            # 单独收集, 由调用方输出在 Bucket sheet 下方, 不进入 Volume List
            if vol_name.lower().startswith(OSS_VOLUME_PREFIX):
                if PATTERN_SUBVOLUME.search(vol_name):
                    parent_name = re.sub(r'__\d{4}$', '', vol_name)
                    if parent_name:
                        fg_subvolume_count[parent_name] += 1
                oss_volumes.append(record)
                continue

            # 统计 FlexGroup 子卷
            if PATTERN_SUBVOLUME.search(vol_name):
                parent_name = re.sub(r'__\d{4}$', '', vol_name)
                if parent_name:
                    fg_subvolume_count[parent_name] += 1
                continue

            # 过滤系统卷
            if vol_name.lower() in tuple(n.lower() for n in SKIP_VOLUME_NAMES):
                continue
            if any(vol_name.lower().startswith(p.lower()) for p in SKIP_VOLUME_PREFIXES):
                continue
            if any(vol_name.lower().endswith(s.lower()) for s in SKIP_VOLUME_SUFFIXES):
                continue

            volumes.append(record)
    except Exception as e:
        print(f"解析 volume.xml 时出错: {e}")

    return volumes, dict(fg_subvolume_count), oss_volumes


def _build_volume_record(get_field, vol_name: str, cluster_name: str) -> dict:
    """按 volume.xml 单行记录构造卷信息字典(普通卷与 fg_oss 卷共用)"""
    size_tb = _to_tb(get_field('size'))
    avail_tb = _to_tb(get_field('avail'))
    used_tb = _to_tb(get_field('used'))
    used_pct = _to_percent(get_field('pcnt_used'))

    inactive_raw = get_field('perfTierInactiveUserData')
    return {
        'cluster_name': cluster_name,
        'volume_name': vol_name,
        'volume_uuid': get_field('vol_uuid'),
        'fg_uuid': get_field('fgUuid'),
        'fg_msid': get_field('fgMsid'),
        'name_ordinal': get_field('name_ordinal'),
        'site_name': get_field('vs'),
        'aggr_name': get_field('aggr'),
        'volume_style': get_field('styleEx'),
        'size_tb': size_tb,
        'avail_tb': avail_tb,
        'used_tb': used_tb,
        'used_percent': used_pct,
        'total_files_yi': _to_yi(get_field('files')),
        'used_files_yi': _to_yi(get_field('files_used')),
        'space_reporting_logical': get_field('logicalSpaceReporting'),
        'space_enforcement_logical': get_field('logicalSpaceEnforcement'),
        'snapshot_policy': get_field('snap_policy'),
        'export_policy': get_field('policy'),
        'logical_used_percent': _to_percent(get_field('logicalUsedPercent')),
        'tiering_policy': get_field('tiering_policy'),
        'tiering_min_cooling_days': get_field('tiering_min_cooling_days'),
        'inactive_user_data_tb': _to_tb(inactive_raw) if inactive_raw and inactive_raw.strip() else None,
    }


def _to_tb(s: str) -> float:
    if not s:
        return None
    match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(Ti?B|Gi?B|Mi?B|Ki?B|B)?', s.strip().replace(',', ''), re.IGNORECASE)
    if not match:
        return None
    scale = {'tb': 1, 'tib': 1, 'gb': 1 / 1024, 'gib': 1 / 1024,
             'mb': 1 / 1024**2, 'mib': 1 / 1024**2, 'kb': 1 / 1024**3,
             'kib': 1 / 1024**3, 'b': 1 / 1024**4}
    value = float(match[1]) * scale[(match[2] or 'b').lower()]
    return round(value, 2) if math.isfinite(value) else None


def _to_yi(s: str) -> float:
    try:
        if s and s.strip():
            num = int(s.replace(',', ''))
            return round(num / 100000000, 2) if num >= 0 else None
    except:
        pass
    return None


def _to_percent(s: str) -> float:
    try:
        if s and s.strip():
            value = float(s.replace('%', '').strip())
            if math.isfinite(value) and value >= 0:
                return value
    except:
        pass
    return None
