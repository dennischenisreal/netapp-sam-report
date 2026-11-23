"""
NetApp Capacity Check Skill - 配置文件
支持从 params.json 加载可调参数，未配置时使用默认值
"""
import json
import os

# ============ 加载 params.json ============
def _load_params():
    """加载参数配置文件"""
    params_path = os.environ.get('SAM_CAPACITY_PARAMS') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'params.json')
    if os.path.exists(params_path):
        with open(params_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    if os.environ.get('SAM_CAPACITY_PARAMS'):
        raise FileNotFoundError(params_path)
    return {}

_PARAMS = _load_params()
_THRESHOLDS = _PARAMS.get('thresholds', {})
_HIGHLIGHTS = _PARAMS.get('highlights', {})
_VOL_FILTER = _PARAMS.get('volume_filter', {})

def _get_threshold(key: str, default):
    """获取阈值参数"""
    entry = _THRESHOLDS.get(key, {})
    return entry.get('value', default)

def _get_highlight(key: str, default):
    """获取标黄阈值参数"""
    entry = _HIGHLIGHTS.get(key, {})
    return entry.get('value', default)


# ============ Sheet 列定义（含动态标黄阈值） ============
SHEET_CAPACITY = {
    'name': 'Controller Capacity',
    'columns': ['Cluster Name', 'Node', 'Model', 'Total (TB)', 'Used (TB)', 'Used Percent', '已分配vol size(TB)', '已用下沉空间(TB)', '超分比', '重删压缩比'],
    'highlights': {6: _get_highlight('capacity_used_percent', 85)},
}

SHEET_TOP_AGGREGATE = {
    'name': 'TOP aggr',
    'columns': ['Cluster Name', 'Node', 'Aggregate', 'Total(TB)', 'used(TB)', 'Available(TB)', 'capacity%'],
    'highlights': {7: _get_highlight('top_aggregate_capacity_percent', 90)},
}

SHEET_TOP_INODE = {
    'name': 'TOP inode',
    'columns': ['Cluster Name', 'Node', 'Volume', 'total inode(亿)', 'iused(亿)', '%iused'],
    'highlights': {},
}

SHEET_TOP_VOLUME = {
    'name': 'TOP vol',
    'columns': ['Cluster Name', 'Node', 'Volume Name', 'Size(TB)', 'Used (TB)', 'Capacity %'],
    'highlights': {},
}

SHEET_FLEXGROUP = {
    'name': 'FlexGroup',
    'columns': ['Cluster Name', 'Volume', 'Node', 'total inode(亿)', 'iused(亿)', '%iused', 'Size(TB)', 'Used (TB)', 'Capacity %'],
    'highlights': {
        6: _get_highlight('flexgroup_iused_percent', 60),
        9: _get_highlight('flexgroup_capacity_percent', 80),
    },
}

SHEET_VOLUME_LIST = {
    'name': 'Volume List',
    'columns': ['Cluster Name', 'Volume Name', 'SVM名字', 'Aggr Name', 'Volume Style', 'Volume Size (TB)', 'Available Size (TB)', 'Used Size (TB)', 'Used Percentage', 'Logical Used Percent', 'Total Files (亿)', 'Files Used (亿)', 'Space Reporting Logical', 'Space Enforcement Logical', 'Snapshot Policy', 'Volume Tiering Policy', 'Min Cooling Days', 'Performance Tier (TB)', 'Capacity Tier (TB)', '待下沉数据 (TB)'],
    'highlights': {9: _get_highlight('volume_list_used_percent', 80), 10: _get_highlight('volume_list_logical_used_percent', 95)},
}

SHEET_BUCKET = {
    'name': 'Bucket',
    'columns': ['Cluster Name', 'vserver', 'bucket', 'volume', 'size_of_bucket (TB)', 'object_count(亿)', 'logical_used_size (TB)', 'bucket used percent'],
    'highlights': {8: _get_highlight('bucket_used_percent', 80)},
}

# Bucket sheet 下方追加的 fg_oss FlexGroup 卷清单(含子卷)
# Bucket 实际落在 fg_oss 前缀的 FlexGroup 卷上, 两者 size 口径不同, 需分开查看
SHEET_BUCKET_VOLUMES = {
    'title': 'Bucket 对应的 fg_oss FlexGroup 卷 (含子卷)',
    'columns': ['Cluster Name', 'Volume Name', 'SVM名字', 'Aggr Name', 'Volume Size (TB)', 'Available Size (TB)', 'Used Size (TB)', 'Used Percentage', 'Logical Used Percent', 'Total Files (亿)', 'Files Used (亿)', 'Space Reporting Logical', 'Space Enforcement Logical', 'Snapshot Policy'],
    'highlights': {8: _get_highlight('volume_list_used_percent', 80), 9: _get_highlight('volume_list_logical_used_percent', 95)},
    'note': '注: fg_oss 卷为 Bucket(object store) 对应的 FlexGroup 卷, 名称带 __xxxx 的为其子卷(分布在各节点的聚合上)。Bucket 的 size 与 FlexGroup 卷 size 口径不同, 请分开查看。',
}

ALL_SHEETS = [SHEET_CAPACITY, SHEET_TOP_AGGREGATE, SHEET_TOP_INODE, SHEET_TOP_VOLUME, SHEET_FLEXGROUP, SHEET_VOLUME_LIST, SHEET_BUCKET]

# Export Policy sheet: 规则来自 export_rule_table.xml, Volume 对应关系来自 volume.xml 的 Export Policy 字段
SHEET_EXPORT_POLICY = {
    'name': 'export-policy',
    'rules_title': 'Export Policy 规则 (export_rule_table.xml)',
    'rule_columns': ['Cluster Name', 'Vserver', 'Export Policy', 'Rule Index', 'Protocol', 'Client Match',
                     'Read-Only Rule', 'Read-Write Rule', 'Anonymous UID', 'Superuser', 'Allow SUID', 'Allow Dev',
                     'Volume Count'],
    'volumes_title': 'Export Policy 对应的 Volume (volume.xml Export Policy)',
    'volume_columns': ['Cluster Name', 'Vserver', 'Export Policy', 'Size (TB)',
                       'Used %', 'Volume Name', 'Logical %'],
    'note': '注: 规则取自 export_rule_table.xml；Volume 对应关系取自 volume.xml 的 Export Policy 字段。系统卷(vol0、*_root、MDV*、fg_oss*)不计入 Volume 清单；未采集到 export_rule_table.xml 的集群只显示 Volume 对应关系。',
}

# ============ 过滤阈值（从 params.json 读取） ============
THRESHOLD_TOP_AGGREGATE_ROOT = _get_threshold('top_aggregate_root_percent', 95)
THRESHOLD_TOP_AGGREGATE_OTHER = _get_threshold('top_aggregate_other_percent', 85)
THRESHOLD_TOP_INODE_COUNT = _get_threshold('top_inode_count', 200000000)
THRESHOLD_TOP_INODE_PERCENT = _get_threshold('top_inode_percent', 70)
THRESHOLD_TOP_VOLUME_SIZE_TB = _get_threshold('top_volume_size_tb', 30)
THRESHOLD_TOP_VOLUME_PERCENT = _get_threshold('top_volume_percent', 80)

# ============ 卷过滤规则（从 params.json 读取） ============
# 注意: 'vol0' 只作为完整卷名精确匹配(系统根卷), vol00xx_* 等均为客户数据卷, 不能按前缀过滤
SKIP_VOLUME_PREFIXES = tuple(_VOL_FILTER.get('skip_prefixes', ['MDV', 'MDV_crs', 'fg_oss', 'fg_oss_']))
SKIP_VOLUME_SUFFIXES = tuple(_VOL_FILTER.get('skip_suffixes', ['_root']))
SKIP_VOLUME_NAMES = tuple(_VOL_FILTER.get('skip_names', ['vol0']))
VOLUME_STYLE_FLEXGROUP_CONSTITUENT = 'flexgroup-constituent'

# fg_oss 前缀卷 = Bucket(object store) 对应的 FlexGroup 卷(含其子卷)。
# 这类卷不进入 Volume List, 而是在 Bucket sheet 下方单独列出
OSS_VOLUME_PREFIX = 'fg_oss'

# ============ 样式配置 ============
HEADER_FILL_COLOR = '4472C4'
HEADER_FONT_COLOR = 'FFFFFF'
ZEBRA_COLOR_1 = 'FFFFFF'
ZEBRA_COLOR_2 = 'F7F9FC'
BORDER_COLOR = 'D9DEE7'
HIGHLIGHT_COLOR = 'FFFF00'

# ============ 单位转换 ============
BYTE_TO_TB = 1 / (1024 ** 4)
KB_TO_TB = 1 / (1024 ** 3)
NUM_TO_YI = 1 / 1e8
