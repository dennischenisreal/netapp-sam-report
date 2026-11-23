"""
Excel 报表导出模块
生成包含7个数据Sheet 的容量检查报告
"""
import os
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Border, Side, PatternFill, Font, Alignment
from openpyxl.utils import get_column_letter
from config import (
    ALL_SHEETS, SHEET_CAPACITY, SHEET_TOP_AGGREGATE, SHEET_TOP_INODE,
    SHEET_TOP_VOLUME, SHEET_FLEXGROUP, SHEET_VOLUME_LIST, SHEET_BUCKET,
    SHEET_BUCKET_VOLUMES, SHEET_EXPORT_POLICY,
    HEADER_FILL_COLOR, HEADER_FONT_COLOR, ZEBRA_COLOR_1, ZEBRA_COLOR_2,
    BORDER_COLOR, HIGHLIGHT_COLOR, BYTE_TO_TB, NUM_TO_YI,
    THRESHOLD_TOP_AGGREGATE_ROOT, THRESHOLD_TOP_AGGREGATE_OTHER,
    THRESHOLD_TOP_INODE_COUNT, THRESHOLD_TOP_INODE_PERCENT,
    THRESHOLD_TOP_VOLUME_SIZE_TB, THRESHOLD_TOP_VOLUME_PERCENT
)


class HealthCheckExporter:
    """NetApp 容量检查 Excel 导出器"""

    def __init__(self):
        self.wb = Workbook()
        self.thin_border = Border(
            left=Side('thin', color=BORDER_COLOR),
            right=Side('thin', color=BORDER_COLOR),
            top=Side('thin', color=BORDER_COLOR),
            bottom=Side('thin', color=BORDER_COLOR)
        )
        self.header_fill = PatternFill('solid', fgColor=HEADER_FILL_COLOR)
        self.header_font = Font(bold=True, color=HEADER_FONT_COLOR, size=11, name='Arial')
        self.zebra_1 = PatternFill('solid', fgColor=ZEBRA_COLOR_1)
        self.zebra_2 = PatternFill('solid', fgColor=ZEBRA_COLOR_2)
        self.yellow_fill = PatternFill('solid', fgColor=HIGHLIGHT_COLOR)
        self.note_fill = PatternFill('solid', fgColor='FFF9CC')
        self.center = Alignment(horizontal='center', vertical='center')
        self.left = Alignment(horizontal='left', vertical='center')
        self.right = Alignment(horizontal='right', vertical='center')

        # 删除默认Sheet，创建数据Sheet
        if self.wb.active:
            self.wb.remove(self.wb.active)

        # 创建所有 Sheet
        self.capacity_ws = self.wb.create_sheet(title=SHEET_CAPACITY['name'])
        self.top_aggr_ws = self.wb.create_sheet(title=SHEET_TOP_AGGREGATE['name'])
        self.top_inode_ws = self.wb.create_sheet(title=SHEET_TOP_INODE['name'])
        self.top_vol_ws = self.wb.create_sheet(title=SHEET_TOP_VOLUME['name'])
        self.flexgroup_ws = self.wb.create_sheet(title=SHEET_FLEXGROUP['name'])
        self.vol_list_ws = self.wb.create_sheet(title=SHEET_VOLUME_LIST['name'])
        self.bucket_ws = self.wb.create_sheet(title=SHEET_BUCKET['name'])
        self.export_policy_ws = self.wb.create_sheet(title=SHEET_EXPORT_POLICY['name'])

        # Sheet 映射
        self.ws_map = {
            SHEET_CAPACITY['name']: self.capacity_ws,
            SHEET_TOP_AGGREGATE['name']: self.top_aggr_ws,
            SHEET_TOP_INODE['name']: self.top_inode_ws,
            SHEET_TOP_VOLUME['name']: self.top_vol_ws,
            SHEET_FLEXGROUP['name']: self.flexgroup_ws,
            SHEET_VOLUME_LIST['name']: self.vol_list_ws,
            SHEET_BUCKET['name']: self.bucket_ws,
            SHEET_EXPORT_POLICY['name']: self.export_policy_ws,
        }

    def export(self, output_path: str, data: dict, input_dir: str):
        """
        导出完整报告

        Args:
            output_path: 输出文件路径
            data: 包含所有Sheet数据的字典
            input_dir: 输入目录名（用于说明显示）
        """
        # 写入各Sheet数据
        self._write_sheet(self.capacity_ws, SHEET_CAPACITY, data.get('capacity', []), None)
        self._write_sheet(self.top_aggr_ws, SHEET_TOP_AGGREGATE, data.get('top_aggregate', []), 
            f'筛选条件: root/aggr0使用率>{THRESHOLD_TOP_AGGREGATE_ROOT}% 或其他聚合使用率>{THRESHOLD_TOP_AGGREGATE_OTHER}%；容量>90%标黄')
        self._write_sheet(self.top_inode_ws, SHEET_TOP_INODE, data.get('top_inode', []),
            f'筛选条件: inode使用量>{THRESHOLD_TOP_INODE_COUNT/100000000}亿 且 inode使用率>{THRESHOLD_TOP_INODE_PERCENT}%')
        self._write_sheet(self.top_vol_ws, SHEET_TOP_VOLUME, data.get('top_volume', []),
            f'筛选条件: 容量使用>{THRESHOLD_TOP_VOLUME_SIZE_TB}TB 且 使用率>{THRESHOLD_TOP_VOLUME_PERCENT}%')
        self._write_sheet(self.flexgroup_ws, SHEET_FLEXGROUP, data.get('flexgroup', []), None)
        self._write_sheet(self.vol_list_ws, SHEET_VOLUME_LIST, data.get('volume_list', []), None)
        self._write_sheet(self.bucket_ws, SHEET_BUCKET, data.get('bucket', []), None)
        self._write_bucket_volume_section(self.bucket_ws, data.get('bucket_volumes', []))
        self._write_export_policy_section(self.export_policy_ws,
                                          data.get('export_rules', []),
                                          data.get('export_volumes', []))

        # 保存
        self.wb.save(output_path)

    def _write_bucket_volume_section(self, ws, rows_data):
        """在 Bucket sheet 的 bucket 表下方追加 fg_oss FlexGroup 卷清单(含子卷)

        Bucket(object store) 实际落在 fg_oss 前缀的 FlexGroup 卷上,
        Bucket 的 size 与 FlexGroup 卷 size 口径不同, 因此单独列一段便于对照。
        """
        columns = SHEET_BUCKET_VOLUMES['columns']
        highlights = SHEET_BUCKET_VOLUMES.get('highlights', {})
        ncols = max(len(SHEET_BUCKET['columns']), len(columns))

        # 整行合并的段落标题
        title_row = ws.max_row + 2
        ws.merge_cells(start_row=title_row, start_column=1, end_row=title_row, end_column=ncols)
        title_cell = ws.cell(row=title_row, column=1, value=SHEET_BUCKET_VOLUMES['title'])
        title_cell.fill = self.header_fill
        title_cell.font = self.header_font
        title_cell.alignment = self.center
        for col_idx in range(1, ncols + 1):
            ws.cell(row=title_row, column=col_idx).border = self.thin_border

        # 表头
        header_row = title_row + 1
        for col_idx, header in enumerate(columns, 1):
            cell = ws.cell(row=header_row, column=col_idx, value=header)
            cell.fill = self.header_fill
            cell.font = self.header_font
            cell.alignment = self.center
            cell.border = self.thin_border

        # 数据
        first_data_row = header_row + 1
        if not rows_data:
            ws.merge_cells(start_row=first_data_row, start_column=1, end_row=first_data_row, end_column=ncols)
            no_data_cell = ws.cell(row=first_data_row, column=1, value='未查询到 fg_oss FlexGroup 卷数据')
            no_data_cell.font = Font(italic=True, color='808080', size=10)
            no_data_cell.alignment = self.center
            no_data_cell.border = self.thin_border
            note_row = first_data_row + 1
        else:
            for row_idx, row_data in enumerate(rows_data):
                r = first_data_row + row_idx
                fill = self.zebra_1 if row_idx % 2 == 0 else self.zebra_2
                for col_idx, value in enumerate(row_data, 1):
                    cell = ws.cell(row=r, column=col_idx, value=value)
                    cell.fill = fill
                    cell.border = self.thin_border
                    if isinstance(value, (int, float)) or (isinstance(value, str) and (value.endswith('%') or value == 'N/A')):
                        cell.alignment = self.center
                    else:
                        cell.alignment = self.left

            # 条件标黄(与 Volume List 口径一致)
            for col_num, threshold in highlights.items():
                for row_idx in range(len(rows_data)):
                    cell = ws.cell(row=first_data_row + row_idx, column=col_num)
                    if cell.value:
                        try:
                            if float(str(cell.value).replace('%', '')) > threshold:
                                cell.fill = self.yellow_fill
                        except ValueError:
                            pass
            note_row = first_data_row + len(rows_data)

        # 说明
        ws.merge_cells(start_row=note_row, start_column=1, end_row=note_row, end_column=ncols)
        note_cell = ws.cell(row=note_row, column=1, value=SHEET_BUCKET_VOLUMES['note'])
        note_cell.font = Font(color='000000', size=10)
        note_cell.fill = self.note_fill
        note_cell.alignment = self.left
        note_cell.border = self.thin_border

        # 合并标题/说明单元格不应参与列宽计算, 否则首列会被撑得过宽
        self._auto_width(ws, skip_merged_anchors=True)

    def _write_export_policy_section(self, ws, rule_rows, volume_rows):
        """写入 export-policy Sheet: Export Policy 规则表 + Policy->Volume 对应表"""
        rule_cols = SHEET_EXPORT_POLICY['rule_columns']
        vol_cols = SHEET_EXPORT_POLICY['volume_columns']
        ncols = max(len(rule_cols), len(vol_cols))

        row = 1
        row = self._write_section_block(ws, row, SHEET_EXPORT_POLICY['rules_title'],
                                        rule_cols, rule_rows, '未查询到 Export Policy 规则数据')
        if rule_rows:
            # 同一 policy 的多条规则: 合并 Cluster/Vserver/Policy/Volume Count, 突出 policy 维度
            self._merge_policy_groups(ws, 3, 2 + len(rule_rows), (1, 2, 3, 13))
        row += 1
        row = self._write_section_block(ws, row, SHEET_EXPORT_POLICY['volumes_title'],
                                        vol_cols, volume_rows, '未查询到 Export Policy 对应的卷数据')

        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
        note_cell = ws.cell(row=row, column=1, value=SHEET_EXPORT_POLICY['note'])
        note_cell.font = Font(color='000000', size=10)
        note_cell.fill = self.note_fill
        note_cell.alignment = self.left
        note_cell.border = self.thin_border

        # 合并标题/说明单元格不参与列宽计算
        self._auto_width(ws, skip_merged_anchors=True)
        for col_letter, width in {'A': 16, 'B': 24, 'C': 30, 'D': 10, 'E': 12, 'F': 50, 'G': 12}.items():
            ws.column_dimensions[col_letter].width = width

    def _merge_policy_groups(self, ws, first_row, last_row, columns):
        """按 (Cluster Name, Vserver, Export Policy) 分组纵向合并指定列"""
        if last_row < first_row:
            return

        def group_key(r):
            return (ws.cell(row=r, column=1).value,
                    ws.cell(row=r, column=2).value,
                    ws.cell(row=r, column=3).value)

        start = first_row
        current = group_key(first_row)
        for r in range(first_row + 1, last_row + 2):
            key = group_key(r) if r <= last_row else None
            if key == current:
                continue
            end = r - 1
            if end > start:
                for col_idx in columns:
                    ws.merge_cells(start_row=start, start_column=col_idx,
                                   end_row=end, end_column=col_idx)
                    ws.cell(row=start, column=col_idx).alignment = self.center
            start = r
            current = key

    def _write_section_block(self, ws, start_row, title, columns, rows_data, empty_text):
        """写入一个带整行合并标题的数据块, 返回下一个可用行号"""
        ncols = max(len(SHEET_EXPORT_POLICY['rule_columns']), len(SHEET_EXPORT_POLICY['volume_columns']))

        ws.merge_cells(start_row=start_row, start_column=1, end_row=start_row, end_column=ncols)
        title_cell = ws.cell(row=start_row, column=1, value=title)
        title_cell.fill = self.header_fill
        title_cell.font = self.header_font
        title_cell.alignment = self.center
        for col_idx in range(1, ncols + 1):
            ws.cell(row=start_row, column=col_idx).border = self.thin_border

        header_row = start_row + 1
        for col_idx, header in enumerate(columns, 1):
            cell = ws.cell(row=header_row, column=col_idx, value=header)
            cell.fill = self.header_fill
            cell.font = self.header_font
            cell.alignment = self.center
            cell.border = self.thin_border

        row = header_row + 1
        if not rows_data:
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
            no_data_cell = ws.cell(row=row, column=1, value=empty_text)
            no_data_cell.font = Font(italic=True, color='808080', size=10)
            no_data_cell.alignment = self.center
            no_data_cell.border = self.thin_border
            return row + 1

        for idx, row_data in enumerate(rows_data):
            fill = self.zebra_1 if idx % 2 == 0 else self.zebra_2
            for col_idx, value in enumerate(row_data, 1):
                cell = ws.cell(row=row, column=col_idx, value=value)
                cell.fill = fill
                cell.border = self.thin_border
                if isinstance(value, (int, float)) or (isinstance(value, str) and (value.endswith('%') or value == 'N/A')):
                    cell.alignment = self.center
                else:
                    cell.alignment = self.left
            row += 1
        return row

    def _write_sheet(self, ws, sheet_config, rows_data, note_text):
        """写入单个数据Sheet"""
        columns = sheet_config['columns']
        highlights = sheet_config.get('highlights', {})

        # 写表头
        for col_idx, header in enumerate(columns, 1):
            cell = ws.cell(row=1, column=col_idx, value=header)
            cell.fill = self.header_fill
            cell.font = self.header_font
            cell.alignment = self.center
            cell.border = self.thin_border

        # 写数据
        for row_idx, row_data in enumerate(rows_data, 2):
            fill = self.zebra_1 if (row_idx - 2) % 2 == 0 else self.zebra_2
            for col_idx, value in enumerate(row_data, 1):
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.fill = fill
                cell.border = self.thin_border
                if isinstance(value, (int, float)) or (isinstance(value, str) and (value.endswith('%') or value == 'N/A')):
                    cell.alignment = self.center
                else:
                    cell.alignment = self.left

        # 如果没有数据，添加提示
        if not rows_data:
            no_data_row = 3
            ws.merge_cells(start_row=no_data_row, start_column=1, end_row=no_data_row, end_column=len(columns))
            no_data_cell = ws.cell(row=no_data_row, column=1, value='未查询到符合筛选条件的数据')
            no_data_cell.font = Font(italic=True, color='808080', size=10)
            no_data_cell.alignment = self.center
            no_data_cell.border = self.thin_border

        # 条件标黄
        if highlights and rows_data:
            for col_num, threshold in highlights.items():
                for r in range(2, len(rows_data) + 2):
                    cell = ws.cell(row=r, column=col_num)
                    if cell.value:
                        try:
                            val = float(str(cell.value).replace('%', ''))
                            if val > threshold:
                                cell.fill = self.yellow_fill
                        except:
                            pass

        # 添加筛选条件说明
        if note_text:
            note_row = len(rows_data) + 3
            note_col = 1
            ws.merge_cells(start_row=note_row, start_column=note_col, end_row=note_row, end_column=len(columns))
            note_cell = ws.cell(row=note_row, column=note_col, value=note_text)
            note_cell.font = Font(bold=True, color='000000', size=10)
            note_cell.fill = self.note_fill
            note_cell.alignment = self.left
            note_cell.border = self.thin_border

        # 列宽自适应
        self._auto_width(ws)

    def _auto_width(self, ws, skip_merged_anchors=False):
        """列宽自适应

        skip_merged_anchors=True 时忽略合并区域的左上角单元格,
        避免整行合并的长文案把该列撑得过宽。
        """
        merged_anchors = set()
        if skip_merged_anchors:
            for rng in ws.merged_cells.ranges:
                merged_anchors.add(str(rng).split(':')[0])
        for col_idx, col in enumerate(ws.columns, start=1):
            max_len = 0
            for cell in col:
                if merged_anchors and cell.coordinate in merged_anchors:
                    continue
                if cell.value:
                    s = str(cell.value)
                    width = sum(2 if '\u4e00' <= c <= '\u9fff' else 1 for c in s)
                    if width > max_len:
                        max_len = width
            adjusted = min(max(max_len + 2, 12), 50)
            ws.column_dimensions[get_column_letter(col_idx)].width = adjusted
