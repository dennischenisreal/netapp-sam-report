"""
aggr-efficiency.xml 解析模块 - 重删压缩比
"""
import re

PATTERN_NODE = re.compile(r'<(?:asup:)?node>([^<]+)</(?:asup:)?node>')
PATTERN_TDRLUWS = re.compile(r'<(?:asup:)?tdrluws>([^<]+)</(?:asup:)?tdrluws>')
PATTERN_TDRPUWS = re.compile(r'<(?:asup:)?tdrpuws>([^<]+)</(?:asup:)?tdrpuws>')


def parse_dedupe_ratio(xml_content: str) -> dict:
    """
    解析 aggr-efficiency.xml 获取每个节点的重删压缩比

    Returns:
        dict: {node_name: dedupe_ratio}
    """
    node_data = {}
    try:
        nodes = PATTERN_NODE.findall(xml_content)
        tdrluws_list = PATTERN_TDRLUWS.findall(xml_content)
        tdrpuws_list = PATTERN_TDRPUWS.findall(xml_content)

        if nodes and tdrluws_list and tdrpuws_list:
            min_count = min(len(nodes), len(tdrluws_list), len(tdrpuws_list))
            for i in range(min_count):
                node = nodes[i].strip()
                try:
                    tdrluws = int(tdrluws_list[i].strip())
                    tdrpuws = int(tdrpuws_list[i].strip())
                    if node not in node_data:
                        node_data[node] = {'tdrluws_sum': 0, 'tdrpuws_sum': 0}
                    node_data[node]['tdrluws_sum'] += tdrluws
                    node_data[node]['tdrpuws_sum'] += tdrpuws
                except ValueError:
                    continue
    except Exception as e:
        print(f"解析 aggr-efficiency.xml 时出错: {e}")

    ratios = {}
    for node, data in node_data.items():
        if data['tdrpuws_sum'] > 0:
            ratios[node] = round(data['tdrluws_sum'] / data['tdrpuws_sum'], 2)
        else:
            ratios[node] = 1.0
    return ratios
