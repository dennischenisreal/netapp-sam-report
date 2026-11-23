import os
import re
import sys
from pathlib import Path
import pandas as pd
import subprocess
import tempfile
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Border, Side, Alignment
from openpyxl.utils.dataframe import dataframe_to_rows
from openpyxl.chart import BarChart, PieChart, Reference, Series
from openpyxl.chart.label import DataLabelList
from openpyxl.chart.shapes import GraphicalProperties

_shared_skill = os.environ.get('SAM_SHARED_SKILL_DIR')
if _shared_skill is not None:
    if not _shared_skill.strip() or not Path(_shared_skill).is_absolute():
        raise ValueError('SAM_SHARED_SKILL_DIR must be a non-empty absolute path')
    _shared_skill = Path(_shared_skill)
else:
    _shared_skill = Path(__file__).resolve().parents[1] / 'NetApp-SAM-Report'
    if not (_shared_skill / 'sam_settings.py').is_file():
        _shared_skill = Path(__file__).resolve().parents[2] / 'skills/NetApp-SAM-Report'
if not (_shared_skill / 'sam_settings.py').is_file():
    raise FileNotFoundError('Shared SAM path settings are missing')
sys.path.insert(0, str(_shared_skill))
from sam_settings import path_setting
from sam_package_compat import legacy_sites, firmware_name

base_log_dir = str(path_setting('logs_root', Path.home() / 'Desktop/demo/ASUP'))
case_data_dir = str(path_setting('knowledge_root', Path.home() / 'Documents/SAM report/知识库') / 'JV Case 数据源')
output_dir = os.environ.get('SAM_REPORT_OUTPUT_DIR') or str(path_setting('legacy_root', Path.home() / 'Desktop/demo/Trae Solo Coder') / 'Case Analysis/output')
seven_zip_path = str(path_setting('seven_zip_path', Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / '7-Zip/7z.exe'))

os.makedirs(output_dir, exist_ok=True)

def extract_file(archive_path, target_file, output_dir):
    try:
        cmd = [seven_zip_path, 'e', '-o' + output_dir, '-y', archive_path, target_file]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            file_path = os.path.join(output_dir, target_file)
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    return f.read()
        return None
    except Exception as e:
        print(f"提取文件出错: {e}")
        return None

def parse_sysconfig(content):
    data = {}
    if not content:
        return data
    
    match = re.search(r'System Serial Number:\s*(\S+)', content)
    if match:
        data['Serial Number'] = match.group(1)
    
    match = re.search(r'System ID:\s*\d+\s*\(([^)]+)\)', content)
    if match:
        data['System Name'] = match.group(1)
    
    match = re.search(r'Model Name:\s*(\S+)', content)
    if match:
        data['Model'] = match.group(1)
    
    lines = content.split('\n')
    for line in lines:
        if 'Cluster Name:' in line:
            parts = line.split(':', 1)
            if len(parts) > 1:
                data['Cluster Name'] = parts[1].strip()
    
    return data

def parse_cluster_info(content):
    data = {}
    if not content:
        return data
    match = re.search(r'<cluster-name>([^<]+)</cluster-name>', content)
    if match:
        data['Cluster Name'] = match.group(1).strip()
    return data

def process_devices(log_dir):
    devices = {}
    files = sorted([f for f in os.listdir(log_dir) if f.endswith('.7z')])
    
    print(f"开始解析 {len(files)} 个7z日志文件...")
    
    for filename in files:
        filepath = os.path.join(log_dir, filename)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            sysconfig_content = extract_file(filepath, 'SYSCONFIG-A.txt', tmpdir)
            cluster_content = extract_file(filepath, 'CLUSTER-INFO.xml', tmpdir)
            
            device_data = {}
            
            if sysconfig_content:
                sysconfig_data = parse_sysconfig(sysconfig_content)
                device_data.update(sysconfig_data)
            
            if cluster_content:
                cluster_data = parse_cluster_info(cluster_content)
                if 'Cluster Name' in cluster_data and cluster_data['Cluster Name']:
                    if 'Cluster Name' not in device_data or device_data['Cluster Name'] == 'N/A':
                        device_data['Cluster Name'] = cluster_data['Cluster Name']
            
            if 'Serial Number' in device_data:
                sn = device_data['Serial Number']
                if sn not in devices:
                    devices[sn] = device_data
                    print(f"  解析到设备: {device_data.get('System Name', 'N/A')} (SN: {sn}, Cluster: {device_data.get('Cluster Name', 'N/A')})")
    
    print(f"共解析到 {len(devices)} 个设备")
    return devices

def load_case_data():
    current_year = datetime.now().year
    
    case_files = []
    for filename in os.listdir(case_data_dir):
        if filename.startswith(f'Case查询-{current_year}年') and filename.endswith('.xlsx'):
            case_files.append(os.path.join(case_data_dir, filename))
    
    if not case_files:
        print(f"警告: 未找到{current_year}年度的Case数据文件")
        return None
    
    all_cases = []
    for file in sorted(case_files):
        if os.path.exists(file):
            df = pd.read_excel(file)
            all_cases.append(df)
            print(f"加载case文件: {file} ({len(df)} 条记录)")
    
    if all_cases:
        print(f"\n=== 当前年度: {current_year}年 ===")
        return pd.concat(all_cases, ignore_index=True)
    return None

def load_last_year_case_data():
    last_year = datetime.now().year - 1
    
    case_files = []
    for filename in os.listdir(case_data_dir):
        if filename.startswith(f'Case查询-{last_year}年') and filename.endswith('.xlsx'):
            case_files.append(os.path.join(case_data_dir, filename))
    
    if not case_files:
        print(f"警告: 未找到{last_year}年度的Case数据文件")
        return None
    
    all_cases = []
    for file in sorted(case_files):
        if os.path.exists(file):
            df = pd.read_excel(file)
            all_cases.append(df)
            print(f"加载上年度case文件: {file} ({len(df)} 条记录)")
    
    if all_cases:
        print(f"\n=== 上年度: {last_year}年 ===")
        return pd.concat(all_cases, ignore_index=True)
    return None

def simplify_hardware_description(desc):
    if pd.isna(desc):
        return 'N/A'
    
    desc = str(desc)
    
    date_pattern = r'\d{4}年\d{1,2}月\d{1,2}日\d{1,2}(:\d{2})?|\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(:\d{2})?'
    desc = re.sub(date_pattern, '', desc)
    desc = re.sub(r'\b\d{1,2}:\d{2}(:\d{2})?\b', '', desc)
    
    location_info = ""
    part_info = ""
    
    location_patterns = [
        (r'(\b\d+\.\d+\b)', "槽位"),
        (r'(slot[:：]\s*\d+)', "插槽"),
        (r'(Slot[:：]\s*\d+)', "插槽"),
        (r'(controller\s+[AB])', "控制器"),
        (r'(node\s+[AB])', "节点"),
        (r'(\b[A-Z][0-9]\b)', "位置"),
        (r'(电源[:：]\s*\d+)', "电源"),
        (r'(风扇[:：]\s*\d+)', "风扇"),
        (r'(disk[:：]\s*\d+)', "磁盘"),
        (r'(Disk[:：]\s*\d+)', "磁盘"),
        (r'(port[:：]\s*\d+)', "端口"),
        (r'(Port[:：]\s*\d+)', "端口")
    ]
    
    for pattern, label in location_patterns:
        match = re.search(pattern, desc, re.IGNORECASE)
        if match:
            location_info = f"{label} {match.group(1)}"
            break
    
    part_num_patterns = [
        (r'(PN[:：]\s*[\w-]+)', "部件号"),
        (r'(Part\s*Number[:：]\s*[\w-]+)', "部件号"),
        (r'部件号[:：]\s*([\w-]+)', "部件号"),
        (r'(\bX[\w-]+\b)', "备件号"),
        (r'(\b[\w-]{10,}\b)', "备件号")
    ]
    
    for pattern, label in part_num_patterns:
        match = re.search(pattern, desc)
        if match:
            part_info = f"{label} {match.group(1)}"
            break
    
    result_parts = []
    if location_info:
        result_parts.append(location_info)
    if part_info:
        result_parts.append(part_info)
    
    if result_parts:
        return " | ".join(result_parts)
    else:
        cleaned_desc = re.sub(r'\s+', ' ', desc).strip()
        return cleaned_desc[:30] if len(cleaned_desc) > 30 else cleaned_desc

def simplify_software_description(desc):
    if pd.isna(desc):
        return 'N/A'
    
    desc = str(desc)
    
    issue_mapping = {
        '性能问题': ['性能', '慢', '延迟', '卡', '响应', 'performance', 'latency'],
        '网络问题': ['网络', '连接', '端口', '通讯', 'network', 'port', 'connection'],
        '空间不足': ['空间', '满', '容量', 'disk full', '容量不足'],
        '时间同步': ['时间', 'NTP', '时钟', 'timestamp', '同步'],
        '配置错误': ['配置', '参数', '设置', 'config', 'parameter'],
        '协议异常': ['协议', 'NFS', 'SMB', 'CIFS', 'protocol'],
        '数据问题': ['数据', '文件', '备份', '恢复', 'data', 'file'],
        '版本问题': ['版本', '升级', '更新', 'upgrade', 'version'],
        '权限问题': ['权限', '用户', 'access', 'permission']
    }
    
    found_issues = []
    for issue_type, keywords in issue_mapping.items():
        for keyword in keywords:
            if keyword.lower() in desc.lower():
                found_issues.append(issue_type)
                break
    
    if found_issues:
        if len(found_issues) == 1:
            return f"系统{found_issues[0]}"
        elif len(found_issues) == 2:
            return f"系统{found_issues[0]}及{found_issues[1]}"
        else:
            return f"系统多方面问题({''.join(found_issues)})"
    else:
        return "系统运行异常"

def match_cases(devices, case_df):
    matched_cases = []
    matched_case_ids = set()
    
    case_df['主机编号'] = case_df['主机编号'].astype(str)
    
    for sn, device_data in devices.items():
        device_cases = case_df[case_df['主机编号'] == sn]
        
        for _, case_row in device_cases.iterrows():
            case_id = case_row['Case ID']
            if case_id in matched_case_ids:
                continue
            
            matched_case_ids.add(case_id)
            fault_type = case_row['故障二级分类']
            
            if fault_type == '硬件故障':
                simplified_desc = simplify_hardware_description(case_row['问题描述'])
            elif fault_type == '软件故障':
                simplified_desc = simplify_software_description(case_row['问题描述'])
            else:
                simplified_desc = str(case_row['问题描述'])[:30] if pd.notna(case_row['问题描述']) else 'N/A'
            
            case_info = {
                'Case ID': case_id,
                'Case创建时间': case_row['Case创建时间'],
                '设备型号': case_row['四级产品树'],
                '序列号': sn,
                '集群名': device_data.get('Cluster Name', 'N/A'),
                '节点名': device_data.get('System Name', 'N/A'),
                '故障分类': fault_type,
                '故障部件': case_row['故障三级分类'],
                '故障四级分类': case_row['故障四级分类'],
                '问题描述': simplified_desc,
                '客户姓名': case_row['客户姓名'],
                '企业名称': case_row['企业名称'],
                '是否有上门单': case_row['是否有上门单']
            }
            matched_cases.append(case_info)
    
    return matched_cases

def add_charts(ws, df, sheet_name):
    if df.empty:
        return
    
    row_offset = len(df) + 5
    
    blue_green_colors = [
        "004D80", 
        "0066A3", 
        "007AB3", 
        "008FB3", 
        "00A3AD", 
        "00B39D", 
        "00C48C", 
        "00D47A",
        "3399CC", 
        "66B2CC"
    ]
    
    if sheet_name == '硬件case':
        model_counts = df['设备型号'].value_counts().reset_index()
        model_counts.columns = ['设备型号', '故障数量']
        
        ws.cell(row=row_offset, column=1, value='设备型号')
        ws.cell(row=row_offset, column=2, value='故障数量')
        ws.cell(row=row_offset, column=1).font = Font(bold=True)
        ws.cell(row=row_offset, column=2).font = Font(bold=True)
        
        for i, row in model_counts.iterrows():
            ws.cell(row=row_offset + i + 1, column=1, value=row['设备型号'])
            ws.cell(row=row_offset + i + 1, column=2, value=row['故障数量'])
        
        if len(model_counts) > 0:
            chart1 = BarChart()
            chart1.title = "故障按设备型号分布"
            chart1.x_axis.title = "设备型号"
            chart1.y_axis.title = "故障数量"
            
            data = Reference(ws, min_col=2, min_row=row_offset + 1, max_row=row_offset + len(model_counts))
            cats = Reference(ws, min_col=1, min_row=row_offset + 1, max_row=row_offset + len(model_counts))
            chart1.add_data(data, titles_from_data=False)
            chart1.set_categories(cats)
            
            if chart1.series:
                chart1.series[0].graphicalProperties = GraphicalProperties(solidFill=blue_green_colors[0])
            
            chart1.dataLabels = DataLabelList()
            chart1.dataLabels.showVal = True
            
            ws.add_chart(chart1, f'D{row_offset}')
        
        row_offset += len(model_counts) + 15
        
        cluster_counts = df['集群名'].value_counts().reset_index()
        cluster_counts.columns = ['集群名', '故障数量']
        
        ws.cell(row=row_offset, column=1, value='集群名称')
        ws.cell(row=row_offset, column=2, value='故障数量')
        ws.cell(row=row_offset, column=1).font = Font(bold=True)
        ws.cell(row=row_offset, column=2).font = Font(bold=True)
        
        for i, row in cluster_counts.iterrows():
            ws.cell(row=row_offset + i + 1, column=1, value=row['集群名'])
            ws.cell(row=row_offset + i + 1, column=2, value=row['故障数量'])
        
        if len(cluster_counts) > 0:
            chart2 = PieChart()
            chart2.title = "故障按集群分布"
            
            data = Reference(ws, min_col=2, min_row=row_offset + 1, max_row=row_offset + len(cluster_counts))
            cats = Reference(ws, min_col=1, min_row=row_offset + 1, max_row=row_offset + len(cluster_counts))
            chart2.add_data(data, titles_from_data=False)
            chart2.set_categories(cats)
            
            for idx, color in enumerate(blue_green_colors[:len(cluster_counts)]):
                if idx < len(chart2.series) and chart2.series[idx].graphicalProperties:
                    chart2.series[idx].graphicalProperties.solidFill = color
            
            chart2.dataLabels = DataLabelList()
            chart2.dataLabels.showVal = True
            chart2.dataLabels.showPercent = True
            
            ws.add_chart(chart2, f'D{row_offset}')
    
    elif sheet_name == '软件case':
        issue_counts = df['问题描述'].value_counts().reset_index()
        issue_counts.columns = ['问题类型', '故障数量']
        
        ws.cell(row=row_offset, column=1, value='问题类型')
        ws.cell(row=row_offset, column=2, value='故障数量')
        ws.cell(row=row_offset, column=1).font = Font(bold=True)
        ws.cell(row=row_offset, column=2).font = Font(bold=True)
        
        for i, row in issue_counts.iterrows():
            ws.cell(row=row_offset + i + 1, column=1, value=row['问题类型'])
            ws.cell(row=row_offset + i + 1, column=2, value=row['故障数量'])
        
        if len(issue_counts) > 0:
            chart1 = BarChart()
            chart1.title = "软件故障按问题类型分布"
            chart1.x_axis.title = "问题类型"
            chart1.y_axis.title = "故障数量"
            
            data = Reference(ws, min_col=2, min_row=row_offset + 1, max_row=row_offset + len(issue_counts))
            cats = Reference(ws, min_col=1, min_row=row_offset + 1, max_row=row_offset + len(issue_counts))
            chart1.add_data(data, titles_from_data=False)
            chart1.set_categories(cats)
            
            if chart1.series:
                chart1.series[0].graphicalProperties = GraphicalProperties(solidFill=blue_green_colors[0])
            
            chart1.dataLabels = DataLabelList()
            chart1.dataLabels.showVal = True
            
            ws.add_chart(chart1, f'D{row_offset}')

def auto_adjust_column_width(ws):
    for col in ws.columns:
        max_length = 0
        column = col[0].column_letter
        for cell in col:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except:
                pass
        adjusted_width = max(max_length + 5, 20)
        ws.column_dimensions[column].width = adjusted_width

def create_beautiful_excel(hardware_df, software_df, other_df, last_year_case_df, output_path):
    wb = Workbook()
    
    header_fill = PatternFill(start_color='1E3A5F', end_color='1E3A5F', fill_type='solid')
    header_font = Font(color='FFFFFF', bold=True, size=11)
    data_font = Font(size=10)
    
    thin_border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )
    
    alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
    
    def add_sheet(name, df):
        ws = wb.create_sheet(title=name)
        
        # 如果没有数据，添加提示信息
        if df.empty:
            ws.cell(row=1, column=1, value='暂无数据')
            ws.cell(row=1, column=1).font = Font(italic=True, color='808080', size=12)
            ws.cell(row=1, column=1).alignment = Alignment(horizontal='center', vertical='center')
            ws.cell(row=3, column=1, value=f'该分类暂无匹配的Case记录')
            ws.cell(row=3, column=1).font = Font(italic=True, color='808080', size=10)
            ws.cell(row=3, column=1).alignment = Alignment(horizontal='center', vertical='center')
            ws.column_dimensions['A'].width = 50
            return
        
        for r_idx, row in enumerate(dataframe_to_rows(df, index=False, header=True), 1):
            for c_idx, value in enumerate(row, 1):
                cell = ws.cell(row=r_idx, column=c_idx, value=value)
                cell.border = thin_border
                cell.alignment = alignment
                
                if r_idx == 1:
                    cell.fill = header_fill
                    cell.font = header_font
                else:
                    cell.font = data_font
        
        for row in range(1, len(df) + 2):
            ws.row_dimensions[row].height = 18
        
        ws.freeze_panes = 'A2'
        
        add_charts(ws, df, name)
        
        auto_adjust_column_width(ws)
    
    def add_year_comparison_and_list(last_year_df):
        ws = wb.create_sheet(title='年度对比')
        
        current_year = datetime.now().year
        last_year = current_year - 1
        
        row = 1
        
        ws.cell(row=row, column=1, value=f'Case数据年度对比 ({last_year}年 vs {current_year}年)')
        ws.cell(row=row, column=1).font = Font(bold=True, size=14, color='1E3A5F')
        row += 2
        
        ws.cell(row=row, column=1, value='统计项目')
        ws.cell(row=row, column=2, value=f'{last_year}年')
        ws.cell(row=row, column=3, value=f'{current_year}年')
        for col in range(1, 4):
            ws.cell(row=row, column=col).font = Font(bold=True)
            ws.cell(row=row, column=col).fill = header_fill
            ws.cell(row=row, column=col).font = Font(color='FFFFFF', bold=True)
            ws.cell(row=row, column=col).alignment = Alignment(horizontal='center')
        row += 1
        
        if last_year_df is not None:
            last_year_total = len(last_year_df)
            last_year_hardware = len(last_year_df[last_year_df['故障二级分类'] == '硬件故障'])
            last_year_software = len(last_year_df[last_year_df['故障二级分类'] == '软件故障'])
            last_year_other = len(last_year_df[last_year_df['故障二级分类'] == '非技术类'])
        else:
            last_year_total = 0
            last_year_hardware = 0
            last_year_software = 0
            last_year_other = 0
            ws.cell(row=5, column=1, value='备注:')
            ws.cell(row=5, column=2, value=f'{last_year}年未查询到Case数据')
            ws.cell(row=5, column=2).font = Font(italic=True, color='808080')
        
        current_total = len(hardware_df) + len(software_df) + len(other_df)
        
        stats = [
            ('Case总数', last_year_total, current_total),
            ('硬件故障', last_year_hardware, len(hardware_df)),
            ('软件故障', last_year_software, len(software_df)),
            ('非技术类', last_year_other, len(other_df))
        ]
        
        for stat_name, last_val, curr_val in stats:
            ws.cell(row=row, column=1, value=stat_name)
            ws.cell(row=row, column=2, value=last_val)
            ws.cell(row=row, column=3, value=curr_val)
            
            for col in range(1, 4):
                ws.cell(row=row, column=col).border = thin_border
                ws.cell(row=row, column=col).alignment = Alignment(horizontal='center')
            row += 1
        
        ws.column_dimensions['A'].width = 15
        ws.column_dimensions['B'].width = 12
        ws.column_dimensions['C'].width = 12
        
        if last_year_df is not None:
            row += 2
            ws.cell(row=row, column=1, value=f'{last_year}年详细分类统计')
            ws.cell(row=row, column=1).font = Font(bold=True, size=12, color='1E3A5F')
            row += 1
            
            ws.cell(row=row, column=1, value='故障分类')
            ws.cell(row=row, column=2, value='数量')
            ws.cell(row=row, column=3, value='占比')
            for col in range(1, 4):
                ws.cell(row=row, column=col).font = Font(bold=True)
                ws.cell(row=row, column=col).fill = header_fill
                ws.cell(row=row, column=col).font = Font(color='FFFFFF', bold=True)
            row += 1
            
            last_year_counts = last_year_df['故障二级分类'].value_counts()
            for category, count in last_year_counts.items():
                percentage = f"{(count / last_year_total * 100):.1f}%"
                ws.cell(row=row, column=1, value=category)
                ws.cell(row=row, column=2, value=count)
                ws.cell(row=row, column=3, value=percentage)
                
                for col in range(1, 4):
                    ws.cell(row=row, column=col).border = thin_border
                row += 1
        
        if last_year_df is not None and not last_year_df.empty:
            row += 4
            ws.cell(row=row, column=1, value=f'{last_year}年Case清单')
            ws.cell(row=row, column=1).font = Font(bold=True, size=12, color='1E3A5F')
            row += 1
            
            selected_cols = ['Case ID', 'Case创建时间', '四级产品树', '主机编号', '故障二级分类', '问题描述', '客户姓名', '企业名称', '是否有上门单']
            display_cols = ['Case ID', '创建时间', '设备型号', '序列号', '故障分类', '问题描述', '客户姓名', '企业名称', '是否上门']
            
            df_display = last_year_df[selected_cols].copy()
            df_display.columns = display_cols
            
            for c_idx, col_name in enumerate(display_cols, 1):
                cell = ws.cell(row=row, column=c_idx, value=col_name)
                cell.fill = header_fill
                cell.font = header_font
                cell.border = thin_border
                cell.alignment = Alignment(horizontal='center')
            row += 1
            
            for _, df_row in df_display.iterrows():
                for c_idx, value in enumerate(df_row, 1):
                    if c_idx == 6 and pd.notna(value):
                        value = str(value)[:30]
                    cell = ws.cell(row=row, column=c_idx, value=value)
                    cell.border = thin_border
                    cell.alignment = alignment
                    cell.font = data_font
                row += 1
            
            ws.column_dimensions['F'].width = 20
        
        if wb.sheetnames[0] == 'Sheet':
            wb.remove(wb['Sheet'])
    
    add_year_comparison_and_list(last_year_case_df)
    add_sheet('硬件case', hardware_df)
    add_sheet('软件case', software_df)
    add_sheet('非技术case', other_df)
    
    wb.save(output_path)

def main():
    import sys
    
    print("=" * 60)
    print("NetApp设备Case分析工具")
    print("=" * 60)
    
    print("\n加载Case数据...")
    case_df = load_case_data()
    if case_df is None:
        print("错误: 未能加载case数据")
        return
    
    print("\n加载上年度Case数据...")
    last_year_case_df = load_last_year_case_data()
    
    if len(sys.argv) > 1:
        input_dir = sys.argv[1]
        subdirs = [os.path.basename(input_dir)]
        base_log_dir = os.path.dirname(input_dir)
    else:
        subdirs = legacy_sites()
    
    current_year = datetime.now().year
    
    for subdir in subdirs:
        log_dir = os.path.join(base_log_dir, subdir)
        if not os.path.isdir(log_dir):
            print(f"\n目录不存在: {log_dir}")
            continue
        
        print(f"\n{'='*60}")
        print(f"处理目录: {subdir}")
        print('='*60)
        
        devices = process_devices(log_dir)
        if not devices:
            print("  未能解析到任何设备")
            continue
        
        print("\n匹配Case记录...")
        matched_cases = match_cases(devices, case_df)
        
        device_sns = set(devices.keys())
        
        filtered_last_year_df = None
        if last_year_case_df is not None and not last_year_case_df.empty:
            last_year_case_df['主机编号'] = last_year_case_df['主机编号'].astype(str)
            filtered_last_year_df = last_year_case_df[last_year_case_df['主机编号'].isin(device_sns)]
            print(f"  上年度Case数据已筛选: {len(filtered_last_year_df)} 条匹配记录")
        
        if matched_cases:
            result_df = pd.DataFrame(matched_cases)
            result_df = result_df[['Case ID', 'Case创建时间', '设备型号', '序列号', '集群名', '节点名', 
                                  '故障分类', '故障部件', '故障四级分类', '问题描述', 
                                  '客户姓名', '企业名称', '是否有上门单']]
            
            hardware_df = result_df[result_df['故障分类'] == '硬件故障']
            software_df = result_df[result_df['故障分类'] == '软件故障']
            other_df = result_df[result_df['故障分类'] == '非技术类']
            
            output_path = os.path.join(output_dir, f'{subdir}_{current_year}_Case_Report.xlsx')
            create_beautiful_excel(hardware_df, software_df, other_df, filtered_last_year_df, output_path)
            
            print(f"\n成功! 共匹配到 {len(matched_cases)} 条Case记录")
            print(f"  - 硬件故障: {len(hardware_df)} 条")
            print(f"  - 软件故障: {len(software_df)} 条")
            print(f"  - 非技术类: {len(other_df)} 条")
            print(f"报告已生成: {output_path}")
        else:
            print("\n未匹配到任何Case记录")
            empty_df = pd.DataFrame(columns=['Case ID', 'Case创建时间', '设备型号', '序列号', '集群名', '节点名',
                                             '故障分类', '故障部件', '故障四级分类', '问题描述',
                                             '客户姓名', '企业名称', '是否有上门单'])
            output_path = os.path.join(output_dir, f'{subdir}_{current_year}_Case_Report.xlsx')
            create_beautiful_excel(empty_df, empty_df, empty_df, filtered_last_year_df, output_path)
            print(f'空Case报告已生成: {output_path}')
    
    print("\n" + "="*60)
    print("所有目录处理完成！")
    print("="*60)

if __name__ == '__main__':
    main()
