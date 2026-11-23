"""
object-store-bucket.xml 解析模块
"""
import re
import math


def parse_bucket_xml(xml_content: str, cluster_name: str = '') -> list:
    """解析 object-store-bucket.xml，返回 bucket 列表
    
    Args:
        xml_content: object-store-bucket.xml 文件内容
        cluster_name: 集群名称
    """
    buckets = []
    try:
        content = re.sub(r'xmlns(:[^=]*)?="[^"]*"', '', xml_content)
        rows = re.findall(r'<ROW[^>]*>(.*?)</ROW>', content, re.DOTALL)
        if not rows:
            rows = re.findall(r'<asup:ROW[^>]*>(.*?)</asup:ROW>', content, re.DOTALL)

        for row in rows:
            def extract(tag):
                pattern = rf'<[^>]*{tag}[^>]*>(.*?)</[^>]*{tag}[^>]*>'
                m = re.search(pattern, row, re.DOTALL | re.IGNORECASE)
                return m.group(1).strip() if m else ""

            bucket_name = extract('bucket')
            if not bucket_name:
                continue

            buckets.append({
                'cluster_name': cluster_name,
                'vserver': extract('vserver'),
                'bucket': bucket_name,
                'volume': extract('volume'),
                'size_bytes': _to_number(extract('size_of_bucket')),
                'object_count': _to_number(extract('object_count')),
                'logical_bytes': _to_number(extract('logical_used_size')),
            })
    except Exception as e:
        print(f"解析 object-store-bucket.xml 时出错: {e}")
    return buckets


def _to_number(s: str) -> float:
    if s:
        s = s.replace(',', '')
        try:
            value = float(s)
            if math.isfinite(value) and value >= 0:
                return value
        except:
            pass
    return None
