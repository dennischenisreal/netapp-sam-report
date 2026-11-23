"""
DF.txt 和 DF-I.txt 解析模块
"""
import os
import re
from config import (
    THRESHOLD_TOP_INODE_COUNT, THRESHOLD_TOP_INODE_PERCENT,
    THRESHOLD_TOP_VOLUME_SIZE_TB, THRESHOLD_TOP_VOLUME_PERCENT, KB_TO_TB
)

PATTERN_DF_HEADER = re.compile(r'Filesystem\s+kbytes\s+used\s+avail\s+capacity\s+Mounted on')
PATTERN_DF_I_HEADER = re.compile(r'Filesystem\s+iused\s+ifree\s+%iused')
PATTERN_VOLUME = re.compile(r'/vol/([^/]+)')
PATTERN_FLEXGROUP = re.compile(r'.*__000\d$|.*__00\d{2}$|.*__\d{4}$')


def _extract_volume_name(filesystem_path: str) -> str:
    match = PATTERN_VOLUME.search(filesystem_path)
    if match:
        return match.group(1)
    return os.path.basename(filesystem_path.rstrip('/'))


def _is_flexgroup_volume(volume_name: str) -> bool:
    if 'bucket' in volume_name.lower():
        return False
    if volume_name.lower().startswith('fg_oss'):
        return False
    return bool(PATTERN_FLEXGROUP.match(volume_name))


def parse_df_i(file_path: str, node_name: str) -> tuple:
    """
    解析 DF-I.txt

    Returns:
        tuple: (top_inodes, flexgroup_inode_data)
    """
    top_inodes = []
    flexgroup_inode = {}
    if not os.path.exists(file_path):
        return top_inodes, flexgroup_inode
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        start_reading = False
        for line in lines:
            line = line.strip()
            if PATTERN_DF_I_HEADER.match(line):
                start_reading = True
                continue
            if start_reading and line and not line.startswith('/proc'):
                cols = re.split(r'\s+', line)
                if len(cols) >= 4:
                    try:
                        iused = int(cols[1])
                        ifree = int(cols[2])
                        percent_iused = float(cols[3].replace('%', ''))
                        total_inode = iused + ifree
                        volume_name = _extract_volume_name(cols[0])

                        if _is_flexgroup_volume(volume_name):
                            flexgroup_inode[volume_name] = {
                                'iused': iused, 'ifree': ifree,
                                'total_inode': total_inode, 'percent_iused': percent_iused
                            }

                        if iused > THRESHOLD_TOP_INODE_COUNT and percent_iused > THRESHOLD_TOP_INODE_PERCENT:
                            top_inodes.append({
                                'node': node_name,
                                'volume_name': volume_name,
                                'total_inode_yi': round(total_inode / 100000000, 2),
                                'iused_yi': round(iused / 100000000, 2),
                                'percent_iused': percent_iused,
                            })
                    except (ValueError, IndexError):
                        continue
    except Exception as e:
        print(f"解析 DF-I.txt 时出错: {e}")
    return top_inodes, flexgroup_inode


def parse_df(file_path: str, node_name: str) -> tuple:
    """
    解析 DF.txt

    Returns:
        tuple: (top_volumes, flexgroup_capacity, total_kbytes)
    """
    top_volumes = []
    flexgroup_cap = {}
    total_kbytes = 0
    if not os.path.exists(file_path):
        return top_volumes, flexgroup_cap, total_kbytes
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            next(f)  # 跳过表头
            for line in f:
                cols = re.split(r'\s+', line.strip())
                if len(cols) >= 6 and cols[0].startswith('/vol/'):
                    if cols[1].isdigit():
                        total_kbytes += int(cols[1])
                elif len(cols) >= 7 and cols[:2] == ['snap', 'reserve']:
                    if cols[2].isdigit():
                        total_kbytes += int(cols[2])

        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        start_reading = False
        for line in lines:
            line = line.strip()
            if PATTERN_DF_HEADER.match(line):
                start_reading = True
                continue
            if start_reading and line and not line.startswith('/proc'):
                cols = re.split(r'\s+', line)
                if len(cols) >= 6 and '.snapshot' not in cols[0]:
                    try:
                        kbytes = int(cols[1])
                        used_kb = int(cols[2])
                        capacity_percent = float(cols[4].replace('%', ''))
                        volume_name = _extract_volume_name(cols[0])

                        if _is_flexgroup_volume(volume_name):
                            flexgroup_cap[volume_name] = {
                                'size_tb': round(kbytes * KB_TO_TB, 2),
                                'used_tb': round(used_kb * KB_TO_TB, 2),
                                'capacity_percent': capacity_percent,
                            }

                        used_tb = used_kb * KB_TO_TB
                        if used_tb > THRESHOLD_TOP_VOLUME_SIZE_TB and capacity_percent > THRESHOLD_TOP_VOLUME_PERCENT:
                            top_volumes.append({
                                'node': node_name,
                                'volume_name': volume_name,
                                'size_tb': round(kbytes * KB_TO_TB, 2),
                                'used_tb': round(used_tb, 2),
                                'capacity_percent': capacity_percent,
                            })
                    except (ValueError, IndexError):
                        continue
    except Exception as e:
        print(f"解析 DF.txt 时出错: {e}")
    return top_volumes, flexgroup_cap, total_kbytes
