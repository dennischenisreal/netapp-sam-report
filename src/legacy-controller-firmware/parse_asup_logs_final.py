import os
import re
import sys
import pandas as pd
import subprocess
import tempfile
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Border, Side, Alignment
from openpyxl.utils.dataframe import dataframe_to_rows
from pathlib import Path

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
kb_root = str(path_setting('knowledge_root', Path.home() / 'Documents/SAM report/知识库'))

def _kb_file(sub_dir, pattern):
    import glob
    hits = glob.glob(os.path.join(kb_root, sub_dir, pattern))
    return hits[0] if hits else os.path.join(kb_root, sub_dir, pattern)

eos_file = _kb_file('ONTAP EOS date', '*.xlsx')
platform_eos_file = _kb_file('Platform EOS date', '*.xlsx')
ma_file = _kb_file('MA date', '*.xlsx')
output_base_dir = os.environ.get('SAM_REPORT_OUTPUT_DIR') or str(path_setting('legacy_root', Path.home() / 'Desktop/demo/Trae Solo Coder') / 'ASUP Analysis/output')
seven_zip_path = str(path_setting('seven_zip_path', Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / '7-Zip/7z.exe'))

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
        return None

def parse_sysconfig(content):
    data = {}
    if not content:
        return data
    
    match = re.search(r'NetApp Release ([\d.]+[P\d]*)', content)
    if match:
        data['OS Version'] = match.group(1)
    
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

def get_os_eos_date(os_version, eos_df):
    if not os_version:
        return 'N/A'
    major_version = re.match(r'(\d+\.\d+)', os_version)
    if not major_version:
        return 'N/A'
    version_prefix = major_version.group(1)
    
    for _, row in eos_df.iterrows():
        version_str = str(row['Version'])
        if version_str.startswith(version_prefix):
            eos_date = row['End of Limited Support']
            return str(eos_date).split(' ')[0] if pd.notna(eos_date) else 'N/A'
    return 'N/A'

def get_hardware_eos_date(model, platform_df):
    if not model:
        return '暂未发布'
    
    model_clean = model.strip().upper()
    
    eos_col = 'EOS日期'
    if 'EOS日期' not in platform_df.columns:
        for col in platform_df.columns:
            if 'EOS' in col or '日期' in col:
                eos_col = col
                break
        else:
            return '暂未发布'
    
    for _, row in platform_df.iterrows():
        platform_model = str(row['设备型号']).strip().upper() if '设备型号' in platform_df.columns else str(row[0]).strip().upper()
        if platform_model == model_clean or model_clean.startswith(platform_model):
            eos_date = row[eos_col]
            if pd.notna(eos_date):
                if isinstance(eos_date, pd.Timestamp):
                    return eos_date.strftime('%Y-%m-%d')
                return str(eos_date).split(' ')[0]
    return '暂未发布'

def get_ma_info(serial_number, ma_df):
    if not serial_number:
        return {'维保开始时间': 'N/A', '维保到期时间': 'N/A', '服务级别': 'N/A'}
    
    for idx, row in ma_df.iterrows():
        ma_serial = str(row[1]).strip()
        if ma_serial == serial_number:
            return {
                '维保开始时间': str(row[2]).split(' ')[0] if pd.notna(row[2]) else 'N/A',
                '维保到期时间': str(row[3]).split(' ')[0] if pd.notna(row[3]) else 'N/A',
                '服务级别': str(row[4]) if pd.notna(row[4]) else 'N/A'
            }
    return {'维保开始时间': 'N/A', '维保到期时间': 'N/A', '服务级别': 'N/A'}

def create_beautiful_excel(df, output_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Controller Report"
    
    header_fill = PatternFill(start_color='1E3A5F', end_color='1E3A5F', fill_type='solid')
    header_font = Font(color='FFFFFF', bold=True, size=11)
    data_font = Font(size=10)
    yellow_fill = PatternFill(start_color='FFFF00', end_color='FFFF00', fill_type='solid')
    
    thin_border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )
    
    alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
    
    headers = ['Cluster Name', 'System Name', 'Serial Number', 'Model', 'OS Version', 
               'OS EoLS日期', '硬件EOS日期', '维保开始时间', '维保到期时间', '服务级别']
    
    warning_cols = {
        'OS EoLS日期': 6,
        '硬件EOS日期': 7,
        '维保到期时间': 9
    }
    
    today = datetime.now()
    warning_threshold_days = 365
    
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
                
                header_name = headers[c_idx - 1] if c_idx <= len(headers) else ''
                
                if header_name in warning_cols and value and value != 'N/A' and value != '暂未发布':
                    try:
                        if isinstance(value, str):
                            date_value = datetime.strptime(str(value).split()[0], '%Y-%m-%d')
                        else:
                            date_value = value
                        
                        days_until = (date_value - today).days
                        
                        if 0 <= days_until <= warning_threshold_days:
                            cell.fill = yellow_fill
                    except:
                        pass
    
    column_widths = [15, 18, 15, 12, 14, 14, 14, 14, 14, 40]
    for i, width in enumerate(column_widths, 1):
        ws.column_dimensions[chr(64 + i)].width = width
    
    for row in range(1, len(df) + 2):
        ws.row_dimensions[row].height = 18
    
    ws.freeze_panes = 'A2'
    
    wb.save(output_path)

def process_directory(log_dir, eos_df, platform_df, ma_df):
    results = []
    files = sorted([f for f in os.listdir(log_dir) if f.endswith('.7z')])
    
    print(f"  解析 {len(files)} 个7z日志文件...")
    for filename in files:
        filepath = os.path.join(log_dir, filename)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            sysconfig_content = extract_file(filepath, 'SYSCONFIG-A.txt', tmpdir)
            cluster_content = extract_file(filepath, 'CLUSTER-INFO.xml', tmpdir)
            
            data = {
                'Cluster Name': 'N/A',
                'System Name': 'N/A',
                'Serial Number': 'N/A',
                'Model': 'N/A',
                'OS Version': 'N/A',
                'OS EoLS日期': 'N/A',
                '硬件EOS日期': '暂未发布',
                '维保开始时间': 'N/A',
                '维保到期时间': 'N/A',
                '服务级别': 'N/A'
            }
            
            if sysconfig_content:
                sysconfig_data = parse_sysconfig(sysconfig_content)
                for key in sysconfig_data:
                    if sysconfig_data[key]:
                        data[key] = sysconfig_data[key]
            
            if cluster_content:
                cluster_data = parse_cluster_info(cluster_content)
                if 'Cluster Name' in cluster_data and cluster_data['Cluster Name']:
                    if data['Cluster Name'] == 'N/A':
                        data['Cluster Name'] = cluster_data['Cluster Name']
            
            data['OS EoLS日期'] = get_os_eos_date(data['OS Version'], eos_df)
            data['硬件EOS日期'] = get_hardware_eos_date(data['Model'], platform_df)
            
            ma_info = get_ma_info(data['Serial Number'], ma_df)
            data.update(ma_info)
            
            results.append(data)
    
    if results:
        df = pd.DataFrame(results)
        df = df[['Cluster Name', 'System Name', 'Serial Number', 'Model', 'OS Version', 
                 'OS EoLS日期', '硬件EOS日期', '维保开始时间', '维保到期时间', '服务级别']]
        df = df.sort_values('Cluster Name')
        return df
    return None

def main():
    print("加载知识库...")
    eos_df = pd.read_excel(eos_file, header=1)
    platform_df = pd.read_excel(platform_eos_file)
    ma_df = pd.read_excel(ma_file, header=None)
    
    os.makedirs(output_base_dir, exist_ok=True)
    
    if len(sys.argv) > 1:
        subdirs = [os.path.basename(sys.argv[1])]
        base_log_dir = os.path.dirname(sys.argv[1])
    else:
        subdirs = legacy_sites()
    
    for subdir in subdirs:
        log_dir = os.path.join(base_log_dir, subdir)
        if not os.path.isdir(log_dir):
            print(f"目录不存在: {log_dir}")
            continue
        
        print(f"\n处理目录: {subdir}")
        df = process_directory(log_dir, eos_df, platform_df, ma_df)
        
        if df is not None:
            output_path = os.path.join(output_base_dir, f'{subdir}_Controller_Report.xlsx')
            create_beautiful_excel(df, output_path)
            print(f"  生成表格: {output_path}")
            print(f"  共 {len(df)} 条记录")
        else:
            print(f"  未解析到数据")
    
    print("\n所有目录处理完成！")

if __name__ == '__main__':
    main()
