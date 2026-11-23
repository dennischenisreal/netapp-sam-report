"""
从 SYSCONFIG-A.txt 和 CLUSTER-INFO.xml 提取节点名、集群名和型号
"""
import os
import re

PATTERN_NODE_NAME = re.compile(r'System Serial Number:.*\((.+?)\)')
PATTERN_MODEL_NAME = re.compile(r'Model Name:\s*(\S+)')


def extract_node_name(sysconfig_path: str) -> str:
    """从 SYSCONFIG-A.txt 提取节点名"""
    if not sysconfig_path or not os.path.exists(sysconfig_path):
        return ''
    with open(sysconfig_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            match = PATTERN_NODE_NAME.search(line)
            if match:
                return match.group(1).strip()
    return ''


def extract_cluster_name(cluster_info_path: str) -> str:
    """从 CLUSTER-INFO.xml 提取集群名"""
    if not cluster_info_path or not os.path.exists(cluster_info_path):
        return ''
    with open(cluster_info_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
        match = re.search(r'<cluster-name>([^<]+)</cluster-name>', content)
        if match:
            return match.group(1).strip()
    return ''


def extract_model_name(sysconfig_path: str) -> str:
    """从 SYSCONFIG-A.txt 提取型号"""
    if not sysconfig_path or not os.path.exists(sysconfig_path):
        return ''
    with open(sysconfig_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            match = PATTERN_MODEL_NAME.search(line)
            if match:
                return match.group(1).strip()
    return ''
