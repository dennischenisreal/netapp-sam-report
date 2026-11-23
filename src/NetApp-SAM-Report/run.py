import os
import sys
import json
import copy
import re
import subprocess
from openpyxl import load_workbook, Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
from datetime import datetime
from pathlib import Path
from sam_runtime import RunContext, SKILLS_ROOT, LEGACY_ROOT, WORKSPACE, validate_final_workbook, describe_file, FIRMWARE_SOURCE_FILES
from customer_profiles import customer_name as configured_customer_name
from sam_settings import path_setting

def get_default_thresholds():
    return {
        'top_aggregate_root_percent': 95,
        'top_aggregate_other_percent': 85,
        'top_inode_count': 200000000,
        'top_inode_percent': 70,
        'top_volume_size_tb': 5,
        'top_volume_percent': 80,
        'capacity_used_percent': 85,
        'top_aggregate_capacity_percent': 90,
        'flexgroup_iused_percent': 60,
        'flexgroup_capacity_percent': 80,
        'volume_list_used_percent': 80,
        'bucket_used_percent': 80
    }

def select_thresholds():
    print("\n=== NetApp SAM Report - 阈值选择 ===")
    print("请为TOP页面选择筛选阈值（直接按回车使用默认值）：\n")
    
    thresholds = {}
    
    print("1. TOP aggregate - root/aggr0使用率筛选阈值(%)")
    print("   默认值: 95%")
    try:
        val = input("请输入(按回车使用默认值): ")
        if val.strip() == '':
            thresholds['top_aggregate_root_percent'] = 95
        else:
            thresholds['top_aggregate_root_percent'] = int(val)
    except:
        thresholds['top_aggregate_root_percent'] = 95
    
    print("\n2. TOP aggregate - 其他聚合使用率筛选阈值(%)")
    print("   默认值: 85%")
    try:
        val = input("请输入(按回车使用默认值): ")
        if val.strip() == '':
            thresholds['top_aggregate_other_percent'] = 85
        else:
            thresholds['top_aggregate_other_percent'] = int(val)
    except:
        thresholds['top_aggregate_other_percent'] = 85
    
    print("\n3. TOP inode - inode使用量筛选阈值(亿)")
    print("   默认值: 2亿")
    try:
        val = input("请输入(按回车使用默认值): ")
        if val.strip() == '':
            thresholds['top_inode_count'] = 200000000
        else:
            thresholds['top_inode_count'] = int(float(val) * 100000000)
    except:
        thresholds['top_inode_count'] = 200000000
    
    print("\n4. TOP inode - inode使用率筛选阈值(%)")
    print("   默认值: 70%")
    try:
        val = input("请输入(按回车使用默认值): ")
        if val.strip() == '':
            thresholds['top_inode_percent'] = 70
        else:
            thresholds['top_inode_percent'] = int(val)
    except:
        thresholds['top_inode_percent'] = 70
    
    print("\n5. TOP volume - 已用容量筛选阈值(TB)")
    print("   默认值: 5 TB")
    try:
        val = input("请输入(按回车使用默认值): ")
        if val.strip() == '':
            thresholds['top_volume_size_tb'] = 5
        else:
            thresholds['top_volume_size_tb'] = float(val)
    except:
        thresholds['top_volume_size_tb'] = 5
    
    print("\n6. TOP volume - 使用率筛选阈值(%)")
    print("   默认值: 80%")
    try:
        val = input("请输入(按回车使用默认值): ")
        if val.strip() == '':
            thresholds['top_volume_percent'] = 80
        else:
            thresholds['top_volume_percent'] = int(val)
    except:
        thresholds['top_volume_percent'] = 80
    
    print("\n=== 您选择的阈值配置 ===")
    print(f"  root/aggr0使用率: {thresholds['top_aggregate_root_percent']}%")
    print(f"  其他聚合使用率: {thresholds['top_aggregate_other_percent']}%")
    print(f"  inode使用量: {thresholds['top_inode_count']/100000000:.1f}亿")
    print(f"  inode使用率: {thresholds['top_inode_percent']}%")
    print(f"  卷容量: {thresholds['top_volume_size_tb']} TB")
    print(f"  卷使用率: {thresholds['top_volume_percent']}%")
    

    print()
    print('--- 标黄阈值（超过此值在表格中标记黄色）---')
    print()

    print('7. Capacity表 - Used Percent 标黄阈值(%)')
    print('   默认值: 85%')
    try:
        val = input('请输入(按回车使用默认值): ')
        if val.strip() == '':
            thresholds['capacity_used_percent'] = 85
        else:
            thresholds['capacity_used_percent'] = int(val)
    except:
        thresholds['capacity_used_percent'] = 85

    print()
    print('8. TOP aggregate表 - capacity% 标黄阈值(%)')
    print('   默认值: 90%')
    try:
        val = input('请输入(按回车使用默认值): ')
        if val.strip() == '':
            thresholds['top_aggregate_capacity_percent'] = 90
        else:
            thresholds['top_aggregate_capacity_percent'] = int(val)
    except:
        thresholds['top_aggregate_capacity_percent'] = 90

    print()
    print('9. FlexGroup表 - %iused 标黄阈值(%)')
    print('   默认值: 60%')
    try:
        val = input('请输入(按回车使用默认值): ')
        if val.strip() == '':
            thresholds['flexgroup_iused_percent'] = 60
        else:
            thresholds['flexgroup_iused_percent'] = int(val)
    except:
        thresholds['flexgroup_iused_percent'] = 60

    print()
    print('10. FlexGroup表 - Capacity% 标黄阈值(%)')
    print('   默认值: 80%')
    try:
        val = input('请输入(按回车使用默认值): ')
        if val.strip() == '':
            thresholds['flexgroup_capacity_percent'] = 80
        else:
            thresholds['flexgroup_capacity_percent'] = int(val)
    except:
        thresholds['flexgroup_capacity_percent'] = 80

    print()
    print('11. Volume List表 - Used Percentage 标黄阈值(%)')
    print('   默认值: 80%')
    try:
        val = input('请输入(按回车使用默认值): ')
        if val.strip() == '':
            thresholds['volume_list_used_percent'] = 80
        else:
            thresholds['volume_list_used_percent'] = int(val)
    except:
        thresholds['volume_list_used_percent'] = 80

    print()
    print('12. Bucket表 - bucket used percent 标黄阈值(%)')
    print('   默认值: 80%')
    try:
        val = input('请输入(按回车使用默认值): ')
        if val.strip() == '':
            thresholds['bucket_used_percent'] = 80
        else:
            thresholds['bucket_used_percent'] = int(val)
    except:
        thresholds['bucket_used_percent'] = 80

    print()
    print('=== 您选择的阈值配置 ===')
    print(f"  root/aggr0使用率: {thresholds['top_aggregate_root_percent']}%")
    print(f"  其他聚合使用率: {thresholds['top_aggregate_other_percent']}%")
    print(f"  inode使用量: {thresholds['top_inode_count']/100000000:.1f}亿")
    print(f"  inode使用率: {thresholds['top_inode_percent']}%")
    print(f"  卷容量: {thresholds['top_volume_size_tb']} TB")
    print(f"  卷使用率: {thresholds['top_volume_percent']}%")
    print(f"  Capacity标黄: {thresholds['capacity_used_percent']}%")
    print(f"  TOP aggr标黄: {thresholds['top_aggregate_capacity_percent']}%")
    print(f"  FlexGroup iused标黄: {thresholds['flexgroup_iused_percent']}%")
    print(f"  FlexGroup Capacity标黄: {thresholds['flexgroup_capacity_percent']}%")
    print(f"  Volume List标黄: {thresholds['volume_list_used_percent']}%")
    print(f"  Bucket标黄: {thresholds['bucket_used_percent']}%")
    return thresholds

def update_capacity_params(thresholds, output_path=None):
    params_path = str(path_setting('skills_root', Path.home() / '.codex' / 'skills') / '2-netapp-capacity-aggr-vol-fg' / 'params.json')
    if os.path.exists(params_path):
        with open(params_path, 'r', encoding='utf-8') as f:
            params = json.load(f)
        
        params['thresholds']['top_aggregate_root_percent']['value'] = thresholds['top_aggregate_root_percent']
        params['thresholds']['top_aggregate_other_percent']['value'] = thresholds['top_aggregate_other_percent']
        params['thresholds']['top_inode_count']['value'] = thresholds['top_inode_count']
        params['thresholds']['top_inode_percent']['value'] = thresholds['top_inode_percent']
        params['thresholds']['top_volume_size_tb']['value'] = thresholds['top_volume_size_tb']
        params['thresholds']['top_volume_percent']['value'] = thresholds['top_volume_percent']
        params['highlights']['capacity_used_percent']['value'] = thresholds['capacity_used_percent']
        params['highlights']['top_aggregate_capacity_percent']['value'] = thresholds['top_aggregate_capacity_percent']
        params['highlights']['flexgroup_iused_percent']['value'] = thresholds['flexgroup_iused_percent']
        params['highlights']['flexgroup_capacity_percent']['value'] = thresholds['flexgroup_capacity_percent']
        params['highlights']['volume_list_used_percent']['value'] = thresholds['volume_list_used_percent']
        params['highlights']['bucket_used_percent']['value'] = thresholds['bucket_used_percent']
        
        target_path = output_path or params_path
        with open(target_path, 'w', encoding='utf-8') as f:
            json.dump(params, f, indent=4, ensure_ascii=False)
        print(f"\nCapacity Check参数: {target_path}")
        return target_path
    raise FileNotFoundError(params_path)

def create_cover_sheet(ws, dir_name):
    thin_border = Border(
        left=Side('thin', color='D9DEE7'),
        right=Side('thin', color='D9DEE7'),
        top=Side('thin', color='D9DEE7'),
        bottom=Side('thin', color='D9DEE7')
    )

    header_fill = PatternFill('solid', fgColor='1F4E78')
    subheader_fill = PatternFill('solid', fgColor='4472C4')
    light_fill = PatternFill('solid', fgColor='D9E2F3')

    ws.column_dimensions['A'].width = 25
    ws.column_dimensions['B'].width = 40
    ws.column_dimensions['C'].width = 20
    ws.column_dimensions['D'].width = 35

    for row in range(1, 40):
        ws.row_dimensions[row].height = 25

    ws.merge_cells('A1:D1')
    ws['A1'] = 'NetApp SAM Report - ' + dir_name
    ws['A1'].font = Font(name='微软雅黑', size=28, bold=True, color='FFFFFF')
    ws['A1'].fill = header_fill
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 50

    ws.merge_cells('A2:D2')
    ws['A2'] = 'NetApp Storage Analysis Management Report - ' + dir_name
    ws['A2'].font = Font(name='Arial', size=14, italic=True, color='FFFFFF')
    ws['A2'].fill = subheader_fill
    ws['A2'].alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[2].height = 30

    ws.merge_cells('A4:D4')
    ws['A4'] = 'Report Information'
    ws['A4'].font = Font(name='微软雅黑', size=14, bold=True, color='1F4E78')
    ws['A4'].fill = light_fill
    ws['A4'].alignment = Alignment(horizontal='left', vertical='center')

    info_data = [
        ('Report Date:', datetime.now().strftime('%Y-%m-%d %H:%M'), 'Generated Time:', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
        ('Analysis Directory:', dir_name, 'Data Source:', 'NetApp ASUP Logs'),
        ('Contents:', 'Controller, BestPractice, Case, Capacity, Firmware, Network', '', ''),
    ]

    for i, (col1, val1, col2, val2) in enumerate(info_data, start=5):
        ws['A' + str(i)] = col1
        ws['B' + str(i)] = val1
        ws['C' + str(i)] = col2
        ws['D' + str(i)] = val2
        ws['A' + str(i)].font = Font(name='微软雅黑', size=12, bold=True, color='000000')
        ws['C' + str(i)].font = Font(name='微软雅黑', size=12, bold=True, color='000000')
        ws['A' + str(i)].fill = light_fill
        ws['C' + str(i)].fill = light_fill
        ws['B' + str(i)].fill = light_fill
        ws['D' + str(i)].fill = light_fill
        
        cell_a = ws['A' + str(i)]
        cell_b = ws['B' + str(i)]
        cell_c = ws['C' + str(i)]
        cell_d = ws['D' + str(i)]
        for cell in [cell_a, cell_b, cell_c, cell_d]:
            cell.alignment = Alignment(horizontal='left', vertical='center')
            cell.border = thin_border

    ws.merge_cells('A9:D9')
    ws['A9'] = 'Report Contents'
    ws['A9'].font = Font(name='微软雅黑', size=14, bold=True, color='1F4E78')
    ws['A9'].fill = light_fill
    ws['A9'].alignment = Alignment(horizontal='left', vertical='center')

    contents = [
        ('No.', 'Category', 'Description', 'Sheet'),
        ('1', 'Cover', 'Report cover', 'Cover'),
        ('2', 'Index', 'All analysis index', 'Index'),
        ('3', 'Controller Report', 'OS version, EOS date, maintenance', 'Controller'),
        ('4', '\u6700\u4f73\u5b9e\u8df5\u89c4\u8303\u68c0\u67e5', 'Best practice check (14 items)', 'BestPractice'),
        ('5', 'Case Analysis', 'Device case matching, fault statistics', 'Case'),
        ('6', 'Capacity Check', 'Capacity, aggregate, volume, FlexGroup, Bucket', 'Capacity'),
        ('7', 'Firmware Analysis', 'Disk, shelf, switch, BMC/SP', 'Firmware'),
        ('8', 'Network Configuration', 'Network interface, port, IFGRP, EMS', 'Network'),
    ]

    for row_idx, row_data in enumerate(contents, start=10):
        for col_idx, value in enumerate(row_data, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.border = thin_border
            if col_idx in [1, 2]:
                cell.alignment = Alignment(horizontal='center', vertical='center')
            else:
                cell.alignment = Alignment(horizontal='left', vertical='center')
            
            if row_idx == 10:
                cell.font = Font(name='微软雅黑', size=12, bold=True, color='FFFFFF')
                cell.fill = subheader_fill
            else:
                cell.font = Font(name='微软雅黑', size=12, color='000000')
                if row_idx % 2 == 0:
                    cell.fill = light_fill

    # 注意：第10行是表头，第11~18行是contents的8条目录（到此为止），
    # 所以Notes必须从第19行开始写，否则会覆盖掉第18行的第8条目录
    # （历史上 A18 被 'Notes' 覆盖，导致 Network Configuration 一行消失）
    ws.merge_cells('A19:D19')
    ws['A19'] = 'Notes'
    ws['A19'].font = Font(name='微软雅黑', size=14, bold=True, color='1F4E78')
    ws['A19'].fill = PatternFill('solid', fgColor='FFF9CC')
    ws['A19'].alignment = Alignment(horizontal='left', vertical='center')

    ws.merge_cells('A20:D22')
    ws['A20'] = 'This report is automatically generated from NetApp ASUP logs. ' + \
                 'For any questions, please contact the technical support team.'
    ws['A20'].font = Font(name='微软雅黑', size=10, color='000000')
    ws['A20'].alignment = Alignment(horizontal='left', vertical='top', wrap_text=True)
    ws['A20'].fill = PatternFill('solid', fgColor='FFF9CC')
    ws.row_dimensions[20].height = 60

    ws.merge_cells('A24:D26')
    ws['A24'] = 'Generated by TRAE AI - NetApp ASUP Analysis System'
    ws['A24'].font = Font(name='Arial', size=10, italic=True, color='7F7F7F')
    ws['A24'].alignment = Alignment(horizontal='center', vertical='center')

def create_index_sheet(wb, dir_name):
    if 'Index' in wb.sheetnames:
        del wb['Index']
    ws = wb.create_sheet('Index', 1)

    thin_border = Border(
        left=Side('thin', color='D9DEE7'),
        right=Side('thin', color='D9DEE7'),
        top=Side('thin', color='D9DEE7'),
        bottom=Side('thin', color='D9DEE7')
    )

    header_fill = PatternFill('solid', fgColor='1F4E78')
    subheader_fill = PatternFill('solid', fgColor='4472C4')
    light_fill = PatternFill('solid', fgColor='D9E2F3')

    ws.column_dimensions['A'].width = 8
    ws.column_dimensions['B'].width = 25
    ws.column_dimensions['C'].width = 50
    ws.column_dimensions['D'].width = 40

    ws.merge_cells('A1:D1')
    ws['A1'] = 'NetApp SAM Report Index - ' + dir_name
    ws['A1'].font = Font(name='微软雅黑', size=16, bold=True, color='FFFFFF')
    ws['A1'].fill = header_fill
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 35

    headers = ['No.', 'Category', 'Description', 'Source']
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=2, column=col, value=header)
        cell.font = Font(name='微软雅黑', size=12, bold=True, color='FFFFFF')
        cell.fill = subheader_fill
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = thin_border
    ws.row_dimensions[2].height = 25

    index_data = [
        (1, 'Controller Report', 'OS version, EOS date, maintenance', 'Controller', "'Controller Report'!A1"),
        (2, '最佳实践规范检查', 'Best practice check (14 items)', 'BestPractice', "'最佳实践规范检查'!A1"),
        (3, 'Case Analysis', 'Device case matching, fault statistics', 'Case', "'硬件case'!A1"),
        (4, 'Capacity Check', 'Capacity, aggregate, volume, FlexGroup, Bucket', 'Capacity', "'Controller Capacity'!A1"),
        (5, 'EMS Alerts', 'EMS alerts summary', 'Network', "'EMS告警'!A1"),
        (6, 'Firmware Analysis', 'Disk, shelf, switch, BMC/SP', 'Firmware', "'Disk Firmware'!A1"),
        (7, 'Network Configuration', 'Network interface, port, IFGRP, EMS', 'Network', "'Network Interfaces'!A1"),
    ]

    for row_idx, (num, category, desc, source, link) in enumerate(index_data, start=3):
        ws.cell(row=row_idx, column=1, value=num).border = thin_border
        
        cat_cell = ws.cell(row=row_idx, column=2, value=category)
        cat_cell.border = thin_border
        # Create proper internal hyperlink with location
        from openpyxl.worksheet.hyperlink import Hyperlink
        cat_cell.hyperlink = Hyperlink(ref=cat_cell.coordinate, location=link)
        cat_cell.font = Font(name='微软雅黑', size=10, color='0000FF', underline='single')
        
        ws.cell(row=row_idx, column=3, value=desc).border = thin_border
        ws.cell(row=row_idx, column=4, value=source).border = thin_border

        for col in range(1, 5):
            cell = ws.cell(row=row_idx, column=col)
            if col == 2:
                cell.font = Font(name='微软雅黑', size=10, color='0000FF', underline='single')
            else:
                cell.font = Font(name='微软雅黑', size=10, color='000000')
            if col in [1, 2, 4]:
                cell.alignment = Alignment(horizontal='center', vertical='center')
            else:
                cell.alignment = Alignment(horizontal='left', vertical='center')
            
            if row_idx % 2 == 0:
                cell.fill = light_fill

        ws.row_dimensions[row_idx].height = 20

    return ws

def update_firmware_navigation(workbook, skipped):
    if not skipped:
        return
    available = [sheet for sheet in FIRMWARE_SOURCE_FILES if sheet not in skipped]
    omitted = ', '.join(f'{sheet} ({FIRMWARE_SOURCE_FILES[sheet]})' for sheet in skipped)
    description = 'Available: ' + (', '.join(available) or 'None') + '; skipped: source logs missing'
    for title, coordinate, height in (('Cover', 'C17', 48), ('Index', 'C8', 48)):
        cell = workbook[title][coordinate]
        cell.value = description
        cell.alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
        workbook[title].row_dimensions[cell.row].height = height
    note = workbook['Cover']['A20']
    note.value += '\nSkipped firmware sheets (source logs missing, not a passed check): ' + omitted
    workbook['Cover'].row_dimensions[20].height = 95
    link = workbook['Index']['B8']
    if available:
        from openpyxl.worksheet.hyperlink import Hyperlink
        link.hyperlink = Hyperlink(ref=link.coordinate, location="'" + available[0] + "'!A1")
    else:
        link.hyperlink = None
        link.font = Font(name='微软雅黑', size=10, color='000000')


def copy_sheet_with_charts(source_ws, target_ws):
    for row in source_ws.iter_rows():
        for cell in row:
            new_cell = target_ws.cell(row=cell.row, column=cell.column, value=cell.value)
            if cell.has_style:
                try:
                    new_cell.font = copy.copy(cell.font)
                    new_cell.fill = copy.copy(cell.fill)
                    new_cell.border = copy.copy(cell.border)
                    new_cell.alignment = copy.copy(cell.alignment)
                    new_cell.number_format = cell.number_format
                except:
                    pass

    for col_idx in range(1, source_ws.max_column + 1):
        col_letter = get_column_letter(col_idx)
        if col_letter in source_ws.column_dimensions:
            target_ws.column_dimensions[col_letter].width = source_ws.column_dimensions[col_letter].width

    for row_idx in range(1, source_ws.max_row + 1):
        if row_idx in source_ws.row_dimensions:
            target_ws.row_dimensions[row_idx].height = source_ws.row_dimensions[row_idx].height

    for merged_range in source_ws.merged_cells.ranges:
        target_ws.merge_cells(str(merged_range))

    if hasattr(source_ws, '_charts'):
        for chart in source_ws._charts:
            new_chart = copy.copy(chart)
            target_ws.add_chart(new_chart)

def copy_workbook_with_charts(source_path, target_wb):
    if not os.path.exists(source_path):
        return
    source_wb = load_workbook(source_path)

    for sheet_name in source_wb.sheetnames:
        if sheet_name == 'Cover':
            continue
        source_ws = source_wb[sheet_name]
        target_ws = target_wb.create_sheet(title=sheet_name)
        copy_sheet_with_charts(source_ws, target_ws)

    source_wb.close()

def copy_specific_sheet(source_wb, target_wb, sheet_name):
    if sheet_name in source_wb.sheetnames:
        source_ws = source_wb[sheet_name]
        target_ws = target_wb.create_sheet(title=sheet_name)
        copy_sheet_with_charts(source_ws, target_ws)
        return True
    return False

def run_skills(input_dir, dir_name, context=None):
    customer_name, month_part = get_customer_info(input_dir)
    context = context or RunContext(input_dir, customer_name, month_part)
    print("\n=== 运行六个子技能（本次任务独立目录） ===", flush=True)
    jobs = [
        ('Capacity', SKILLS_ROOT / '2-netapp-capacity-aggr-vol-fg' / 'run.py',
         [input_dir, '{output}'], customer_name + '_Capacity_Check.xlsx'),
        ('Controller', LEGACY_ROOT / 'ASUP Analysis' / 'parse_asup_logs_final.py',
         [input_dir], dir_name + '_Controller_Report.xlsx'),
        ('Firmware', SKILLS_ROOT / '4-netapp-firmware-shelf-io-sp' / 'main.py',
         [input_dir], customer_name + ' Firmware Report.xlsx'),
        ('Case', LEGACY_ROOT / 'Case Analysis' / 'parse_c1_case.py',
         [input_dir], dir_name + '_' + str(context.manifest['case_year']) + '_Case_Report.xlsx'),
        ('Network', SKILLS_ROOT / '5-netapp-network-ems' / 'parse_network_final.py',
         [input_dir], dir_name + ' Network Interface Report.xlsx'),
        ('BestPractice', SKILLS_ROOT / '6-best-practice-check' / 'run.py',
         [input_dir, '{folder}'], dir_name + '_最佳实践检查.xlsx'),
    ]
    for module, script, arguments, expected_name in jobs:
        context.execute(module, script, arguments, expected_name)
    context.require_complete()
    return context


def get_customer_info(input_dir):
    """根据输入路径识别客户名和月份"""
    input_dir = input_dir.replace("\\", "/")
    parts = input_dir.split("/")
    customer_name = configured_customer_name(input_dir, 'sam')
    month_part = None
    for part in parts:
        if "月" in part:
            month_part = part
            break
    if not month_part:
        month_part = str(datetime.now().month) + "月"
    return customer_name, month_part

HEADER_BG_LIGHT = "BDD7EE"
HEADER_FONT_DARK = "1F4E78"


def is_dark_header_fill(cell):
    """判断单元格底色是否为"深色表头底色"（蓝色系且偏深）。

    背景：各子Skill/Controller脚本写表头时用的底色并不统一
    （1E3A5F 深藏蓝 / 1F4E78 藏蓝 / 4472C4 标准蓝 / 9DC3E6 中蓝），
    合并后再统一成浅蓝，保证表头文字任何时候都清晰可读。
    只识别蓝色系，因此红色/黄色/绿色等状态色（FF0000/FFE699/C6EFCE）不会被改动。
    """
    fill = cell.fill
    if fill is None or fill.fill_type != "solid":
        return False
    rgb = fill.start_color.rgb
    if not isinstance(rgb, str):
        return False
    rgb = rgb[-6:].upper()
    if len(rgb) != 6:
        return False
    if rgb == HEADER_BG_LIGHT:
        # 已经统一过的表头底色，重复执行时仍需按表头处理（保证幂等）
        return True
    try:
        r, g, b = int(rgb[0:2], 16), int(rgb[2:4], 16), int(rgb[4:6], 16)
    except ValueError:
        return False
    if b <= max(r, g) + 10:
        return False
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    return luminance < 0.75


BEST_PRACTICE_SHEET = "\u6700\u4f73\u5b9e\u8df5\u89c4\u8303\u68c0\u67e5"


def sync_best_practice_count(wb):
    """按最佳实践Sheet里实际的检查项数量，刷新Cover/Index的"(N items)"文案。

    检查项数量会随版本增加（12 -> 14 -> ...），写死容易忘记同步，
    这里改为从"最佳实践规范检查"Sheet的 "Check N: xxx" 标题行自动数出来，
    以后新增Check不需要再手工改文案。
    """
    if BEST_PRACTICE_SHEET not in wb.sheetnames:
        return
    check_numbers = set()
    for row in wb[BEST_PRACTICE_SHEET].iter_rows(min_col=1, max_col=1):
        value = row[0].value
        if isinstance(value, str):
            matched = re.match(r"^\s*Check\s+(\d+)\s*:", value)
            if matched:
                check_numbers.add(int(matched.group(1)))
    if not check_numbers:
        return
    pattern = re.compile(r"Best practice check \(\d+ items\)")
    replacement = "Best practice check (%d items)" % len(check_numbers)
    for sheet_name in ("Cover", "Index"):
        if sheet_name not in wb.sheetnames:
            continue
        for row in wb[sheet_name].iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and pattern.search(cell.value):
                    cell.value = pattern.sub(replacement, cell.value)
    print("  Best practice items synced: " + str(len(check_numbers)))


def normalize_sheet_styles(wb):
    """全表样式统一（所有Sheet的最后一道处理，保证风格一致）。

    1. 表头/标题底色统一为浅蓝 BDD7EE，表头字体为深蓝加粗，确保看得清字
       （此前 1E3A5F 等深底会被统一成黑色字体，导致"字看不见"）
    2. 其余单元格字体统一微软雅黑，补 D9DEE7 细边框并居中
    3. 冻结首行

    Cover和Index只做表头配色统一，不改动其既有版式。
    """
    header_fill = PatternFill("solid", fgColor=HEADER_BG_LIGHT)
    normal_font = Font(name="微软雅黑", size=10)
    center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin_border = Border(left=Side("thin", color="D9DEE7"), right=Side("thin", color="D9DEE7"), top=Side("thin", color="D9DEE7"), bottom=Side("thin", color="D9DEE7"))
    for ws in wb.worksheets:
        keep_layout = ws.title in ("Cover", "Index")
        for row in ws.iter_rows():
            for cell in row:
                is_header = is_dark_header_fill(cell)
                if cell.value is None and not is_header:
                    continue
                if is_header:
                    # 保留原有字号(标题更大)，只统一底色和字色
                    size = cell.font.size or 12
                    italic = bool(cell.font.italic)
                    cell.fill = header_fill
                    cell.font = Font(name="微软雅黑", size=size, bold=True,
                                     italic=italic, color=HEADER_FONT_DARK)
                elif not keep_layout:
                    cell.font = normal_font
                if not keep_layout:
                    cell.alignment = center_align
                    cell.border = thin_border
        if not keep_layout:
            ws.freeze_panes = "A1"

def generate_sam_report(input_dir, dir_name, context=None, output_root=None):
    if context is None:
        raise RuntimeError('需要本次六个子技能的运行记录，禁止直接合并共享目录中的旧文件')
    context.require_complete()
    skipped_firmware = context.firmware_skipped_sheets()
    print("\n" + "=" * 60)
    print("Generating SAM Report for " + dir_name)
    print("=" * 60)
    customer_name, month_part = get_customer_info(input_dir)
    if Path(input_dir).resolve() != context.input_dir or customer_name != context.customer_name:
        raise ValueError('输入日志或客户与本次运行记录不一致，禁止跨任务合并')
    context.require_coverage()
    print(f"  \u5ba2\u6237: {customer_name}")
    print(f"  \u6708\u4efd: {month_part}")
    output_dir = str(output_root or path_setting('reports_root', WORKSPACE / 'Report'))
    customer_dir = os.path.join(output_dir, customer_name)
    if not os.path.exists(customer_dir):
        os.makedirs(customer_dir)
        print(f"  \u5df2\u521b\u5efa\u5ba2\u6237\u76ee\u5f55: {customer_dir}")
    report_filename = f"SAM_Report_{customer_name}_{month_part}.xlsx"
    final_report_path = os.path.join(customer_dir, report_filename)
    wb = Workbook()
    if wb.active:
        wb.remove(wb.active)
    cover_ws = wb.create_sheet("Cover")
    create_cover_sheet(cover_ws, dir_name)
    create_index_sheet(wb, dir_name)
    source_files = {module: os.path.dirname(path) for module, path in context.paths.items()}
    mappings = {module: os.path.basename(path) for module, path in context.paths.items()}
    controller_path = os.path.join(source_files["Controller"], mappings["Controller"])
    if os.path.exists(controller_path):
        print("  Copying Controller: " + mappings["Controller"])
        try:
            copy_workbook_with_charts(controller_path, wb)
            print("    Success")
        except Exception as e:
            raise RuntimeError('Controller 合并失败') from e
    bp_path = os.path.join(source_files["BestPractice"], mappings["BestPractice"])
    if os.path.exists(bp_path):
        print("  Copying BestPractice: " + mappings["BestPractice"])
        try:
            bp_wb = load_workbook(bp_path)
            for sheet_name in bp_wb.sheetnames:
                target_name = "\u6700\u4f73\u5b9e\u8df5\u89c4\u8303\u68c0\u67e5"
                source_ws = bp_wb[sheet_name]
                target_ws = wb.create_sheet(title=target_name)
                copy_sheet_with_charts(source_ws, target_ws)
            bp_wb.close()
            print("    Success")
        except Exception as e:
            raise RuntimeError('BestPractice 合并失败') from e
    case_path = os.path.join(source_files["Case"], mappings["Case"])
    if os.path.exists(case_path):
        print("  Copying Case: " + mappings["Case"])
        try:
            copy_workbook_with_charts(case_path, wb)
            print("    Success")
        except Exception as e:
            raise RuntimeError('Case 合并失败') from e
    capacity_path = os.path.join(source_files["Capacity"], mappings["Capacity"])
    if os.path.exists(capacity_path):
        print("  Copying Capacity: " + os.path.basename(capacity_path))
        try:
            copy_workbook_with_charts(capacity_path, wb)
            print("    Success")
        except Exception as e:
            raise RuntimeError('Capacity 合并失败') from e
    network_path = os.path.join(source_files["Network"], mappings["Network"])
    if os.path.exists(network_path):
        network_wb = load_workbook(network_path)
        print("  Copying EMS\u544a\u8b66 from Network")
        copy_specific_sheet(network_wb, wb, "EMS\u544a\u8b66")
        network_wb.close()
        print("    Success")
    firmware_path = os.path.join(source_files["Firmware"], mappings["Firmware"])
    if os.path.exists(firmware_path) and len(skipped_firmware) != len(FIRMWARE_SOURCE_FILES):
        print("  Copying Firmware: " + os.path.basename(firmware_path))
        try:
            copy_workbook_with_charts(firmware_path, wb)
            print("    Success")
        except Exception as e:
            raise RuntimeError('Firmware 合并失败') from e
    if os.path.exists(network_path):
        network_wb = load_workbook(network_path)
        for sheet_name in network_wb.sheetnames:
            if sheet_name != "EMS\u544a\u8b66":
                print("  Copying Network: " + sheet_name)
                copy_specific_sheet(network_wb, wb, sheet_name)
        network_wb.close()
        print("    Success")
    # export-policy 需要固定排在最后
    if "export-policy" in wb.sheetnames:
        export_idx = wb.sheetnames.index("export-policy")
        wb.move_sheet(wb["export-policy"], offset=len(wb.sheetnames) - 1 - export_idx)
        print("  Moved export-policy to the end")
    sync_best_practice_count(wb)
    update_firmware_navigation(wb, skipped_firmware)
    normalize_sheet_styles(wb)
    validate_final_workbook(wb, skipped_firmware=skipped_firmware)
    base, ext = os.path.splitext(final_report_path)
    timestamp = datetime.now().strftime("_%Y%m%d_%H%M%S_%f")
    final_report_path = base + timestamp + ext
    try:
        wb.save(final_report_path)
        print("\nReport saved: " + final_report_path)
        print("Contains " + str(len(wb.sheetnames)) + " sheets")
    except Exception as e:
        raise RuntimeError('SAM report 保存失败: ' + final_report_path) from e
    return final_report_path

def process_directory(input_dir, output_path=None, use_defaults=False, runs_root=None):
    input_dir = os.path.normpath(os.path.abspath(input_dir))
    if not os.path.isdir(input_dir):
        print(f"\u9519\u8bef\uff1a{input_dir} \u4e0d\u662f\u6709\u6548\u7684\u76ee\u5f55")
        raise NotADirectoryError(input_dir)
    dir_name = os.path.basename(input_dir)
    if use_defaults:
        thresholds = get_default_thresholds()
        print("\n=== \u4f7f\u7528\u9ed8\u8ba4\u9608\u503c\u914d\u7f6e ===")
        print(f"  root/aggr0\u4f7f\u7528\u7387: {thresholds['top_aggregate_root_percent']}%")
        print(f"  \u5176\u4ed6\u805a\u5408\u4f7f\u7528\u7387: {thresholds['top_aggregate_other_percent']}%")
        print(f"  inode\u4f7f\u7528\u91cf: {thresholds['top_inode_count']/100000000:.1f}\u4ebf")
        print(f"  inode\u4f7f\u7528\u7387: {thresholds['top_inode_percent']}%")
        print(f"  \u5377\u5bb9\u91cf: {thresholds['top_volume_size_tb']} TB")
        print(f"  \u5377\u4f7f\u7528\u7387: {thresholds['top_volume_percent']}%")
    else:
        thresholds = select_thresholds()
    customer_name, month_part = get_customer_info(input_dir)
    context = RunContext(input_dir, customer_name, month_part, runs_root=runs_root)
    print(f'本次运行记录: {context.manifest_path}', flush=True)
    try:
        params_path = update_capacity_params(thresholds, context.directory / 'capacity_params.json')
        context.manifest['thresholds'] = thresholds
        context.manifest['capacity_params'] = describe_file(params_path)
        context.capture_versions()
        context.prepare_coverage()
        run_skills(input_dir, dir_name, context)
        context.reconcile_coverage()
        final_path = generate_sam_report(input_dir, dir_name, context, output_root=output_path)
        context.finish(path=final_path)
        return final_path
    except BaseException as error:
        context.finish(error=error)
        raise

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("\u7528\u6cd5: python run.py " + chr(60) + "ASUP\u65e5\u5fd7\u76ee\u5f55" + chr(62) + " [--interactive]")
        print("  --interactive: \u4ea4\u4e92\u5f0f\u9009\u62e9\u9608\u503c\uff08\u9ed8\u8ba4\u4f7f\u7528\u9ed8\u8ba4\u503c\uff09")
        sys.exit(1)
    input_dir = sys.argv[1]
    use_defaults = True
    for arg in sys.argv[2:]:
        if arg == "--interactive":
            use_defaults = False
    process_directory(input_dir, None, use_defaults)
