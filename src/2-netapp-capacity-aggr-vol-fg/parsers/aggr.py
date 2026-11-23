"""
DF-A.txt 和 AGGR-STATUS-S.txt 解析模块
"""
import os
import re
from config import THRESHOLD_TOP_AGGREGATE_ROOT, THRESHOLD_TOP_AGGREGATE_OTHER, KB_TO_TB

PATTERN_DF_A_HEADER = re.compile(r'Aggregate\s+kbytes\s+used\s+avail\s+capacity')


def _to_tb(value_str: str, unit: str) -> float:
    """将 '数值+单位' 转为 TB"""
    try:
        value = float(value_str)
    except ValueError:
        return 0.0
    unit = unit.upper()
    if unit == 'TB':
        return value
    if unit == 'GB':
        return value / 1024
    if unit == 'MB':
        return value / (1024 ** 2)
    if unit == 'KB':
        return value / (1024 ** 3)
    if unit == 'B':
        return value / (1024 ** 4)
    return value


def parse_df_a_for_capacity(file_path: str) -> tuple:
    """
    解析 DF-A.txt 获取总容量数据

    Returns:
        tuple: (total_kbytes, total_used_kbytes)
    """
    total_kbytes = 0
    total_used = 0
    if not os.path.exists(file_path):
        return total_kbytes, total_used
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            next(f)  # 跳过表头
            for line in f:
                if '.snapshot' in line:
                    continue
                cols = re.split(r'\s+', line.strip())
                if len(cols) >= 3:
                    try:
                        total_kbytes += int(cols[1])
                        total_used += int(cols[2])
                    except:
                        continue
    except Exception as e:
        print(f"解析 DF-A.txt 容量时出错: {e}")
    return total_kbytes, total_used


def parse_df_a_for_top_aggregates(file_path: str, node_name: str) -> list:
    """
    解析 DF-A.txt 筛选高使用率聚合
    root/aggr0 > 95% 或其他 > 85%
    """
    aggregates = []
    if not os.path.exists(file_path):
        return aggregates
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        start_reading = False
        for line in lines:
            line = line.strip()
            if PATTERN_DF_A_HEADER.match(line):
                start_reading = True
                continue
            if start_reading and line and '.snapshot' not in line:
                cols = re.split(r'\s+', line)
                if len(cols) >= 5:
                    try:
                        aggregate = cols[0]
                        kbytes = int(cols[1])
                        used = int(cols[2])
                        avail = int(cols[3])
                        capacity = float(cols[4].replace('%', ''))
                        is_root_aggr0 = 'root' in aggregate.lower() or 'aggr0' in aggregate.lower()
                        if (is_root_aggr0 and capacity > THRESHOLD_TOP_AGGREGATE_ROOT) or \
                           (not is_root_aggr0 and capacity > THRESHOLD_TOP_AGGREGATE_OTHER):
                            aggregates.append({
                                'node': node_name,
                                'aggregate': aggregate,
                                'total_tb': round(kbytes * KB_TO_TB, 2),
                                'used_tb': round(used * KB_TO_TB, 2),
                                'avail_tb': round(avail * KB_TO_TB, 2),
                                'capacity': capacity,
                            })
                    except (ValueError, IndexError):
                        continue
    except Exception as e:
        print(f"解析 DF-A.txt TOP聚合时出错: {e}")
    return aggregates




def parse_aggr_info_xml_for_capacity(file_path: str) -> tuple:
    """Parse aggr-info.xml to get node capacity (TB)
    Fallback when DF-A.txt and AGGR-STATUS-S.txt are both missing
    Returns: (total_tb, used_tb, used_pct)"""
    total_tb = 0.0
    used_tb = 0.0
    used_pct = 0
    if not os.path.exists(file_path):
        return total_tb, used_tb, used_pct
    try:
        import xml.etree.ElementTree as ET
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            xml_content = f.read()
        # Remove DOCTYPE entirely (multi-line, ends with ]>)
        xml_content = re.sub(r"<!DOCTYPE.*?\]>", "", xml_content, flags=re.DOTALL)
        # Strip xmlns attributes (e.g. xmlns=, xmlns:asup=)
        xml_content = re.sub(r"\s+xmlns[^=]*=\"[^\"]*\"", "", xml_content)
        # Remove namespace prefixes
        xml_content = re.sub(r"<asup:", "<", xml_content)
        xml_content = re.sub(r"</asup:", "</", xml_content)
        root = ET.fromstring(xml_content)
        total_bytes = 0
        total_avail = 0
        for row in root.iter("ROW"):
            size_el = row.find("size")
            avail_el = row.find("available_size")
            if size_el is not None and avail_el is not None:
                try:
                    size_b = int(size_el.text.strip())
                    avail_b = int(avail_el.text.strip())
                    total_bytes += size_b
                    total_avail += avail_b
                except (ValueError, AttributeError):
                    continue
        if total_bytes > 0:
            used_bytes = total_bytes - total_avail
            total_tb = round(total_bytes / (1024 ** 4), 2)
            used_tb = round(used_bytes / (1024 ** 4), 2)
            used_pct = int((used_bytes / total_bytes) * 100)
        return total_tb, used_tb, used_pct
    except Exception as e:
        print("  aggr-info.xml capacity parse error: %s" % str(e))
        return 0.0, 0.0, 0


def parse_aggr_status_physical_used(file_path):
    """parse AGGR-STATUS-S.txt for Total Physical Used and capacity"""
    total_used_tb = 0.0
    total_capacity_tb = 0.0
    if not os.path.exists(file_path):
        return total_used_tb, total_capacity_tb
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
            pattern = re.compile(r'Total Physical Used\s+([\d.]+)\s*([TGMK]?B)\s+([\d.]+)%')
        for m in pattern.finditer(text):
            value = float(m.group(1))
            unit = m.group(2).upper()
            pct = float(m.group(3))
            if pct > 0:
                if unit == "TB":
                    used_tb = value
                elif unit == "GB":
                    used_tb = value / 1024
                elif unit == "MB":
                    used_tb = value / (1024**2)
                elif unit == "KB":
                    used_tb = value / (1024**3)
                else:
                    used_tb = value
                cap_tb = used_tb / (pct / 100.0)
                total_used_tb += used_tb
                total_capacity_tb += cap_tb
    except Exception as e:
        print("  AGGR-STATUS-S physical used parse error:", e)
    return round(total_used_tb, 2), round(total_capacity_tb, 2)

def parse_aggr_status_logical_used(file_path: str) -> float:
    'Shared candidate: use explicit local input, configuration and output paths.'
    total_tb = 0.0
    if not os.path.exists(file_path):
        return total_tb
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.read().splitlines()
        logical_used = None   # 当前表 Logical Used (TB)
        space_saved = None    # 当前表 Space Saved by Storage Efficiency (TB)
        for line in lines:
            stripped = line.strip()
            # 新的 Feature 表头: 先结算上一张表
            if 'Feature' in stripped and 'Used' in stripped:
                if logical_used is not None and space_saved is not None:
                    total_tb += logical_used - space_saved
                logical_used = None
                space_saved = None
                continue
            m = re.match(r'Logical Used\s+([\d.]+)\s*([TGMK]?B)', stripped)
            if m:
                logical_used = _to_tb(m.group(1), m.group(2))
                continue
            m = re.match(r'Space Saved by Storage Efficiency\s+([\d.]+)\s*([TGMK]?B)', stripped)
            if m:
                space_saved = _to_tb(m.group(1), m.group(2))
                continue
        # 结算最后一张表
        if logical_used is not None and space_saved is not None:
            total_tb += logical_used - space_saved
    except Exception as e:
        print(f"解析 AGGR-STATUS-S.txt 时出错: {e}")
    return round(total_tb, 2)
