from pathlib import Path
import os
import sys
_package_shared = os.environ.get('SAM_SHARED_SKILL_DIR') or str(Path(__file__).resolve().parents[1] / 'NetApp-SAM-Report')
if not Path(_package_shared).is_absolute() or not (Path(_package_shared) / 'sam_package_compat.py').is_file():
    raise FileNotFoundError('Explicit candidate shared runtime directory is required')
sys.path.insert(0, _package_shared)
from sam_package_compat import package_path

import os
import tempfile
import subprocess
import xml.etree.ElementTree as ET
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
import gzip
import re


def get_cluster_name(seven_zip_path, seven_zip_exe):
    cluster_name = "Unknown"
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            subprocess.run([seven_zip_exe, 'e', '-o' + tmpdir, seven_zip_path, 'CLUSTER-INFO.xml', '-y'],
                          capture_output=True, text=True, timeout=120)
            
            xml_file_path = os.path.join(tmpdir, 'CLUSTER-INFO.xml')
            if os.path.exists(xml_file_path):
                with open(xml_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    xml_content = f.read()
                
                root = ET.fromstring(xml_content)
                namespaces = {
                    'asup': 'http://asup_search.netapp.com/ns/ASUP/1.1',
                    'ci': 'http://asup_search.netapp.com/ns/CLUSTER_INFO/1.0'
                }
                
                for row in root.findall('.//asup:ROW', namespaces):
                    cluster_name_elem = row.find('ci:cluster-name', namespaces)
                    if cluster_name_elem is not None and cluster_name_elem.text:
                        cluster_name = cluster_name_elem.text
                        break
        except Exception as e:
            print(f"Error getting cluster name: {e}")
    
    return cluster_name


def extract_network_interface_info(xml_content, cluster_name):
    interfaces = []
    try:
        root = ET.fromstring(xml_content)
        
        namespaces = {
            'asup': 'http://asup_search.netapp.com/ns/ASUP/1.1',
            'vif': 'http://asup_search.netapp.com/ns/T_VIF/1.0'
        }
        
        for row in root.findall('.//asup:ROW', namespaces):
            vserver_elem = row.find('vif:vserver', namespaces)
            vserver_name = vserver_elem.text if vserver_elem is not None else ''
            
            if vserver_name.lower() == 'cluster':
                continue
            
            logical_elem = row.find('vif:vif', namespaces)
            logical_name = logical_elem.text if logical_elem is not None else ''
            
            home_node_elem = row.find('vif:home_node', namespaces)
            home_node = home_node_elem.text if home_node_elem is not None else ''
            
            home_port_elem = row.find('vif:home_port', namespaces)
            home_port = home_port_elem.text if home_port_elem is not None else ''
            
            is_home_elem = row.find('vif:is_home', namespaces)
            is_home = is_home_elem.text if is_home_elem is not None else ''
            
            address_elem = row.find('vif:address', namespaces)
            network_address = address_elem.text if address_elem is not None else ''
            
            netmask_elem = row.find('vif:netmask', namespaces)
            netmask = netmask_elem.text if netmask_elem is not None else ''
            
            role_elem = row.find('vif:role', namespaces)
            role = role_elem.text if role_elem is not None else ''
            
            if vserver_name or logical_name or network_address:
                interfaces.append({
                    'Cluster Name': cluster_name,
                    'Vserver Name': vserver_name,
                    'Logical Interface Name': logical_name,
                    'Home Node': home_node,
                    'Home Port': home_port,
                    'Is Home': is_home,
                    'Network Address': network_address,
                    'Netmask': netmask,
                    '(DEPRECATED)-Role': role
                })
    except Exception as e:
        print(f"Error parsing network-interface.xml: {e}")
    
    return interfaces


def extract_sp_network_info(xml_content, cluster_name, filename):
    sp_interfaces = []
    try:
        root = ET.fromstring(xml_content)
        
        namespaces = {
            'asup': 'http://asup_search.netapp.com/ns/ASUP/1.1',
            'sp': 'http://asup_search.netapp.com/ns/T_SPNETWORK/1.0'
        }
        
        node_name = 'N/A'
        if filename.startswith('ASUP_'):
            parts = filename[5:].split('_')
            if len(parts) >= 3:
                node_name = '_'.join(parts[:-2])
        
        node_sn = root.attrib.get('node_sn', '')
        
        for row in root.findall('.//asup:ROW', namespaces):
            address_type_elem = row.find('sp:address-type', namespaces)
            address_type = address_type_elem.text if address_type_elem is not None else ''
            
            if address_type != 'IPv4':
                continue
            
            ip_address_elem = row.find('sp:ip-address', namespaces)
            ip_address = ip_address_elem.text if ip_address_elem is not None else ''
            
            netmask_elem = row.find('sp:netmask', namespaces)
            netmask = netmask_elem.text if netmask_elem is not None else ''
            
            sp_interfaces.append({
                'Cluster Name': cluster_name,
                'Vserver Name': 'N/A',
                'Logical Interface Name': 'SP-' + node_sn if node_sn else 'SP',
                'Home Node': node_name,
                'Home Port': 'N/A',
                'Is Home': 'N/A',
                'Network Address': ip_address if ip_address else 'N/A',
                'Netmask': netmask if netmask else 'N/A',
                '(DEPRECATED)-Role': 'SP'
            })
    except Exception as e:
        print(f"Error parsing sp-network-info.xml: {e}")
    
    return sp_interfaces


def extract_network_ports_info(xml_content, cluster_name):
    ports = []
    port_role_map = {}
    try:
        root = ET.fromstring(xml_content)
        
        namespaces = {
            'asup': 'http://asup_search.netapp.com/ns/ASUP/1.1',
            'np': 'http://asup_search.netapp.com/ns/T_PORT/1.0'
        }
        
        for row in root.findall('.//asup:ROW', namespaces):
            role_elem = row.find('np:role', namespaces)
            role = role_elem.text if role_elem is not None else ''
            
            if role.lower() == 'cluster':
                continue
            
            link_elem = row.find('np:link', namespaces)
            link = link_elem.text if link_elem is not None else ''
            
            node_elem = row.find('np:node', namespaces)
            node = node_elem.text if node_elem is not None else ''
            
            port_elem = row.find('np:port', namespaces)
            port = port_elem.text if port_elem is not None else ''
            
            mtu_elem = row.find('np:mtu', namespaces)
            mtu = mtu_elem.text if mtu_elem is not None else ''
            
            ifgrp_elem = row.find('np:ifgrp', namespaces)
            ifgrp = ifgrp_elem.text if ifgrp_elem is not None else ''
            
            type_elem = row.find('np:type', namespaces)
            port_type = type_elem.text if type_elem is not None else ''
            
            speed_elem = row.find('np:speed-actual', namespaces)
            speed = speed_elem.text if speed_elem is not None else ''
            
            bd_elem = row.find('np:broadcast-domain', namespaces)
            broadcast_domain = bd_elem.text if bd_elem is not None else ''
            
            remote_elem = row.find('np:remote-device-id', namespaces)
            remote_device_id = remote_elem.text if remote_elem is not None else ''
            
            port_role_map[(node, port)] = role
            
            if link.lower() != 'down' and (node or port):
                ports.append({
                    'Cluster Name': cluster_name,
                    'Node': node,
                    'Port': port,
                    '(DEPRECATED)-Role': role,
                    'Link': link,
                    'MTU': mtu,
                    'Interface Group Owner': ifgrp if ifgrp else 'N/A',
                    'Port Type': port_type,
                    'Speed': speed if speed else 'N/A',
                    'Broadcast Domain': broadcast_domain if broadcast_domain else 'N/A',
                    'Remote Device ID': remote_device_id if remote_device_id else 'N/A'
                })
    except Exception as e:
        print(f"Error parsing network-ports.xml: {e}")
    
    return ports, port_role_map


def extract_ifgrp_info(xml_content, cluster_name):
    ifgrps = []
    try:
        root = ET.fromstring(xml_content)
        
        namespaces = {
            'asup': 'http://asup_search.netapp.com/ns/ASUP/1.1',
            'ifg': 'http://asup_search.netapp.com/ns/T_IFGRP/1.0'
        }
        
        for row in root.findall('.//asup:ROW', namespaces):
            node_elem = row.find('ifg:node', namespaces)
            node = node_elem.text if node_elem is not None else ''
            
            ifgrp_name_elem = row.find('ifg:ifgrp-name', namespaces)
            ifgrp_name = ifgrp_name_elem.text if ifgrp_name_elem is not None else ''
            
            distr_func_elem = row.find('ifg:distr-func', namespaces)
            distr_func = distr_func_elem.text if distr_func_elem is not None else ''
            
            lacp_elem = row.find('ifg:lacp', namespaces)
            lacp = lacp_elem.text if lacp_elem is not None else ''
            
            mac_elem = row.find('ifg:mac', namespaces)
            mac = mac_elem.text if mac_elem is not None else ''
            
            activeports_elem = row.find('ifg:activeports', namespaces)
            activeports = activeports_elem.text if activeports_elem is not None else ''
            
            ports_elem = row.find('ifg:ports', namespaces)
            if ports_elem is not None:
                ports_list = []
                for li in ports_elem.findall('.//asup:li', namespaces):
                    if li.text:
                        ports_list.append(li.text)
                ports_str = ', '.join(ports_list) if ports_list else 'N/A'
            else:
                ports_str = 'N/A'
            
            up_ports_elem = row.find('ifg:up-ports', namespaces)
            if up_ports_elem is not None:
                up_ports_list = []
                for li in up_ports_elem.findall('.//asup:li', namespaces):
                    if li.text:
                        up_ports_list.append(li.text)
                up_ports_str = ', '.join(up_ports_list) if up_ports_list else 'N/A'
            else:
                up_ports_str = 'N/A'
            
            down_ports_elem = row.find('ifg:down-ports', namespaces)
            if down_ports_elem is not None:
                down_ports_list = []
                for li in down_ports_elem.findall('.//asup:li', namespaces):
                    if li.text:
                        down_ports_list.append(li.text)
                down_ports_str = ', '.join(down_ports_list) if down_ports_list else 'N/A'
            else:
                down_ports_str = 'N/A'
            
            if node or ifgrp_name:
                ifgrps.append({
                    'Cluster Name': cluster_name,
                    'Node': node,
                    'IFGRP Name': ifgrp_name,
                    'Distribution Function': distr_func,
                    'LACP': lacp,
                    'MAC': mac,
                    'Active Ports': activeports,
                    'Ports': ports_str,
                    'Up Ports': up_ports_str,
                    'Down Ports': down_ports_str
                })
    except Exception as e:
        print(f"Error parsing ifgrps.xml: {e}")
    
    return ifgrps


def extract_device_discovery_info(xml_content, cluster_name, port_role_map):
    discoveries = []
    try:
        root = ET.fromstring(xml_content)
        
        namespaces = {
            'asup': 'http://asup_search.netapp.com/ns/ASUP/1.1',
            'dd': 'http://asup_search.netapp.com/ns/T_NET_DEVICE_DISCOVERY/1.0'
        }
        
        for row in root.findall('.//asup:ROW', namespaces):
            node_elem = row.find('dd:node', namespaces)
            node = node_elem.text if node_elem is not None else ''
            
            protocol_elem = row.find('dd:protocol', namespaces)
            protocol = protocol_elem.text if protocol_elem is not None else ''
            
            port_elem = row.find('dd:port', namespaces)
            port = port_elem.text if port_elem is not None else ''
            
            discovered_device_elem = row.find('dd:discovered-device', namespaces)
            discovered_device = discovered_device_elem.text if discovered_device_elem is not None else ''
            
            interface_elem = row.find('dd:interface', namespaces)
            discovered_interface = interface_elem.text if interface_elem is not None else ''
            
            device_ip_elem = row.find('dd:device-ip', namespaces)
            if device_ip_elem is not None:
                ip_list = []
                for li in device_ip_elem.findall('.//asup:li', namespaces):
                    if li.text:
                        ip_list.append(li.text)
                device_ip = ', '.join(ip_list) if ip_list else 'N/A'
            else:
                device_ip = 'N/A'
            
            platform_elem = row.find('dd:platform', namespaces)
            platform = platform_elem.text if platform_elem is not None else 'N/A'
            
            version_elem = row.find('dd:version', namespaces)
            version = version_elem.text if version_elem is not None else 'N/A'
            
            port_role = port_role_map.get((node, port), 'N/A')
            
            if node and port:
                discoveries.append({
                    'Cluster Name': cluster_name,
                    'Node': node,
                    'Protocol': protocol,
                    'Port': port,
                    '(DEPRECATED)-Role': port_role,
                    'Discovered Device': discovered_device if discovered_device else 'N/A',
                    'Discovered Device Interface': discovered_interface if discovered_interface else 'N/A',
                    'Discovered Device IP Addresses': device_ip,
                    'Discovered Device Platform': platform,
                    'Discovered Device Version': version if version else 'N/A'
                })
    except Exception as e:
        print(f"Error parsing device-discovery.xml: {e}")
    
    return discoveries


def extract_ems_alerts(log_content, cluster_name, node_name):
    alerts = []
    try:
        priority_map = {
            '0': 'Emergency',
            '1': 'Alert',
            '2': 'Critical',
            '3': 'Error',
            '4': 'Warning',
            '5': 'Notice',
            '6': 'Info',
            '7': 'Debug'
        }
        
        lr_pattern = re.compile(r'<LR\s+([^>]+)>(.*?)</LR>', re.DOTALL)
        matches = lr_pattern.findall(log_content)
        
        for attrs_str, inner_content in matches:
            p_match = re.search(r'p="([^"]*)"', attrs_str)
            d_match = re.search(r'd="([^"]*)"', attrs_str)
            n_match = re.search(r'n="([^"]*)"', attrs_str)
            o_match = re.search(r'o="([^"]*)"', attrs_str)
            
            priority = p_match.group(1) if p_match else ''
            date = d_match.group(1) if d_match else ''
            node = n_match.group(1) if n_match else ''
            thread = o_match.group(1) if o_match else ''
            
            if priority not in ['0', '1', '2', '3']:
                continue
            
            inner_content = inner_content.strip()
            inner_content = re.sub(r'\s+', ' ', inner_content)
            
            alerts.append({
                'Cluster Name': cluster_name,
                'Node Name': node if node else node_name,
                '时间': date,
                '告警级别': priority_map.get(priority, 'Unknown'),
                'Thread name': thread,
                '告警内容': inner_content[:1000]
            })
    except Exception as e:
        print(f"Error parsing EMS-LOG-FILE: {e}")
    
    return alerts


def sort_interfaces(interfaces):
    role_priority = {
        'data': 1,
        'cluster-mgmt': 2,
        'node-mgmt': 3,
        'SP': 4
    }
    
    def sort_key(iface):
        cluster = iface.get('Cluster Name', '')
        role = iface.get('(DEPRECATED)-Role', '')
        role_prio = role_priority.get(role, 99)
        return (cluster, role_prio, role)
    
    return sorted(interfaces, key=sort_key)


def sort_ports(ports):
    role_priority = {
        'data': 1,
        'node-mgmt': 2,
        'cluster-mgmt': 3
    }
    
    def sort_key(port):
        cluster = port.get('Cluster Name', '')
        node = port.get('Node', '')
        role = port.get('(DEPRECATED)-Role', '')
        role_prio = role_priority.get(role, 99)
        return (cluster, node, role_prio, port.get('Port', ''))
    
    return sorted(ports, key=sort_key)


def sort_ifgrps(ifgrps):
    def sort_key(ifg):
        cluster = ifg.get('Cluster Name', '')
        node = ifg.get('Node', '')
        ifgrp_name = ifg.get('IFGRP Name', '')
        return (cluster, node, ifgrp_name)
    
    return sorted(ifgrps, key=sort_key)


def sort_alerts(alerts):
    priority_order = {
        'Emergency': 0,
        'Alert': 1,
        'Critical': 2,
        'Error': 3,
        'Unknown': 999
    }
    
    def alert_sort_key(alert):
        cluster = alert.get('Cluster Name', '')
        priority = alert.get('告警级别', 'Unknown')
        time = alert.get('时间', '')
        return (cluster, priority_order.get(priority, 999), time)
    
    return sorted(alerts, key=alert_sort_key)


def sort_discoveries(discoveries):
    def sort_key(d):
        cluster = d.get('Cluster Name', '')
        node = d.get('Node', '')
        port = d.get('Port', '')
        return (cluster, node, port)
    
    return sorted(discoveries, key=sort_key)


def add_sheet_with_data(wb, sheet_name, headers, data, priority_column_name=None):
    ws = wb.create_sheet(title=sheet_name)
    
    header_fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    header_font = Font(bold=True, color='FFFFFF')
    center_alignment = Alignment(horizontal='center')
    left_alignment = Alignment(horizontal='left', vertical='top', wrap_text=True)
    thin_border = Border(left=Side(style='thin'),
                         right=Side(style='thin'),
                         top=Side(style='thin'),
                         bottom=Side(style='thin'))
    
    priority_column_idx = None
    if priority_column_name and priority_column_name in headers:
        priority_column_idx = headers.index(priority_column_name) + 1
    
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center_alignment
        cell.border = thin_border
    
    for row_idx, item in enumerate(data, 2):
        for col_idx, header in enumerate(headers, 1):
            cell_value = item.get(header, '')
            cell = ws.cell(row=row_idx, column=col_idx, value=cell_value)
            cell.border = thin_border
            cell.alignment = left_alignment
            
            if priority_column_idx and col_idx == priority_column_idx:
                priority_value = item.get(priority_column_name, '')
                if priority_value == 'Emergency':
                    cell.fill = PatternFill(start_color='FF0000', end_color='FF0000', fill_type='solid')
                    cell.font = Font(bold=True, color='FFFFFF')
                elif priority_value in ['Alert', 'Critical']:
                    cell.fill = PatternFill(start_color='FFFF00', end_color='FFFF00', fill_type='solid')
                    cell.font = Font(bold=True, color='000000')
    
    column_widths = [len(header) * 1.5 for header in headers]
    
    for row in ws.iter_rows():
        for idx, cell in enumerate(row):
            if cell.value is not None:
                cell_str = str(cell.value)
                cell_length = 0
                for c in cell_str:
                    if ord(c) > 127:
                        cell_length += 2
                    else:
                        cell_length += 1
                cell_length = cell_length * 1.2
                if cell_length > column_widths[idx]:
                    column_widths[idx] = cell_length
    
    for col, width in enumerate(column_widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = min(max(width, 12), 60)
    
    for row_idx in range(1, ws.max_row + 1):
        max_line_count = 1
        for col_idx in range(1, ws.max_column + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            if cell.value is not None:
                cell_str = str(cell.value)
                newline_count = cell_str.count('\n')
                col_width = ws.column_dimensions[get_column_letter(col_idx)].width
                estimated_lines = max(1, int(len(cell_str) / (col_width * 0.8)) + 1)
                max_line_count = max(max_line_count, newline_count + estimated_lines)
        
        ws.row_dimensions[row_idx].height = min(max(15, 15 * max_line_count), 100)


def create_excel_report(interfaces_data, ports_data, ifgrps_data, discoveries_data, alerts_data, output_path):
    wb = Workbook()
    
    default_sheet = wb.active
    wb.remove(default_sheet)
    
    ni_headers = ['Cluster Name', 'Vserver Name', 'Logical Interface Name', 'Home Node', 'Home Port',
                  'Is Home', 'Network Address', 'Netmask', '(DEPRECATED)-Role']
    add_sheet_with_data(wb, 'Network Interfaces', ni_headers, interfaces_data)
    
    np_headers = ['Cluster Name', 'Node', 'Port', '(DEPRECATED)-Role', 'Link', 'MTU',
                  'Interface Group Owner', 'Port Type', 'Speed', 'Broadcast Domain', 'Remote Device ID']
    add_sheet_with_data(wb, 'Network Port', np_headers, ports_data)
    
    ifgrp_headers = ['Cluster Name', 'Node', 'IFGRP Name', 'Distribution Function', 'LACP',
                     'MAC', 'Active Ports', 'Ports', 'Up Ports', 'Down Ports']
    add_sheet_with_data(wb, 'IFGRP', ifgrp_headers, ifgrps_data)
    
    discovery_headers = ['Cluster Name', 'Node', 'Protocol', 'Port', '(DEPRECATED)-Role',
                         'Discovered Device', 'Discovered Device Interface',
                         'Discovered Device IP Addresses', 'Discovered Device Platform',
                         'Discovered Device Version']
    add_sheet_with_data(wb, 'Network Discovery', discovery_headers, discoveries_data)
    
    alert_headers = ['Cluster Name', 'Node Name', '时间', '告警级别', 'Thread name', '告警内容']
    add_sheet_with_data(wb, 'EMS告警', alert_headers, alerts_data, priority_column_name='告警级别')
    
    if os.path.exists(output_path):
        try:
            os.remove(output_path)
        except:
            pass
    
    wb.save(output_path)
    print(f"Report saved to {output_path}")


def process_directory(asup_subdir, output_dir, seven_zip_exe):
    dir_name = os.path.basename(asup_subdir)
    print(f"\n{'='*60}")
    print(f"Processing directory: {dir_name}")
    print(f"{'='*60}")
    
    all_interfaces = []
    all_ports = []
    all_ifgrps = []
    all_discoveries = []
    all_alerts = []
    port_role_map = {}
    
    seven_zip_files = [f for f in os.listdir(asup_subdir) if f.endswith('.7z')]
    print(f"Found {len(seven_zip_files)} 7z files")
    
    for idx, filename in enumerate(seven_zip_files):
        seven_zip_path = os.path.join(asup_subdir, filename)
        print(f"\nProcessing {filename} ({idx+1}/{len(seven_zip_files)})...")
        
        try:
            cluster_name = get_cluster_name(seven_zip_path, seven_zip_exe)
            print(f"  Cluster Name: {cluster_name}")
            
            node_name = 'N/A'
            if filename.startswith('ASUP_'):
                parts = filename[5:].split('_')
                if len(parts) >= 3:
                    node_name = '_'.join(parts[:-2])
            
            with tempfile.TemporaryDirectory() as tmpdir:
                subprocess.run([seven_zip_exe, 'e', '-o' + tmpdir, seven_zip_path, 
                              'network-interface.xml', 'sp-network-info.xml', 'network-ports.xml', 
                              'ifgrps.xml', 'device-discovery.xml', 'EMS-LOG-FILE.gz', '-y'],
                              capture_output=True, text=True, timeout=120)
                
                ni_xml_path = os.path.join(tmpdir, 'network-interface.xml')
                if os.path.exists(ni_xml_path):
                    with open(ni_xml_path, 'r', encoding='utf-8', errors='ignore') as f:
                        xml_content = f.read()
                    
                    interfaces = extract_network_interface_info(xml_content, cluster_name)
                    all_interfaces.extend(interfaces)
                    print(f"  Found {len(interfaces)} network interfaces")
                
                sp_xml_path = os.path.join(tmpdir, 'sp-network-info.xml')
                if os.path.exists(sp_xml_path):
                    with open(sp_xml_path, 'r', encoding='utf-8', errors='ignore') as f:
                        xml_content = f.read()
                    
                    sp_interfaces = extract_sp_network_info(xml_content, cluster_name, filename)
                    all_interfaces.extend(sp_interfaces)
                    print(f"  Found {len(sp_interfaces)} SP interfaces")
                
                np_xml_path = os.path.join(tmpdir, 'network-ports.xml')
                if os.path.exists(np_xml_path):
                    with open(np_xml_path, 'r', encoding='utf-8', errors='ignore') as f:
                        xml_content = f.read()
                    
                    ports, pr_map = extract_network_ports_info(xml_content, cluster_name)
                    all_ports.extend(ports)
                    port_role_map.update(pr_map)
                    print(f"  Found {len(ports)} network ports (link=up only)")
                
                ifgrp_xml_path = os.path.join(tmpdir, 'ifgrps.xml')
                if os.path.exists(ifgrp_xml_path):
                    with open(ifgrp_xml_path, 'r', encoding='utf-8', errors='ignore') as f:
                        xml_content = f.read()
                    
                    ifgrps = extract_ifgrp_info(xml_content, cluster_name)
                    all_ifgrps.extend(ifgrps)
                    print(f"  Found {len(ifgrps)} IFGRPs")
                
                dd_xml_path = os.path.join(tmpdir, 'device-discovery.xml')
                if os.path.exists(dd_xml_path):
                    with open(dd_xml_path, 'r', encoding='utf-8', errors='ignore') as f:
                        xml_content = f.read()
                    
                    discoveries = extract_device_discovery_info(xml_content, cluster_name, port_role_map)
                    all_discoveries.extend(discoveries)
                    print(f"  Found {len(discoveries)} device discoveries")
                
                ems_gz_path = os.path.join(tmpdir, 'EMS-LOG-FILE.gz')
                if os.path.exists(ems_gz_path):
                    try:
                        with gzip.open(ems_gz_path, 'rt', encoding='utf-8', errors='ignore') as f:
                            log_content = f.read()
                        
                        alerts = extract_ems_alerts(log_content, cluster_name, node_name)
                        all_alerts.extend(alerts)
                        print(f"  Found {len(alerts)} EMS alerts (priority 0/1/2)")
                    except Exception as e:
                        print(f"  Error reading EMS-LOG-FILE.gz: {e}")
                
        except Exception as e:
            print(f"  Error: {e}")
    
    print(f"\nTotal interfaces: {len(all_interfaces)}")
    print(f"Total ports (link=up): {len(all_ports)}")
    print(f"Total IFGRPs: {len(all_ifgrps)}")
    print(f"Total discoveries: {len(all_discoveries)}")
    print(f"Total EMS alerts: {len(all_alerts)}")
    
    sorted_interfaces = sort_interfaces(all_interfaces)
    sorted_ports = sort_ports(all_ports)
    sorted_ifgrps = sort_ifgrps(all_ifgrps)
    sorted_discoveries = sort_discoveries(all_discoveries)
    sorted_alerts = sort_alerts(all_alerts)
    
    output_path = os.path.join(output_dir, f'{dir_name} Network Interface Report.xlsx')
    create_excel_report(sorted_interfaces, sorted_ports, sorted_ifgrps, sorted_discoveries, sorted_alerts, output_path)
    
    return len(sorted_interfaces), len(sorted_ports), len(sorted_ifgrps), len(sorted_discoveries), len(sorted_alerts)


def main():
    import sys as _sys
    asup_base_dir = str(package_path('logs_root'))
    output_dir = os.environ.get('SAM_REPORT_OUTPUT_DIR') or str(package_path('legacy_root') / 'Network parse/output')
    
    os.makedirs(output_dir, exist_ok=True)
    
    seven_zip_exe = str(package_path('seven_zip_path'))
    
    # Accept optional command-line argument for custom input directory
    custom_dirs = _sys.argv[1:] if len(_sys.argv) > 1 else []
    
    total_interfaces = 0
    total_ports = 0
    total_ifgrps = 0
    total_discoveries = 0
    total_alerts = 0
    processed_dirs = 0
    
    if custom_dirs:
        dirs_to_process = [(d, os.path.basename(d.rstrip(os.sep))) for d in custom_dirs]
    else:
        dirs_to_process = [(os.path.join(asup_base_dir, e), e) for e in os.listdir(asup_base_dir)
                          if os.path.isdir(os.path.join(asup_base_dir, e))]
    
    for entry_path, entry_name in dirs_to_process:
        interfaces, ports, ifgrps, discoveries, alerts = process_directory(entry_path, output_dir, seven_zip_exe)
        total_interfaces += interfaces
        total_ports += ports
        total_ifgrps += ifgrps
        total_discoveries += discoveries
        total_alerts += alerts
        processed_dirs += 1
    
    print(f"\n{'='*60}")
    print(f"Processing complete!")
    print(f"Total directories processed: {processed_dirs}")
    print(f"Total interfaces: {total_interfaces}")
    print(f"Total ports (link=up): {total_ports}")
    print(f"Total IFGRPs: {total_ifgrps}")
    print(f"Total discoveries: {total_discoveries}")
    print(f"Total EMS alerts: {total_alerts}")
    print(f"Reports saved to: {output_dir}")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
