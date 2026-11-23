"""
VOL-STATUS-F.txt 解析模块

从每个节点的 ASUP 归档中解析各卷在性能层(Performance Tier)与容量层(Bucket)
的占用数据，用于 Volume List 的 Tiering 分层容量展示。
"""
import re

_UNIT_SCALE = {
    'tb': 1.0, 'tib': 1.0,
    'gb': 1 / 1024, 'gib': 1 / 1024,
    'mb': 1 / (1024 ** 2), 'mib': 1 / (1024 ** 2),
    'kb': 1 / (1024 ** 3), 'kib': 1 / (1024 ** 3),
    'b': 1 / (1024 ** 4),
}

_PAT_PERF = re.compile(
    r'Footprint in Performance Tier\s+([\d.]+)\s*(TB|GB|MB|KB|TiB|GiB|MiB|KiB|B)\b',
    re.IGNORECASE)
_PAT_BUCKET = re.compile(
    r'Footprint in (?!Performance\s+Tier\b)([A-Za-z0-9_.-]+)\s+([\d.]+)\s*(TB|GB|MB|KB|TiB|GiB|MiB|KiB|B)\b',
    re.IGNORECASE)


def _to_tb(value: str, unit: str):
    try:
        num = float(value.replace(',', ''))
        return round(num * _UNIT_SCALE.get(unit.lower(), 0.0), 4)
    except (TypeError, ValueError):
        return None


def parse_volstatus_blocks(content: str) -> list:
    """
    解析 VOL-STATUS-F.txt 内容

    Args:
        content: VOL-STATUS-F.txt 文件内容

    Returns:
        dict: {volume_name: {'perf_tb': float|None, 'bucket_tb': float|None,
                             'bucket_name': str}}
    """
    result = []
    blocks = re.split(r'(?m)^Volume\s*:\s*', content)
    for block in blocks[1:]:
        lines = block.splitlines()
        if not lines:
            continue
        vol_name = lines[0].strip()
        if not vol_name:
            continue
        perf_tb = None
        bucket_tb = None
        bucket_name = ''
        bucket_values = {}
        for line in lines[1:]:
            m = _PAT_PERF.search(line)
            if m:
                perf_tb = _to_tb(m.group(1), m.group(2))
                continue
            m = _PAT_BUCKET.search(line)
            if m:
                bucket_tb = _to_tb(m.group(2), m.group(3))
                bucket_name = m.group(1)
                bucket_values[bucket_name] = bucket_tb
        if len(bucket_values) > 1:
            bucket_tb = None
        result.append({
            'volume_name': vol_name,
            'perf_tb': perf_tb,
            'bucket_tb': bucket_tb,
            'bucket_name': bucket_name,
            'bucket_values': bucket_values,
        })
    return result


def parse_volstatus_f(content: str) -> dict:
    return {block['volume_name']: block for block in parse_volstatus_blocks(content)}
