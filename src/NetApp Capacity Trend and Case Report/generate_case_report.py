"""
Case分析报告生成函数
"""

import pandas as pd
import os
import json
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from report_dates import select_report_files

def _json_default(o):
    if hasattr(o, 'item'):
        return o.item()
    return str(o)

HARDWARE_CATEGORIES = ['硬盘', 'SAS线', 'SFP', 'NVRAM', 'NVR存卡', '缓存卡', '网卡', '主板', '电源', '风扇', '盘柜', 'IOM', '其他']

CLUSTER_FILTER = None  # if not None, only these clusters are reported


def set_cluster_filter(clusters):
    global CLUSTER_FILTER
    if clusters:
        CLUSTER_FILTER = [str(c).strip() for c in clusters]
    else:
        CLUSTER_FILTER = None
    return CLUSTER_FILTER


def categorize_hardware(part):
    """将故障部件归类到硬件分类中"""
    part = str(part).strip()
    
    if '硬盘' in part or 'disk' in part.lower() or 'HDD' in part or 'SSD' in part:
        return '硬盘'
    elif 'SAS线' in part or 'SAS cable' in part.lower():
        return 'SAS线'
    elif 'SFP' in part:
        return 'SFP'
    elif 'NVRAM' in part or 'NVR' in part:
        return 'NVRAM'
    elif '缓存' in part or 'cache' in part.lower():
        return '缓存卡'
    elif '网卡' in part or 'NIC' in part or 'network' in part.lower():
        return '网卡'
    elif '主板' in part or 'motherboard' in part.lower():
        return '主板'
    elif '电源' in part or 'power' in part.lower():
        return '电源'
    elif '风扇' in part or 'fan' in part.lower():
        return '风扇'
    elif '盘柜' in part or 'shelf' in part.lower():
        return '盘柜'
    elif 'IOM' in part:
        return 'IOM'
    else:
        return '其他'

def extract_case_data(base_dir):
    """从最后一个月份报告中按Case创建时间提取各月份数据"""
    files = select_report_files(base_dir, ('硬件case', '软件case'))
    months = list(files)
    
    if not months:
        return {}, [], pd.DataFrame()
    
    last_month = months[-1]
    last_file = files[last_month]
    
    print(f"  从 {last_month} 报告读取历史数据...")
    
    try:
        hardware_df = pd.read_excel(last_file, sheet_name='硬件case', header=0)
        software_df = pd.read_excel(last_file, sheet_name='软件case', header=0)
        
        hardware_df['Case类型'] = '硬件故障'
        software_df['Case类型'] = '软件故障'
        
        software_df = software_df.dropna(subset=['Case ID']).copy()
        software_df = software_df[software_df['Case ID'].astype(str).str.strip() != '问题类型'].copy()
        software_df = software_df[software_df['Case ID'].astype(str).str.len() > 5].copy()
        software_df = software_df[software_df['Case ID'].astype(str).str.match(r'^[A-Z0-9]+$')].copy()
        
        if '暂无数据' in hardware_df.columns and 'Case ID' not in hardware_df.columns:
            all_cases = software_df.copy()
        else:
            hardware_df = hardware_df.dropna(subset=['Case ID']).copy()
            all_cases = pd.concat([hardware_df, software_df], ignore_index=True)
        
        if 'Case创建时间' in all_cases.columns:
            all_cases['Case创建时间'] = pd.to_datetime(all_cases['Case创建时间'], errors='coerce')
            all_cases = all_cases[all_cases['Case创建时间'].notna()].copy()
            all_cases = all_cases[all_cases['Case创建时间'].dt.year == 2026].copy()
            if CLUSTER_FILTER is not None and '集群名' in all_cases.columns:
                all_cases = all_cases[all_cases['集群名'].astype(str).str.strip().isin(CLUSTER_FILTER)].copy()
            
            if len(all_cases) == 0:
                print("  警告: 没有有效的Case数据")
                return {}, [], pd.DataFrame()
            all_cases['月份'] = all_cases['Case创建时间'].dt.month
            all_cases['月份名称'] = all_cases['月份'].map({1: '1月', 2: '2月', 3: '3月', 4: '4月', 5: '5月', 6: '6月', 7: '7月', 8: '8月', 9: '9月', 10: '10月', 11: '11月', 12: '12月'})
            
            all_cases['硬件分类'] = all_cases.apply(lambda row: categorize_hardware(row['故障部件']) if row['Case类型'] == '硬件故障' else '', axis=1)
            
            case_data = {}
            month_map = {1: '1月', 2: '2月', 3: '3月', 4: '4月', 5: '5月', 6: '6月', 7: '7月', 8: '8月', 9: '9月', 10: '10月', 11: '11月', 12: '12月'}
            
            valid_months = []
            for m in range(1, 13):
                m_name = month_map[m]
                filtered = all_cases[all_cases['月份'] == m].copy()
                case_data[m_name] = filtered
                if len(filtered) > 0:
                    valid_months.append(m_name)
            
            print(f"  数据月份范围: {valid_months[0]}-{valid_months[-1]}")
            return case_data, valid_months, all_cases
        else:
            print("  警告: 未找到Case创建时间字段")
            return {}, [], pd.DataFrame()
            
    except Exception as e:
        print(f"  警告: 读取失败 - {e}")
        return {}, [], pd.DataFrame()

def create_case_excel_report(case_data, months, all_cases, output_dir, prefix):
    """创建详细的Case分析Excel报告"""
    wb = Workbook()

    header_font = Font(name='微软雅黑', size=11, bold=True, color='FFFFFF')
    header_fill_blue = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    header_fill_red = PatternFill(start_color='E74C3C', end_color='E74C3C', fill_type='solid')
    header_fill_green = PatternFill(start_color='27AE60', end_color='27AE60', fill_type='solid')
    header_alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    data_font = Font(name='微软雅黑', size=10)
    data_alignment = Alignment(horizontal='center', vertical='center')
    thin_border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))

    ws1 = wb.active
    ws1.title = "Case趋势统计"
    header1 = ['月份', '总Case数', '硬件故障数', '软件故障数', '硬件占比(%)', '软件占比(%)']
    ws1.append(header1)

    for month in months:
        df = case_data[month]
        total = len(df)
        hardware = len(df[df['Case类型'] == '硬件故障'])
        software = len(df[df['Case类型'] == '软件故障'])
        hw_pct = (hardware / total * 100) if total > 0 else 0
        sw_pct = (software / total * 100) if total > 0 else 0
        ws1.append([month, total, hardware, software, round(hw_pct, 1), round(sw_pct, 1)])

    for col in range(1, ws1.max_column + 1):
        ws1.column_dimensions[ws1.cell(1, col).column_letter].width = 18
        ws1.cell(1, col).font = header_font
        ws1.cell(1, col).fill = header_fill_blue
        ws1.cell(1, col).alignment = header_alignment
        ws1.cell(1, col).border = thin_border
    for row in range(2, ws1.max_row + 1):
        for col in range(1, ws1.max_column + 1):
            ws1.cell(row, col).font = data_font
            ws1.cell(row, col).alignment = data_alignment
            ws1.cell(row, col).border = thin_border

    ws2 = wb.create_sheet("集群Case月份统计")
    header2 = ['集群名', '月份', '总Case数', '硬件故障', '软件故障', '占比(%)']
    ws2.append(header2)

    if '集群名' in all_cases.columns:
        cluster_month_stats = all_cases.groupby(['集群名', '月份名称']).size().unstack(fill_value=0)
        hw_cluster_month = all_cases[all_cases['Case类型'] == '硬件故障'].groupby(['集群名', '月份名称']).size().unstack(fill_value=0)
        sw_cluster_month = all_cases[all_cases['Case类型'] == '软件故障'].groupby(['集群名', '月份名称']).size().unstack(fill_value=0)
        
        total_total = len(all_cases)
        
        for cluster in cluster_month_stats.index:
            for month in months:
                total = cluster_month_stats.loc[cluster, month] if month in cluster_month_stats.columns else 0
                hardware = hw_cluster_month.loc[cluster, month] if cluster in hw_cluster_month.index and month in hw_cluster_month.columns else 0
                software = sw_cluster_month.loc[cluster, month] if cluster in sw_cluster_month.index and month in sw_cluster_month.columns else 0
                pct = (total / total_total * 100) if total_total > 0 else 0
                ws2.append([cluster, month, total, hardware, software, round(pct, 1)])

    for col in range(1, ws2.max_column + 1):
        ws2.column_dimensions[ws2.cell(1, col).column_letter].width = 18
        ws2.cell(1, col).font = header_font
        ws2.cell(1, col).fill = header_fill_blue
        ws2.cell(1, col).alignment = header_alignment
        ws2.cell(1, col).border = thin_border
    for row in range(2, ws2.max_row + 1):
        for col in range(1, ws2.max_column + 1):
            ws2.cell(row, col).font = data_font
            ws2.cell(row, col).alignment = data_alignment
            ws2.cell(row, col).border = thin_border

    ws3 = wb.create_sheet("集群Case汇总")
    header3 = ['集群名', '总Case数', '硬件故障', '软件故障', '硬件占比(%)', '软件占比(%)', '排序']
    ws3.append(header3)

    if '集群名' in all_cases.columns:
        cluster_stats = all_cases.groupby(['集群名', 'Case类型']).size().unstack(fill_value=0)
        cluster_stats['总计'] = cluster_stats.sum(axis=1)
        cluster_stats = cluster_stats.sort_values('总计', ascending=False)
        
        for i, (cluster, row) in enumerate(cluster_stats.iterrows(), 1):
            hardware = row.get('硬件故障', 0)
            software = row.get('软件故障', 0)
            total = row['总计']
            hw_pct = (hardware / total * 100) if total > 0 else 0
            sw_pct = (software / total * 100) if total > 0 else 0
            ws3.append([cluster, total, hardware, software, round(hw_pct, 1), round(sw_pct, 1), i])

    for col in range(1, ws3.max_column + 1):
        ws3.column_dimensions[ws3.cell(1, col).column_letter].width = 18
        ws3.cell(1, col).font = header_font
        ws3.cell(1, col).fill = header_fill_blue
        ws3.cell(1, col).alignment = header_alignment
        ws3.cell(1, col).border = thin_border
    for row in range(2, ws3.max_row + 1):
        for col in range(1, ws3.max_column + 1):
            ws3.cell(row, col).font = data_font
            ws3.cell(row, col).alignment = data_alignment
            ws3.cell(row, col).border = thin_border

    ws4 = wb.create_sheet("硬件故障分类统计")
    header4 = ['硬件分类', '数量', '占比(%)']
    ws4.append(header4)

    hw_cases = all_cases[all_cases['Case类型'] == '硬件故障']
    hw_category_counts = hw_cases['硬件分类'].value_counts().reset_index()
    hw_category_counts.columns = ['硬件分类', '数量']
    hw_total = hw_category_counts['数量'].sum()
    
    for _, row in hw_category_counts.iterrows():
        pct = (row['数量'] / hw_total * 100) if hw_total > 0 else 0
        ws4.append([row['硬件分类'], row['数量'], round(pct, 1)])

    for col in range(1, ws4.max_column + 1):
        ws4.column_dimensions[ws4.cell(1, col).column_letter].width = 25
        ws4.cell(1, col).font = header_font
        ws4.cell(1, col).fill = header_fill_red
        ws4.cell(1, col).alignment = header_alignment
        ws4.cell(1, col).border = thin_border
    for row in range(2, ws4.max_row + 1):
        for col in range(1, ws4.max_column + 1):
            ws4.cell(row, col).font = data_font
            ws4.cell(row, col).alignment = data_alignment
            ws4.cell(row, col).border = thin_border

    ws5 = wb.create_sheet("硬件分类趋势")
    header5 = ['月份'] + HARDWARE_CATEGORIES
    ws5.append(header5)

    for month in months:
        month_data = [month]
        month_cases = case_data[month][case_data[month]['Case类型'] == '硬件故障']
        for category in HARDWARE_CATEGORIES:
            count = len(month_cases[month_cases['硬件分类'] == category])
            month_data.append(count)
        ws5.append(month_data)

    for col in range(1, ws5.max_column + 1):
        ws5.column_dimensions[ws5.cell(1, col).column_letter].width = 12
        ws5.cell(1, col).font = header_font
        ws5.cell(1, col).fill = header_fill_red
        ws5.cell(1, col).alignment = header_alignment
        ws5.cell(1, col).border = thin_border
    for row in range(2, ws5.max_row + 1):
        for col in range(1, ws5.max_column + 1):
            ws5.cell(row, col).font = data_font
            ws5.cell(row, col).alignment = data_alignment
            ws5.cell(row, col).border = thin_border

    ws6 = wb.create_sheet("软件故障详细分类")
    header6 = ['故障部件', '数量', '占比(%)']
    ws6.append(header6)

    sw_cases = all_cases[all_cases['Case类型'] == '软件故障']
    if '故障部件' in sw_cases.columns:
        sw_part_counts = sw_cases['故障部件'].value_counts().reset_index()
        sw_part_counts.columns = ['故障部件', '数量']
        sw_total = sw_part_counts['数量'].sum()
        
        for _, row in sw_part_counts.iterrows():
            pct = (row['数量'] / sw_total * 100) if sw_total > 0 else 0
            ws6.append([row['故障部件'], row['数量'], round(pct, 1)])

    for col in range(1, ws6.max_column + 1):
        ws6.column_dimensions[ws6.cell(1, col).column_letter].width = 25
        ws6.cell(1, col).font = header_font
        ws6.cell(1, col).fill = header_fill_green
        ws6.cell(1, col).alignment = header_alignment
        ws6.cell(1, col).border = thin_border
    for row in range(2, ws6.max_row + 1):
        for col in range(1, ws6.max_column + 1):
            ws6.cell(row, col).font = data_font
            ws6.cell(row, col).alignment = data_alignment
            ws6.cell(row, col).border = thin_border

    ws7 = wb.create_sheet("故障分类统计")
    header7 = ['Case类型', '故障部件', '硬件分类', '数量', '占比(%)']
    ws7.append(header7)

    if '故障部件' in all_cases.columns:
        all_part_counts = all_cases.groupby(['Case类型', '故障部件', '硬件分类']).size().reset_index(name='数量')
        all_part_counts = all_part_counts.sort_values('数量', ascending=False)
        total = all_part_counts['数量'].sum()
        
        for _, row in all_part_counts.iterrows():
            pct = (row['数量'] / total * 100) if total > 0 else 0
            ws7.append([row['Case类型'], row['故障部件'], row['硬件分类'], row['数量'], round(pct, 1)])

    for col in range(1, ws7.max_column + 1):
        ws7.column_dimensions[ws7.cell(1, col).column_letter].width = 20
        ws7.cell(1, col).font = header_font
        ws7.cell(1, col).fill = header_fill_blue
        ws7.cell(1, col).alignment = header_alignment
        ws7.cell(1, col).border = thin_border
    for row in range(2, ws7.max_row + 1):
        for col in range(1, ws7.max_column + 1):
            ws7.cell(row, col).font = data_font
            ws7.cell(row, col).alignment = data_alignment
            ws7.cell(row, col).border = thin_border

    ws8 = wb.create_sheet("设备型号统计")
    header8 = ['设备型号', '数量', '占比(%)']
    ws8.append(header8)

    if '设备型号' in all_cases.columns:
        device_counts = all_cases['设备型号'].value_counts().reset_index()
        device_counts.columns = ['设备型号', '数量']
        total = device_counts['数量'].sum()
        
        for _, row in device_counts.iterrows():
            pct = (row['数量'] / total * 100) if total > 0 else 0
            ws8.append([row['设备型号'], row['数量'], round(pct, 1)])

    for col in range(1, ws8.max_column + 1):
        ws8.column_dimensions[ws8.cell(1, col).column_letter].width = 25
        ws8.cell(1, col).font = header_font
        ws8.cell(1, col).fill = header_fill_blue
        ws8.cell(1, col).alignment = header_alignment
        ws8.cell(1, col).border = thin_border
    for row in range(2, ws8.max_row + 1):
        for col in range(1, ws8.max_column + 1):
            ws8.cell(row, col).font = data_font
            ws8.cell(row, col).alignment = data_alignment
            ws8.cell(row, col).border = thin_border

    ws9 = wb.create_sheet("Case详情")
    if not all_cases.empty:
        cols = ['月份名称', 'Case ID', 'Case创建时间', '集群名', '节点名', '设备型号', '故障分类', '故障部件', '硬件分类', '问题描述', '客户姓名', '企业名称', '是否有上门单']
        available_cols = [c for c in cols if c in all_cases.columns]
        ws9.append(available_cols)
        
        for _, row in all_cases.iterrows():
            row_data = []
            for col in available_cols:
                val = row.get(col, '')
                if pd.isna(val):
                    val = ''
                row_data.append(val)
            ws9.append(row_data)

        for col in range(1, ws9.max_column + 1):
            ws9.column_dimensions[ws9.cell(1, col).column_letter].width = 20
            ws9.cell(1, col).font = header_font
            ws9.cell(1, col).fill = header_fill_blue
            ws9.cell(1, col).alignment = header_alignment
            ws9.cell(1, col).border = thin_border
        for row in range(2, ws9.max_row + 1):
            for col in range(1, ws9.max_column + 1):
                ws9.cell(row, col).font = data_font
                ws9.cell(row, col).alignment = Alignment(horizontal='left', vertical='center')
                ws9.cell(row, col).border = thin_border

    output_file = os.path.join(output_dir, f'{prefix}Case分析报告.xlsx')
    wb.save(output_file)
    print(f"  Case Excel报告已保存: {output_file}")
    return output_file

def generate_case_html_report(case_data, months, all_cases, output_dir, prefix):
    """生成详细的Case分析HTML报告"""
    trend_data = []
    for month in months:
        df = case_data[month]
        total = len(df)
        hardware = len(df[df['Case类型'] == '硬件故障'])
        software = len(df[df['Case类型'] == '软件故障'])
        trend_data.append({'month': month, 'total': total, 'hardware': hardware, 'software': software})

    cluster_stats = all_cases.groupby(['集群名', 'Case类型']).size().unstack(fill_value=0)
    cluster_stats['总计'] = cluster_stats.sum(axis=1)
    cluster_stats = cluster_stats.sort_values('总计', ascending=False)

    hw_cases = all_cases[all_cases['Case类型'] == '硬件故障']
    hw_category_counts = hw_cases['硬件分类'].value_counts()

    hw_trend_data = []
    for month in months:
        month_data = {'month': month}
        month_cases = case_data[month][case_data[month]['Case类型'] == '硬件故障']
        for category in HARDWARE_CATEGORIES:
            month_data[category] = len(month_cases[month_cases['硬件分类'] == category])
        hw_trend_data.append(month_data)

    sw_cases = all_cases[all_cases['Case类型'] == '软件故障']
    sw_part_counts = sw_cases['故障部件'].value_counts().head(10)

    device_counts = all_cases['设备型号'].value_counts().head(10)

    charts = []

    charts.append({
        'title': 'Case数量趋势',
        'type': 'line',
        'xAxis': [d['month'] for d in trend_data],
        'series': [
            {'name': '总Case数', 'data': [d['total'] for d in trend_data], 'smooth': True},
            {'name': '硬件故障', 'data': [d['hardware'] for d in trend_data], 'smooth': True},
            {'name': '软件故障', 'data': [d['software'] for d in trend_data], 'smooth': True}
        ]
    })

    hw_pie_data = [{'name': k, 'value': v} for k, v in hw_category_counts.items()]
    charts.append({
        'title': '硬件故障分类统计',
        'type': 'pie',
        'data': hw_pie_data,
        'color': ['#E74C3C', '#C0392B', '#E67E22', '#D35400', '#F39C12', '#3498DB', '#2980B9', '#1ABC9C', '#16A085', '#9B59B6', '#8E44AD', '#2C3E50', '#7F8C8D']
    })

    hw_trend_series = []
    for category in HARDWARE_CATEGORIES:
        hw_trend_series.append({
            'name': category,
            'data': [d[category] for d in hw_trend_data],
            'smooth': True
        })
    charts.append({
        'title': '硬件分类趋势',
        'type': 'line',
        'xAxis': [d['month'] for d in hw_trend_data],
        'series': hw_trend_series
    })

    sw_pie_data = [{'name': k, 'value': v} for k, v in sw_part_counts.items()]
    charts.append({
        'title': '软件故障详细分类',
        'type': 'pie',
        'data': sw_pie_data,
        'color': ['#3498DB', '#2980B9', '#1ABC9C', '#16A085', '#9B59B6']
    })

    bar_xaxis = cluster_stats.index.tolist()
    bar_hardware = cluster_stats['硬件故障'].tolist() if '硬件故障' in cluster_stats.columns else [0]*len(cluster_stats)
    bar_software = cluster_stats['软件故障'].tolist() if '软件故障' in cluster_stats.columns else [0]*len(cluster_stats)
    
    charts.append({
        'title': '集群Case数量对比',
        'type': 'bar',
        'xAxis': bar_xaxis,
        'hardwareData': bar_hardware,
        'softwareData': bar_software
    })

    device_pie_data = [{'name': k, 'value': v} for k, v in device_counts.items()]
    charts.append({
        'title': '设备型号分布',
        'type': 'pie',
        'data': device_pie_data
    })

    charts_json = json.dumps(charts, ensure_ascii=False, default=_json_default)

    trend_table_rows = ''
    for d in trend_data:
        hw_pct = (d['hardware'] / d['total'] * 100) if d['total'] > 0 else 0
        sw_pct = (d['software'] / d['total'] * 100) if d['total'] > 0 else 0
        trend_table_rows += f'<tr><td>{d["month"]}</td><td>{d["total"]}</td><td>{d["hardware"]}</td><td>{d["software"]}</td><td>{hw_pct:.1f}</td><td>{sw_pct:.1f}</td></tr>'

    cluster_table_rows = ''
    total_total = len(all_cases)
    for i, (cluster, row) in enumerate(cluster_stats.iterrows(), 1):
        hardware = row.get('硬件故障', 0)
        software = row.get('软件故障', 0)
        total = row['总计']
        hw_pct = (hardware / total * 100) if total > 0 else 0
        sw_pct = (software / total * 100) if total > 0 else 0
        pct = (total / total_total * 100) if total_total > 0 else 0
        cluster_table_rows += f'<tr><td>{i}</td><td>{cluster}</td><td>{total}</td><td>{hardware}</td><td>{software}</td><td>{hw_pct:.1f}</td><td>{sw_pct:.1f}</td><td>{pct:.1f}</td></tr>'

    hw_category_table_rows = ''
    hw_total = hw_category_counts.sum()
    for category, count in hw_category_counts.items():
        pct = (count / hw_total * 100) if hw_total > 0 else 0
        hw_category_table_rows += f'<tr><td>{category}</td><td>{count}</td><td>{pct:.1f}%</td></tr>'

    hw_trend_table_rows = ''
    for d in hw_trend_data:
        row_str = f'<tr><td>{d["month"]}</td>'
        for category in HARDWARE_CATEGORIES:
            row_str += f'<td>{d[category]}</td>'
        row_str += '</tr>'
        hw_trend_table_rows += row_str

    sw_table_rows = ''
    sw_total = sw_part_counts.sum()
    for part, count in sw_part_counts.items():
        pct = (count / sw_total * 100) if sw_total > 0 else 0
        sw_table_rows += f'<tr><td>{part}</td><td>{count}</td><td>{pct:.1f}%</td></tr>'

    device_table_rows = ''
    total_device = device_counts.sum()
    for device, count in device_counts.items():
        pct = (count / total_device * 100) if total_device > 0 else 0
        device_table_rows += f'<tr><td>{device}</td><td>{count}</td><td>{pct:.1f}%</td></tr>'

    cluster_month_data_js = []
    if '集群名' in all_cases.columns:
        for cluster in cluster_stats.index[:5]:
            hw_data = []
            sw_data = []
            for month in months:
                month_cases = all_cases[(all_cases['集群名'] == cluster) & (all_cases['月份名称'] == month)]
                hw = len(month_cases[month_cases['Case类型'] == '硬件故障'])
                sw = len(month_cases[month_cases['Case类型'] == '软件故障'])
                hw_data.append(hw)
                sw_data.append(sw)
            cluster_month_data_js.append({
                'cluster': cluster,
                'months': months,
                'hwData': hw_data,
                'swData': sw_data
            })

    cluster_month_html = ''
    for cluster in cluster_stats.index[:5]:
        chart_id = 'chart-cluster-month-' + cluster.replace('.', '-').replace('_', '-')
        cluster_month_html += f'<div class="chart-box"><div class="chart-title">{cluster} - Case趋势</div><div id="{chart_id}" class="chart"></div></div>'

    html_content = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{prefix}Case分析报告</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <style>
        body {{ font-family: 'Microsoft YaHei', sans-serif; background: #f5f7fa; padding: 20px; }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        h1 {{ text-align: center; color: #333; margin-bottom: 30px; font-size: 28px; }}
        h2 {{ color: #4472C4; margin: 20px 0 15px; font-size: 20px; border-bottom: 2px solid #4472C4; padding-bottom: 8px; }}
        h3 {{ color: #666; margin: 15px 0 10px; font-size: 16px; }}
        .chart-box {{ background: white; border-radius: 8px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); padding: 20px; margin-bottom: 25px; }}
        .chart-title {{ font-size: 16px; font-weight: bold; color: #333; margin-bottom: 10px; text-align: center; }}
        .chart {{ width: 100%; height: 350px; }}
        .grid-container {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(500px, 1fr)); gap: 20px; }}
        .summary-table {{ width: 100%; border-collapse: collapse; margin-top: 15px; }}
        .summary-table th, .summary-table td {{ border: 1px solid #ddd; padding: 10px; text-align: center; font-size: 13px; }}
        .summary-table th {{ background: #4472C4; color: white; font-weight: bold; }}
        .summary-table tr:nth-child(even) {{ background: #f9f9fa; }}
        .note {{ font-size: 12px; color: #888; margin-top: 10px; padding: 10px; background: #f8f9fa; border-radius: 4px; }}
        .hw-table th {{ background: #E74C3C !important; }}
        .sw-table th {{ background: #27AE60 !important; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 {prefix}Case分析报告</h1>
        
        <div class="note">
            <strong>数据说明：</strong><br>
            - 从最后一个月份报告中提取历史Case数据<br>
            - 按Case创建时间进行月份分组统计<br>
            - 硬件故障按分类统计：硬盘、SAS线、SFP、NVRAM、缓存卡、网卡、主板、电源、风扇、盘柜、IOM、其他
        </div>

        <div class="chart-box">
            <h2>{prefix} Case数量趋势</h2>
            <div id="chart-trend" class="chart"></div>
        </div>

        <div class="chart-box">
            <h2>{prefix} 硬件故障分类统计</h2>
            <div id="chart-hw-category" class="chart"></div>
        </div>

        <div class="chart-box">
            <h2>{prefix} 硬件分类趋势</h2>
            <div id="chart-hw-trend" class="chart"></div>
        </div>

        <div class="chart-box">
            <h2>{prefix} 软件故障详细分类</h2>
            <div id="chart-sw-category" class="chart"></div>
        </div>

        <div class="chart-box">
            <h2>{prefix} 集群Case数量对比</h2>
            <div id="chart-cluster" class="chart"></div>
        </div>

        <div class="chart-box">
            <h2>{prefix} 设备型号分布</h2>
            <div id="chart-device" class="chart"></div>
        </div>

        <div class="chart-box">
            <h2>{prefix} 各集群Case趋势</h2>
            <div class="grid-container">
                {cluster_month_html}
            </div>
        </div>

        <div class="chart-box">
            <h2>{prefix} 数据汇总表</h2>
            
            <h3>Case趋势统计</h3>
            <table class="summary-table">
                <tr><th>月份</th><th>总Case数</th><th>硬件故障</th><th>软件故障</th><th>硬件占比(%)</th><th>软件占比(%)</th></tr>
                {trend_table_rows}
            </table>
        </div>

        <div class="chart-box">
            <h3>集群Case汇总（按数量排序）</h3>
            <table class="summary-table">
                <tr><th>排序</th><th>集群名</th><th>总Case数</th><th>硬件故障</th><th>软件故障</th><th>硬件占比(%)</th><th>软件占比(%)</th><th>总占比(%)</th></tr>
                {cluster_table_rows}
            </table>
        </div>

        <div class="chart-box">
            <h3>硬件故障分类统计</h3>
            <table class="summary-table hw-table">
                <tr><th>硬件分类</th><th>数量</th><th>占比</th></tr>
                {hw_category_table_rows}
            </table>
        </div>

        <div class="chart-box">
            <h3>硬件分类趋势</h3>
            <table class="summary-table hw-table">
                <tr><th>月份</th><th>硬盘</th><th>SAS线</th><th>SFP</th><th>NVRAM</th><th>缓存卡</th><th>网卡</th><th>主板</th><th>电源</th><th>风扇</th><th>盘柜</th><th>IOM</th><th>其他</th></tr>
                {hw_trend_table_rows}
            </table>
        </div>

        <div class="chart-box">
            <h3>软件故障详细分类</h3>
            <table class="summary-table sw-table">
                <tr><th>故障部件</th><th>数量</th><th>占比</th></tr>
                {sw_table_rows}
            </table>
        </div>

        <div class="chart-box">
            <h3>设备型号统计（TOP 10）</h3>
            <table class="summary-table">
                <tr><th>设备型号</th><th>数量</th><th>占比</th></tr>
                {device_table_rows}
            </table>
        </div>
    </div>

    <script>
        var charts = {charts_json};
        
        function initLineChart(id, config) {{
            var chart = echarts.init(document.getElementById(id));
            chart.setOption({{
                tooltip: {{trigger: 'axis', formatter: function(p) {{ 
                    var r = p[0].axisValue + '<br/>'; 
                    p.forEach(function(item){{
                        r += item.marker + item.seriesName + ': ' + item.value + '<br/>';
                    }}); 
                    return r;
                }} }},
                legend: {{data: config.series.map(function(s){{return s.name;}}), bottom:0, itemWidth:25, itemHeight:10, type: 'scroll'}},
                grid: {{left:'3%', right:'4%', bottom:'20%', top:'10%', containLabel:true}},
                xAxis: {{type:'category', boundaryGap:false, data:config.xAxis}},
                yAxis: {{type:'value', name:'数量'}},
                series: config.series.map(function(s){{
                    return {{
                        name: s.name, 
                        type: config.type, 
                        smooth: s.smooth || false, 
                        data: s.data,
                        symbol: 'circle',
                        symbolSize: 6,
                        lineStyle: {{width: 2}},
                        itemStyle: {{borderWidth: 2, borderColor: '#fff'}}
                    }};
                }})
            }});
            window.addEventListener('resize', function(){{chart.resize();}});
        }}
        
        function initPieChart(id, config) {{
            var chart = echarts.init(document.getElementById(id));
            var option = {{
                tooltip: {{trigger: 'item', formatter: '{{b}}: {{c}} ({{d}}%)'}},
                legend: {{bottom: 0, left: 'center', type: 'scroll'}},
                series: [{{type: 'pie', radius: ['40%', '70%'], data: config.data}}]
            }};
            if (config.color) {{
                option.color = config.color;
            }}
            chart.setOption(option);
            window.addEventListener('resize', function(){{chart.resize();}});
        }}
        
        function initBarChart(id, config) {{
            var chart = echarts.init(document.getElementById(id));
            chart.setOption({{
                tooltip: {{trigger: 'axis', axisPointer: {{type: 'shadow'}}}},
                legend: {{data: ['硬件故障', '软件故障'], bottom:0}},
                grid: {{left:'3%', right:'4%', bottom:'20%', top:'10%', containLabel:true}},
                xAxis: {{type:'category', data:config.xAxis, axisLabel:{{rotate:30, fontSize:10}}}},
                yAxis: {{type:'value', name:'数量'}},
                series: [
                    {{name: '硬件故障', type: 'bar', stack: 'total', data: config.hardwareData}},
                    {{name: '软件故障', type: 'bar', stack: 'total', data: config.softwareData}}
                ]
            }});
            window.addEventListener('resize', function(){{chart.resize();}});
        }}
        
        initLineChart('chart-trend', charts[0]);
        initPieChart('chart-hw-category', charts[1]);
        initLineChart('chart-hw-trend', charts[2]);
        initPieChart('chart-sw-category', charts[3]);
        initBarChart('chart-cluster', charts[4]);
        initPieChart('chart-device', charts[5]);
        
        var clusterData = {json.dumps(cluster_month_data_js, ensure_ascii=False, default=_json_default)};
        clusterData.forEach(function(item) {{
            var chartId = 'chart-cluster-month-' + item.cluster.replace(/[._]/g, '-');
            var chart = echarts.init(document.getElementById(chartId));
            chart.setOption({{
                tooltip: {{trigger: 'axis'}},
                legend: {{data: ['硬件故障', '软件故障'], bottom:0}},
                grid: {{left:'3%', right:'4%', bottom:'15%', top:'10%', containLabel:true}},
                xAxis: {{type:'category', data: item.months}},
                yAxis: {{type:'value', name:'数量'}},
                series: [
                    {{name: '硬件故障', type: 'line', smooth: true, data: item.hwData}},
                    {{name: '软件故障', type: 'line', smooth: true, data: item.swData}}
                ]
            }});
            window.addEventListener('resize', function(){{chart.resize();}});
        }});
    </script>
</body>
</html>'''

    html_output = os.path.join(output_dir, f'{prefix}Case分析报告.html')
    with open(html_output, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f"  Case HTML报告已保存: {html_output}")
    return html_output

def generate_case_report(source_dir, output_dir):
    """为单个目录生成Case分析报告"""
    case_data, months, all_cases = extract_case_data(source_dir)
    
    prefix = os.path.basename(source_dir)
    
    print(f"\n生成 {prefix} Case报告...")
    
    if len(months) == 0:
        print(f"  警告: {prefix}目录没有有效数据，跳过...")
        return
    
    create_case_excel_report(case_data, months, all_cases, output_dir, prefix)
    generate_case_html_report(case_data, months, all_cases, output_dir, prefix)

def generate_all_case_reports(source_base, output_base):
    """为所有客户目录生成Case分析报告"""
    if os.path.isdir(source_base):
        has_subdirs = False
        for name in os.listdir(source_base):
            full_path = os.path.join(source_base, name)
            if os.path.isdir(full_path):
                has_subdirs = True
                break
        
        if has_subdirs:
            for dir_name in os.listdir(source_base):
                source_dir = os.path.join(source_base, dir_name)
                if os.path.isdir(source_dir):
                    output_dir = os.path.join(output_base, dir_name)
                    os.makedirs(output_dir, exist_ok=True)
                    try:
                        generate_case_report(source_dir, output_dir)
                    except Exception as e:
                        print("  report failed (%s): %s" % (dir_name, e))
        else:
            output_dir = output_base
            os.makedirs(output_dir, exist_ok=True)
            generate_case_report(source_base, output_dir)
