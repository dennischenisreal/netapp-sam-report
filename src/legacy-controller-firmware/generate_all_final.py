import os
import re
import sys
import pandas as pd
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
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
output_dir = os.environ.get('SAM_REPORT_OUTPUT_DIR') or str(path_setting('legacy_root', Path.home() / 'Desktop/demo/Trae Solo Coder') / 'ASUP Analysis/output')
seven_zip_path = str(path_setting('seven_zip_path', Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / '7-Zip/7z.exe'))

DISK_NS = 'http://asup_search.netapp.com/ns/T_DISK_INFO/1.0'
DISK_NS_MAP = {'ns': DISK_NS}

SP_NS = 'http://asup_search.netapp.com/ns/T_SPINFO/1.0'
SP_NS_MAP = {'ns': SP_NS}

SWITCH_NS = 'http://asup_search.netapp.com/ns/T_cshm_switch_config/1.0'
SWITCH_NS_MAP = {'ns': SWITCH_NS}

SHELF_NS = 'http://asup_search.netapp.com/ns/T_STORAGE_SHELF/1.0'

# BMC/SP firmware knowledge base path
# BMC/SP firmware knowledge base - find by glob to avoid Unicode path issues in subprocess
import glob as _glob
BMC_FW_KB_DIRS = [
    str(path_setting('knowledge_root', Path.home() / 'Documents/SAM report/知识库') / 'BMC firmware'),
]
BMC_FW_KB_PATH = ''
for _d in BMC_FW_KB_DIRS:
    _files = _glob.glob(os.path.join(_d, '*.xlsx'))
    if _files:
        BMC_FW_KB_PATH = _files[0]
        break
if not BMC_FW_KB_PATH:
    print('  Warning: BMC firmware knowledge base not found')

def get_bmc_firmware_lookup():
    """获取 BMC/SP 巩件查询表 {major_version -> latest_version}"""
    try:
        from openpyxl import load_workbook
        wb = load_workbook(BMC_FW_KB_PATH)
        ws = wb[wb.sheetnames[0]]  # First sheet
        lookup = {}
        for r in range(3, ws.max_row + 1):
            major = str(ws.cell(r, 1).value or '').strip()
            latest = str(ws.cell(r, 2).value or '').strip()
            if major and latest:
                lookup[major] = latest
        wb.close()
        return lookup
    except Exception as e:
        print(f'  Warning: 加载 BMC firmware 知识库失败: {e}')
        return {}

def get_latest_firmware(fw_version, fw_type, lookup):
    """?????????????????????"""
    if not fw_version or not lookup:
        return ''
    
    m = re.match(r'(\d+)\.', str(fw_version).strip())
    if not m:
        return ''
    major_num = m.group(1)
    
    fw_type_str = str(fw_type or '').strip().upper()
    prefix = 'SP' if fw_type_str == 'SP' else 'BMC'
    
    major_key = f'{prefix} {major_num}.x'
    if major_key in lookup:
        return lookup[major_key]
    
    if prefix == 'BMC':
        alt_key = f'SP {major_num}.x'
        if alt_key in lookup:
            return lookup[alt_key]
    
    return ''

def extract_file(archive_path, target_file, output_dir):
    cmd = [seven_zip_path, 'e', '-o' + output_dir, '-y', archive_path, target_file]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f'Firmware extraction failed: {target_file}, exit={result.returncode}')
    file_path = os.path.join(output_dir, target_file)
    if not os.path.exists(file_path):
        return None
    with open(file_path, 'r', encoding='utf-8-sig', errors='strict') as stream:
        content = stream.read()
    if target_file.endswith('.xml'):
        if not content.strip():
            raise ValueError(f'Firmware XML is empty, not absent: {target_file}')
        ET.fromstring(content)
    return content

def parse_controller_info(content):
    info = {
        'Serial Number': 'N/A',
        'Model': 'N/A',
        'Node Name': 'N/A',
        'Cluster Name': 'N/A'
    }
    
    match = re.search(r'System Serial Number:\s*(\S+)', content)
    if match:
        info['Serial Number'] = match.group(1)
    
    match = re.search(r'Model Name:\s*(\S+)', content)
    if match:
        info['Model'] = match.group(1)
    
    match = re.search(r'System ID:\s*\d+\s*\(([^)]+)\)', content)
    if match:
        info['Node Name'] = match.group(1)
    
    match = re.search(r'Cluster Name:\s*(\S+)', content)
    if match:
        info['Cluster Name'] = match.group(1)
    
    return info

def parse_header_data(content):
    info = {
        'Cluster Name': 'N/A'
    }
    
    match = re.search(r'X-Netapp-asup-cluster-name:\s*(.+)', content)
    if match:
        info['Cluster Name'] = match.group(1).strip()
    
    return info

def parse_storage_disk(xml_content):
    disks = []
    try:
        root = ET.fromstring(xml_content.encode('utf-8'))
        for row in root.findall('.//{http://asup_search.netapp.com/ns/ASUP/1.1}ROW'):
            disk_info = {}
            
            model_elem = row.find('ns:model', DISK_NS_MAP)
            disk_model = model_elem.text.strip() if model_elem is not None and model_elem.text else 'N/A'
            
            type_elem = row.find('ns:disk-type', DISK_NS_MAP)
            disk_type = type_elem.text.strip() if type_elem is not None and type_elem.text else 'N/A'
            
            if disk_model == 'PHA-DISK' or disk_type == 'Disk Type':
                continue
            
            disk_info['Disk Model'] = disk_model
            disk_info['Disk Type'] = disk_type
            
            size_elem = row.find('ns:physical-size-mb', DISK_NS_MAP)
            if size_elem is not None and size_elem.text and size_elem.text.strip():
                try:
                    size_mb = float(size_elem.text.strip())
                    disk_info['Disk Size (TB)'] = round(size_mb / 1024 / 1024, 2)
                except:
                    disk_info['Disk Size (TB)'] = 'N/A'
            else:
                disk_info['Disk Size (TB)'] = 'N/A'
            
            fw_elem = row.find('ns:firmware-revision', DISK_NS_MAP)
            disk_info['Firmware Revision'] = fw_elem.text.strip() if fw_elem is not None and fw_elem.text else 'N/A'
            
            disks.append(disk_info)
    except Exception as e:
        raise ValueError('Firmware disk XML parsing failed') from e
    return disks

def parse_storage_shelf(xml_content):
    shelves = []
    try:
        root = ET.fromstring(xml_content.encode('utf-8'))
        for row in root.findall('.//{http://asup_search.netapp.com/ns/ASUP/1.1}ROW'):
            shelf_info = {}
            
            shelf_name_elem = row.find('{http://asup_search.netapp.com/ns/T_STORAGE_SHELF/1.0}shelf_name')
            shelf_info['Shelf Name'] = shelf_name_elem.text.strip() if shelf_name_elem is not None and shelf_name_elem.text else 'N/A'
            
            serial_num_elem = row.find('{http://asup_search.netapp.com/ns/T_STORAGE_SHELF/1.0}serial_number')
            shelf_info['Serial Number'] = serial_num_elem.text.strip() if serial_num_elem is not None and serial_num_elem.text else 'N/A'
            
            product_id_elem = row.find('{http://asup_search.netapp.com/ns/T_STORAGE_SHELF/1.0}product_id')
            shelf_info['Model'] = product_id_elem.text.strip() if product_id_elem is not None and product_id_elem.text else 'N/A'
            
            disk_count_elem = row.find('{http://asup_search.netapp.com/ns/T_STORAGE_SHELF/1.0}disk_count')
            shelf_info['Disk Count'] = disk_count_elem.text.strip() if disk_count_elem is not None and disk_count_elem.text else 'N/A'
            
            controller_elem = row.find('{http://asup_search.netapp.com/ns/T_STORAGE_SHELF/1.0}controller')
            if controller_elem is not None:
                li_list = controller_elem.findall('.//{http://asup_search.netapp.com/ns/ASUP/1.1}li')
                controllers = [li.text.strip() for li in li_list if li.text and li.text.strip()]
                controllers = list(set(controllers))
                shelf_info['Controller Name'] = ', '.join(controllers) if controllers else 'N/A'
            else:
                shelf_info['Controller Name'] = 'N/A'
            
            module_fw_elem = row.find('{http://asup_search.netapp.com/ns/T_STORAGE_SHELF/1.0}module_fw_revision')
            if module_fw_elem is not None:
                li_list = module_fw_elem.findall('.//{http://asup_search.netapp.com/ns/ASUP/1.1}li')
                fw_values = [li.text.strip() for li in li_list if li.text and li.text.strip() != '-']
                shelf_info['Firmware Revision A'] = fw_values[0] if len(fw_values) > 0 else 'N/A'
                shelf_info['Firmware Revision B'] = fw_values[1] if len(fw_values) > 1 else 'N/A'
            else:
                shelf_info['Firmware Revision A'] = 'N/A'
                shelf_info['Firmware Revision B'] = 'N/A'
            
            module_op_elem = row.find('{http://asup_search.netapp.com/ns/T_STORAGE_SHELF/1.0}module_op_status')
            if module_op_elem is not None:
                li_list = module_op_elem.findall('.//{http://asup_search.netapp.com/ns/ASUP/1.1}li')
                status_values = [li.text.strip() for li in li_list if li.text]
                shelf_info['Module Operational Status A'] = status_values[0] if len(status_values) > 0 else 'N/A'
                shelf_info['Module Operational Status B'] = status_values[1] if len(status_values) > 1 else 'N/A'
            else:
                shelf_info['Module Operational Status A'] = 'N/A'
                shelf_info['Module Operational Status B'] = 'N/A'
            
            shelves.append(shelf_info)
    except Exception as e:
        raise ValueError('Firmware shelf XML parsing failed') from e
    return shelves

def parse_switch_config(xml_content):
    switches = []
    try:
        root = ET.fromstring(xml_content.encode('utf-8'))
        
        for row in root.findall('.//{http://asup_search.netapp.com/ns/ASUP/1.1}ROW'):
            switch_info = {}
            
            network_elem = row.find('ns:Switch_Network', SWITCH_NS_MAP)
            switch_network = network_elem.text.strip() if network_elem is not None and network_elem.text else 'N/A'
            
            if 'management' in switch_network.lower():
                continue
            
            device_elem = row.find('ns:Device_Name', SWITCH_NS_MAP)
            switch_info['Device Name'] = device_elem.text.strip() if device_elem is not None and device_elem.text else 'N/A'
            
            ip_elem = row.find('ns:IP_Address', SWITCH_NS_MAP)
            switch_info['IP Address'] = ip_elem.text.strip() if ip_elem is not None and ip_elem.text else 'N/A'
            
            model_elem = row.find('ns:Model_Number', SWITCH_NS_MAP)
            switch_info['Model to display'] = model_elem.text.strip() if model_elem is not None and model_elem.text else 'N/A'
            
            switch_info['Switch Network'] = switch_network
            
            sw_elem = row.find('ns:Software_Version', SWITCH_NS_MAP)
            switch_info['Software Version'] = sw_elem.text.strip() if sw_elem is not None and sw_elem.text else 'N/A'
            
            config_elem = row.find('ns:Config_File_Version', SWITCH_NS_MAP)
            switch_info['Reference Config File Version'] = config_elem.text.strip() if config_elem is not None and config_elem.text else 'N/A'
            
            sn_elem = row.find('ns:Serial_Number', SWITCH_NS_MAP)
            switch_info['Serial Number of the Device'] = sn_elem.text.strip() if sn_elem is not None and sn_elem.text else 'N/A'
            
            switches.append(switch_info)
    except Exception as e:
        raise ValueError('Firmware switch XML parsing failed') from e
    return switches

def parse_sp_info(xml_content):
    sp_records = []
    try:
        root = ET.fromstring(xml_content.encode('utf-8'))
        
        for row in root.findall('.//{http://asup_search.netapp.com/ns/ASUP/1.1}ROW'):
            sp_info = {}
            
            type_elem = row.find('ns:service-processor-type', SP_NS_MAP)
            sp_info['Type'] = type_elem.text.strip() if type_elem is not None and type_elem.text else 'N/A'
            
            status_elem = row.find('ns:status', SP_NS_MAP)
            sp_info['Status'] = status_elem.text.strip() if status_elem is not None and status_elem.text else 'N/A'
            
            ip_elem = row.find('ns:public-ip-address', SP_NS_MAP)
            if ip_elem is not None:
                ip_list = ip_elem.find('asup:list', {'asup': 'http://asup_search.netapp.com/ns/ASUP/1.1'})
                if ip_list is not None:
                    ips = [li.text for li in ip_list.findall('asup:li', {'asup': 'http://asup_search.netapp.com/ns/ASUP/1.1'}) if li.text]
                    sp_info['Public IP Address'] = ', '.join(ips) if ips else 'N/A'
                elif ip_elem.text:
                    sp_info['Public IP Address'] = ip_elem.text.strip()
                else:
                    sp_info['Public IP Address'] = 'N/A'
            else:
                sp_info['Public IP Address'] = 'N/A'
            
            fw_elem = row.find('ns:firmware-version', SP_NS_MAP)
            sp_info['Firmware Version'] = fw_elem.text.strip() if fw_elem is not None and fw_elem.text else 'N/A'
            
            sp_records.append(sp_info)
    except Exception as e:
        raise ValueError('Firmware BMC/SP XML parsing failed') from e
    return sp_records

def extract_disk_type(disk_model):
    match = re.match(r'(X\d+)', disk_model)
    if match:
        return match.group(1)
    return disk_model

def get_ha_base_name(node_name):
    match = re.search(r'(.+)-(\d{2})$', node_name)
    if match:
        return match.group(1)
    return node_name

def deduplicate_shelf_records(shelf_records):
    ha_pairs = {}

    for record in shelf_records:
        node_name = record.get('Node Name', 'N/A')
        ha_base = get_ha_base_name(node_name)
        
        if ha_base not in ha_pairs:
            ha_pairs[ha_base] = {
                'records': [],
                'node_names': set()
            }
        
        ha_pairs[ha_base]['records'].append(record)
        ha_pairs[ha_base]['node_names'].add(node_name)

    result = []
    
    for ha_base, data in ha_pairs.items():
        records = data['records']
        node_names = sorted(data['node_names'])
        
        # 按 Shelf Module Type 和 Shelf Type 分组
        shelf_groups = {}
        for record in records:
            key = (record['Shelf Type'], record['Shelf Module Type'], record['Firmware Revision'])
            if key not in shelf_groups:
                shelf_groups[key] = {
                    'records': [],
                    'counts': []
                }
            shelf_groups[key]['records'].append(record)
            shelf_groups[key]['counts'].append(record['Shelf Count'])
        
        # 为每个组创建一条记录
        for (shelf_type, module_type, fw_rev), group_data in shelf_groups.items():
            # 取 HA 两个节点中较小的 Shelf Count
            shelf_count = min(group_data['counts'])
            
            # 取第一个记录作为模板
            first_record = group_data['records'][0]
            
            merged_record = {
                'Serial Number': first_record['Serial Number'],
                'Model': first_record['Model'],
                'Node Name': '/'.join(node_names),
                'Cluster Name': first_record['Cluster Name'],
                'Shelf Type': shelf_type,
                'Shelf Count': shelf_count,
                'Shelf Module Type': module_type,
                'Firmware Revision': fw_rev
            }
            result.append(merged_record)

    return result

def deduplicate_switch_records(switch_records):
    seen = {}
    result = []

    for record in switch_records:
        key = (record.get('Device Name'), record.get('Serial Number of the Device'))
        
        if key not in seen:
            seen[key] = record
        else:
            existing = seen[key]
            if existing.get('Cluster Name', 'N/A') == 'N/A' and record.get('Cluster Name', 'N/A') != 'N/A':
                seen[key]['Cluster Name'] = record.get('Cluster Name', 'N/A')
            if existing.get('Node Name', 'N/A') == 'N/A' and record.get('Node Name', 'N/A') != 'N/A':
                seen[key]['Node Name'] = record.get('Node Name', 'N/A')

    for value in seen.values():
        result.append(value)

    return result

def auto_adjust_column_width(worksheet, min_width=15, max_width=120):
    for col in worksheet.columns:
        max_length = 0
        column = col[0].column_letter
        for cell in col:
            try:
                if cell.value:
                    text_length = len(str(cell.value))
                    if text_length > max_length:
                        max_length = text_length
            except:
                pass
        adjusted_width = min(max(max_length + 3, min_width), max_width)
        worksheet.column_dimensions[column].width = adjusted_width

def auto_adjust_row_height(worksheet):
    for row in worksheet.iter_rows():
        max_height = 20
        for cell in row:
            if cell.value:
                text_length = len(str(cell.value))
                lines = (text_length // 50) + 1
                cell_height = 20 * lines
                if cell_height > max_height:
                    max_height = cell_height
        worksheet.row_dimensions[row[0].row].height = max_height

def style_worksheet(worksheet, header_color='1E3A5F'):
    auto_adjust_column_width(worksheet)
    
    for row in worksheet.iter_rows(min_row=1, max_row=1):
        for cell in row:
            cell.fill = PatternFill(start_color=header_color, end_color=header_color, fill_type='solid')
            cell.font = Font(color='FFFFFF', bold=True)
            cell.alignment = Alignment(vertical='center', horizontal='left')
    
    for row in worksheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical='center', horizontal='left', wrap_text=True)
    
    auto_adjust_row_height(worksheet)
    
    thin_border = Border(left=Side(style='thin'),
                        right=Side(style='thin'),
                        top=Side(style='thin'),
                        bottom=Side(style='thin'))
    for row in worksheet.iter_rows():
        for cell in row:
            cell.border = thin_border

def process_single_directory(dir_name, input_dir=''):
    print(f"DEBUG: dir_name={repr(dir_name)}, input_dir={repr(input_dir)}")
    if input_dir:
        log_dir = input_dir
    else:
        log_dir = os.path.join(base_log_dir, dir_name)
    print(f"DEBUG: log_dir={repr(log_dir)}")
    output_file = os.path.join(output_dir, f'{dir_name} Firmware Report.xlsx')
    
    print(f"\n=== 处理目录: {dir_name} ===")
    
    disk_records = []
    all_disks_for_summary = []
    shelf_records = []
    sp_records = []
    switch_records = []
    available_sources = set()
    
    files = sorted([f for f in os.listdir(log_dir) if f.endswith('.7z')])
    print(f"找到 {len(files)} 个日志文件")
    
    for i, filename in enumerate(files, 1):
        print(f"  处理 {i}/{len(files)}: {filename}")
        filepath = os.path.join(log_dir, filename)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            sysconfig_content = extract_file(filepath, 'SYSCONFIG-A.txt', tmpdir)
            header_content = extract_file(filepath, 'X-HEADER-DATA.TXT', tmpdir)
            
            controller_info = {'Serial Number': 'N/A', 'Model': 'N/A', 'Node Name': 'N/A', 'Cluster Name': 'N/A'}
            if sysconfig_content:
                controller_info = parse_controller_info(sysconfig_content)
            
            if header_content:
                header_info = parse_header_data(header_content)
                if header_info['Cluster Name'] != 'N/A':
                    controller_info['Cluster Name'] = header_info['Cluster Name']
            
            xml_content = extract_file(filepath, 'storage-disk.xml', tmpdir)
            if xml_content:
                available_sources.add('Disk Firmware')
                disks = parse_storage_disk(xml_content)
                
                disk_summary = {}
                for disk in disks:
                    key = (disk['Disk Model'], disk['Firmware Revision'], disk['Disk Type'], disk['Disk Size (TB)'])
                    if key not in disk_summary:
                        disk_summary[key] = 0
                    disk_summary[key] += 1
                    
                    all_disks_for_summary.append({
                        'Disk Model': disk['Disk Model'],
                        'Disk Type': disk['Disk Type'],
                        'Disk Size (TB)': disk['Disk Size (TB)']
                    })
                
                for (disk_model, fw_rev, disk_type, disk_size), count in disk_summary.items():
                    disk_records.append({
                        'Serial Number': controller_info['Serial Number'],
                        'Model': controller_info['Model'],
                        'Node Name': controller_info['Node Name'],
                        'Cluster Name': controller_info['Cluster Name'],
                        'Disk Model': disk_model,
                        'Disk Count': count,
                        'Disk Type': disk_type,
                        'Disk Size (TB)': disk_size,
                        'Current Version': fw_rev
                    })
            
            shelf_xml_content = extract_file(filepath, 'storage-shelf.xml', tmpdir)
            if shelf_xml_content:
                available_sources.add('Shelf Firmware')
                shelves = parse_storage_shelf(shelf_xml_content)
                for shelf in shelves:
                    shelf_record = shelf.copy()
                    shelf_record['Cluster Name'] = controller_info['Cluster Name']
                    shelf_records.append(shelf_record)
            
            sp_content = extract_file(filepath, 'sp-info.xml', tmpdir)
            if sp_content:
                available_sources.add('BMC&SP')
                sp_info_list = parse_sp_info(sp_content)
                for sp_info in sp_info_list:
                    sp_record = {
                        'Serial Number': controller_info['Serial Number'],
                        'Model': controller_info['Model'],
                        'Node Name': controller_info['Node Name'],
                        'Cluster Name': controller_info['Cluster Name'],
                        'Type': sp_info.get('Type', 'N/A'),
                        'Status': sp_info.get('Status', 'N/A'),
                        'Public IP Address': sp_info.get('Public IP Address', 'N/A'),
                        'Firmware Version': sp_info.get('Firmware Version', 'N/A')
                    }
                    sp_records.append(sp_record)
            
            switch_content = extract_file(filepath, 'cshm-switch-config.xml', tmpdir)
            if switch_content:
                available_sources.add('Switch')
                switches = parse_switch_config(switch_content)
                for switch in switches:
                    switch_record = {
                        'Device Name': switch.get('Device Name', 'N/A'),
                        'IP Address': switch.get('IP Address', 'N/A'),
                        'Model to display': switch.get('Model to display', 'N/A'),
                        'Switch Network': switch.get('Switch Network', 'N/A'),
                        'Software Version': switch.get('Software Version', 'N/A'),
                        'Reference Config File Version': switch.get('Reference Config File Version', 'N/A'),
                        'Serial Number of the Device': switch.get('Serial Number of the Device', 'N/A'),
                        'Cluster Name': controller_info.get('Cluster Name', 'N/A'),
                        'Node Name': controller_info.get('Node Name', 'N/A')
                    }
                    switch_records.append(switch_record)
    
    df_disk = pd.DataFrame(disk_records, columns=[
        'Serial Number', 'Model', 'Node Name', 'Cluster Name', 'Disk Model',
        'Disk Count', 'Disk Type', 'Disk Size (TB)', 'Current Version'])
    df_disk = df_disk[['Serial Number', 'Model', 'Node Name', 'Cluster Name', 'Disk Model',
                       'Disk Count', 'Disk Type', 'Disk Size (TB)', 'Current Version']]
    df_disk = df_disk.sort_values('Cluster Name')
    
    df_summary = pd.DataFrame(all_disks_for_summary, columns=['Disk Model', 'Disk Type', 'Disk Size (TB)'])
    df_summary['Disk Type Code'] = df_summary['Disk Model'].apply(extract_disk_type)
    summary_grouped = df_summary.groupby(['Disk Type Code', 'Disk Type', 'Disk Size (TB)']).size().reset_index(name='Total Count')
    summary_grouped = summary_grouped.sort_values('Disk Size (TB)')
    
    # 去重：按Serial Number去重
    if shelf_records:
        df_shelf = pd.DataFrame(shelf_records)
        # 先按照需要的列顺序重新排列
        df_shelf = df_shelf[['Cluster Name', 'Controller Name', 'Shelf Name', 'Serial Number',
                            'Model', 'Disk Count', 
                            'Firmware Revision A', 'Firmware Revision B',
                            'Module Operational Status A', 'Module Operational Status B']]
        # 基于Serial Number去重，保留第一条记录
        df_shelf = df_shelf.drop_duplicates(subset=['Serial Number'], keep='first')
        # 排序：先按Cluster Name，然后按Controller Name
        df_shelf = df_shelf.sort_values(['Cluster Name', 'Controller Name'])
        
        # 创建Shelf Summary汇总表
        df_shelf_summary = df_shelf.copy()
        df_shelf_summary['Disk Count'] = pd.to_numeric(df_shelf_summary['Disk Count'], errors='coerce').fillna(0).astype(int)
        
        # 定义各Model的总槽位数
        total_slots_map = {
            'DS212': 12,
            'DS224': 24,
            'DS460C': 60,
            'NS224': 24,
            'FS4483NSM16E': 48
        }
        
        def get_total_slots(model):
            for key, slots in total_slots_map.items():
                if key in str(model):
                    return slots
            return None
        
        df_shelf_summary['Total Slots'] = df_shelf_summary['Model'].apply(get_total_slots)
        df_shelf_summary['Empty Slots'] = df_shelf_summary.apply(
            lambda row: (row['Total Slots'] - row['Disk Count']) if pd.notna(row['Total Slots']) else None, 
            axis=1
        )
        
        shelf_summary_grouped = df_shelf_summary.groupby('Model').agg({
            'Shelf Name': 'count',
            'Disk Count': 'sum',
            'Empty Slots': 'sum'
        }).reset_index()
        shelf_summary_grouped.columns = ['Model', 'Shelf Count', 'Total Disk Count', 'Total Empty Slots']
        shelf_summary_grouped = shelf_summary_grouped.sort_values('Model')
    else:
        df_shelf = pd.DataFrame(columns=[
            'Cluster Name', 'Controller Name', 'Shelf Name', 'Serial Number',
            'Model', 'Disk Count', 'Firmware Revision A', 'Firmware Revision B',
            'Module Operational Status A', 'Module Operational Status B'])
        shelf_summary_grouped = pd.DataFrame(columns=['Model', 'Shelf Count', 'Total Disk Count', 'Total Empty Slots'])
    
    switch_deduped = deduplicate_switch_records(switch_records)
    df_switch = pd.DataFrame(switch_deduped, columns=[
        'Device Name', 'IP Address', 'Model to display', 'Switch Network',
        'Software Version', 'Reference Config File Version', 'Serial Number of the Device',
        'Cluster Name', 'Node Name'])
    if not df_switch.empty:
        df_switch = df_switch[['Device Name', 'IP Address', 'Model to display', 'Switch Network',
                               'Software Version', 'Reference Config File Version', 'Serial Number of the Device',
                               'Cluster Name', 'Node Name']]
        df_switch = df_switch.sort_values('Cluster Name')
    
    df_sp = pd.DataFrame(sp_records, columns=[
        'Serial Number', 'Model', 'Node Name', 'Cluster Name', 'Type', 'Status',
        'Public IP Address', 'Firmware Version'])
    if df_sp.empty:
        df_sp['Latest Firmware'] = pd.Series(dtype='object')
    if not df_sp.empty:
        df_sp = df_sp[['Serial Number', 'Model', 'Node Name', 'Cluster Name',
                       'Type', 'Status', 'Public IP Address', 'Firmware Version']]
        # Add Latest Firmware column
        fw_lookup = get_bmc_firmware_lookup()
        df_sp['Latest Firmware'] = df_sp.apply(
            lambda row: get_latest_firmware(row['Firmware Version'], row.get('Type', ''), fw_lookup),
            axis=1
        )
        df_sp = df_sp.sort_values('Cluster Name')
    
    print(f"  生成文件: {dir_name} Firmware Report.xlsx")
    print(f"  Disk记录: {len(df_disk)}, 汇总: {len(summary_grouped)}, Shelf: {len(df_shelf)}, Switch: {len(df_switch) if not df_switch.empty else 0}, BMC: {len(df_sp) if not df_sp.empty else 0}")
    
    wb = Workbook()
    del wb['Sheet']
    
    ws_disk = wb.create_sheet('Disk Firmware')
    
    ws_disk.cell(row=1, column=1, value='Disk Type Summary by Model').font = Font(bold=True, size=12)
    
    for r_idx, row in enumerate(dataframe_to_rows(summary_grouped, index=False, header=True), 2):
        for c_idx, value in enumerate(row, 1):
            ws_disk.cell(row=r_idx, column=c_idx, value=value)
    
    detail_start_row = len(summary_grouped) + 6
    
    for r_idx, row in enumerate(dataframe_to_rows(df_disk, index=False, header=True), detail_start_row):
        for c_idx, value in enumerate(row, 1):
            ws_disk.cell(row=r_idx, column=c_idx, value=value)
    
    auto_adjust_column_width(ws_disk)
    
    summary_header_row = 2
    for col_idx in range(1, len(summary_grouped.columns) + 1):
        cell = ws_disk.cell(row=summary_header_row, column=col_idx)
        cell.fill = PatternFill(start_color='1E3A5F', end_color='1E3A5F', fill_type='solid')
        cell.font = Font(color='FFFFFF', bold=True)
        cell.alignment = Alignment(vertical='center', horizontal='left')
    
    for row_idx in range(2, len(summary_grouped) + 3):
        for col_idx in range(1, len(summary_grouped.columns) + 1):
            cell = ws_disk.cell(row=row_idx, column=col_idx)
            cell.alignment = Alignment(vertical='center', horizontal='left', wrap_text=False)
    
    detail_header_row = detail_start_row
    for col_idx in range(1, len(df_disk.columns) + 1):
        cell = ws_disk.cell(row=detail_header_row, column=col_idx)
        cell.fill = PatternFill(start_color='1E3A5F', end_color='1E3A5F', fill_type='solid')
        cell.font = Font(color='FFFFFF', bold=True)
        cell.alignment = Alignment(vertical='center', horizontal='left')
    
    for row_idx in range(detail_start_row + 1, detail_start_row + len(df_disk) + 1):
        for col_idx in range(1, len(df_disk.columns) + 1):
            cell = ws_disk.cell(row=row_idx, column=col_idx)
            cell.alignment = Alignment(vertical='center', horizontal='left', wrap_text=False)
    
    thin_border = Border(left=Side(style='thin'),
                        right=Side(style='thin'),
                        top=Side(style='thin'),
                        bottom=Side(style='thin'))
    for row in ws_disk.iter_rows():
        for cell in row:
            cell.border = thin_border
        ws_disk.row_dimensions[row[0].row].height = 18
    
    if 'Disk Firmware' not in available_sources:
        wb.remove(ws_disk)

    if 'Shelf Firmware' in available_sources:
        ws_shelf = wb.create_sheet('Shelf Firmware')
        
        ws_shelf.cell(row=1, column=1, value='Shelf Summary by Model').font = Font(bold=True, size=12)
        
        for r_idx, row in enumerate(dataframe_to_rows(shelf_summary_grouped, index=False, header=True), 2):
            for c_idx, value in enumerate(row, 1):
                ws_shelf.cell(row=r_idx, column=c_idx, value=value)
        
        summary_start_row = 2
        summary_header_row = 2
        for col_idx in range(1, len(shelf_summary_grouped.columns) + 1):
            cell = ws_shelf.cell(row=summary_header_row, column=col_idx)
            cell.fill = PatternFill(start_color='1E3A5F', end_color='1E3A5F', fill_type='solid')
            cell.font = Font(color='FFFFFF', bold=True)
            cell.alignment = Alignment(vertical='center', horizontal='left')
        
        for row_idx in range(2, len(shelf_summary_grouped) + 3):
            for col_idx in range(1, len(shelf_summary_grouped.columns) + 1):
                cell = ws_shelf.cell(row=row_idx, column=col_idx)
                cell.alignment = Alignment(vertical='center', horizontal='left', wrap_text=False)
        
        detail_start_row = len(shelf_summary_grouped) + 6
        
        for r_idx, row in enumerate(dataframe_to_rows(df_shelf, index=False, header=True), detail_start_row):
            for c_idx, value in enumerate(row, 1):
                ws_shelf.cell(row=r_idx, column=c_idx, value=value)
        
        detail_header_row = detail_start_row
        for col_idx in range(1, len(df_shelf.columns) + 1):
            cell = ws_shelf.cell(row=detail_header_row, column=col_idx)
            cell.fill = PatternFill(start_color='1E3A5F', end_color='1E3A5F', fill_type='solid')
            cell.font = Font(color='FFFFFF', bold=True)
            cell.alignment = Alignment(vertical='center', horizontal='left')
        
        for row_idx in range(detail_start_row + 1, detail_start_row + len(df_shelf) + 1):
            for col_idx in range(1, len(df_shelf.columns) + 1):
                cell = ws_shelf.cell(row=row_idx, column=col_idx)
                cell.alignment = Alignment(vertical='center', horizontal='left', wrap_text=False)
        
        for row in ws_shelf.iter_rows():
            for cell in row:
                cell.border = thin_border
            ws_shelf.row_dimensions[row[0].row].height = 18
        
        auto_adjust_column_width(ws_shelf)
    
    if 'Switch' in available_sources:
        ws_switch = wb.create_sheet('Switch')
        
        for r_idx, row in enumerate(dataframe_to_rows(df_switch, index=False, header=True), 1):
            for c_idx, value in enumerate(row, 1):
                ws_switch.cell(row=r_idx, column=c_idx, value=value)
        
        style_worksheet(ws_switch)
    
    if 'BMC&SP' in available_sources:
        ws_sp = wb.create_sheet('BMC&SP')
        
        for r_idx, row in enumerate(dataframe_to_rows(df_sp, index=False, header=True), 1):
            for c_idx, value in enumerate(row, 1):
                ws_sp.cell(row=r_idx, column=c_idx, value=value)
        
        style_worksheet(ws_sp)
    
    if not wb.sheetnames:
        status = wb.create_sheet('Firmware Source Status')
        status.append(['Status', 'Reason'])
        status.append(['Skipped', 'All four firmware source files are absent; no firmware values were inferred.'])
        style_worksheet(status)
    wb.save(output_file)
    print(f"  已保存: {output_file}")

def main():
    os.makedirs(output_dir, exist_ok=True)
    
    if len(sys.argv) > 1:
        input_dir = sys.argv[1]
        dir_name = os.path.basename(input_dir)
        # Extract meaningful customer name from full path
        path_lower = input_dir.lower()
        dir_name = firmware_name(input_dir)
        dir_name = os.environ.get('SAM_REPORT_CUSTOMER') or dir_name
        print(f"=== 处理指定目录: {dir_name} ===")
        process_single_directory(dir_name, input_dir)
    else:
        subdirs = [d for d in os.listdir(base_log_dir) if os.path.isdir(os.path.join(base_log_dir, d))]
        print(f"=== 开始处理 {len(subdirs)} 个目录 ===")
        for subdir in sorted(subdirs):
            process_single_directory(subdir)
    
    print("\n=== 全部处理完成 ===")

if __name__ == '__main__':
    main()
