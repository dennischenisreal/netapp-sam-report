"""
设备盘点分析报告生成函数
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
from datetime import datetime

CLUSTER_FILTER = None  # if not None, only these clusters are reported


def set_cluster_filter(clusters):
    global CLUSTER_FILTER
    if clusters:
        CLUSTER_FILTER = [str(c).strip() for c in clusters]
    else:
        CLUSTER_FILTER = None
    return CLUSTER_FILTER


def extract_device_data(source_dir):
    """提取设备数据"""
    files = select_report_files(source_dir, ('Controller Report',))
    months = list(files)
    
    if not months:
        return None
    
    last_month = months[-1]
    last_file = files[last_month]
    
    print(f"  读取设备数据: {os.path.basename(last_file)}")
    
    try:
        df = pd.read_excel(last_file, sheet_name='Controller Report', header=0)
        if CLUSTER_FILTER is not None and 'Cluster Name' in df.columns:
            df = df[df['Cluster Name'].astype(str).str.strip().isin(CLUSTER_FILTER)]
        return df
    except Exception as e:
        print(f"  警告: 无法读取Controller Report: {e}")
        return None

def generate_device_report(source_dir, output_dir):
    """生成设备盘点分析报告"""
    df = extract_device_data(source_dir)
    
    if df is None or df.empty:
        print("  警告: 没有有效的设备数据，跳过设备盘点报告...")
        return
    
    prefix = os.path.basename(source_dir)
    
    df['OS EoLS日期'] = pd.to_datetime(df['OS EoLS日期'], errors='coerce')
    df['硬件EOS日期'] = pd.to_datetime(df['硬件EOS日期'], errors='coerce')
    df['维保到期时间'] = pd.to_datetime(df['维保到期时间'], errors='coerce')
    
    today = datetime(2026, 5, 21)
    
    df['OS EoLS状态'] = df['OS EoLS日期'].apply(lambda x: '已过期' if pd.notna(x) and x < today else ('90天内过期' if pd.notna(x) and (x - today).days <= 90 else ('180天内过期' if pd.notna(x) and (x - today).days <= 180 else '正常')))
    df['硬件EOS状态'] = df['硬件EOS日期'].apply(lambda x: '已过期' if pd.notna(x) and x < today else ('90天内过期' if pd.notna(x) and (x - today).days <= 90 else ('180天内过期' if pd.notna(x) and (x - today).days <= 180 else '正常')))
    df['维保状态'] = df['维保到期时间'].apply(lambda x: '已过期' if pd.notna(x) and x < today else ('90天内过期' if pd.notna(x) and (x - today).days <= 90 else ('180天内过期' if pd.notna(x) and (x - today).days <= 180 else '有效')))
    
    wb = Workbook()
    
    header_font = Font(name='微软雅黑', size=11, bold=True, color='FFFFFF')
    header_fill_blue = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    header_fill_red = PatternFill(start_color='E74C3C', end_color='E74C3C', fill_type='solid')
    header_fill_yellow = PatternFill(start_color='F39C12', end_color='F39C12', fill_type='solid')
    header_fill_green = PatternFill(start_color='27AE60', end_color='27AE60', fill_type='solid')
    header_alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    data_font = Font(name='微软雅黑', size=10)
    data_alignment = Alignment(horizontal='center', vertical='center')
    thin_border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))
    
    ws1 = wb.active
    ws1.title = "设备汇总表"
    header1 = ['Cluster Name', 'System Name', 'Serial Number', 'Model', 'OS Version', 'OS EoLS日期', 'OS EoLS状态', '硬件EOS日期', '硬件EOS状态', '维保到期时间', '维保状态', '服务级别']
    ws1.append(header1)
    
    for _, row in df.iterrows():
        ws1.append([
            row['Cluster Name'], row['System Name'], row['Serial Number'], row['Model'],
            row['OS Version'], 
            row['OS EoLS日期'].strftime('%Y-%m-%d') if pd.notna(row['OS EoLS日期']) else '暂无',
            row['OS EoLS状态'],
            row['硬件EOS日期'].strftime('%Y-%m-%d') if pd.notna(row['硬件EOS日期']) else '暂无',
            row['硬件EOS状态'],
            row['维保到期时间'].strftime('%Y-%m-%d') if pd.notna(row['维保到期时间']) else '暂无',
            row['维保状态'],
            row['服务级别']
        ])
    
    for col in range(1, ws1.max_column + 1):
        ws1.column_dimensions[ws1.cell(1, col).column_letter].width = 15
        ws1.cell(1, col).font = header_font
        ws1.cell(1, col).fill = header_fill_blue
        ws1.cell(1, col).alignment = header_alignment
        ws1.cell(1, col).border = thin_border
    for row in range(2, ws1.max_row + 1):
        for col in range(1, ws1.max_column + 1):
            ws1.cell(row, col).font = data_font
            ws1.cell(row, col).alignment = data_alignment
            ws1.cell(row, col).border = thin_border
            if col == 7 or col == 9 or col == 11:
                if ws1.cell(row, col).value == '已过期':
                    ws1.cell(row, col).fill = header_fill_red
                    ws1.cell(row, col).font = Font(name='微软雅黑', size=10, color='FFFFFF', bold=True)
                elif ws1.cell(row, col).value == '90天内过期':
                    ws1.cell(row, col).fill = header_fill_yellow
                    ws1.cell(row, col).font = Font(name='微软雅黑', size=10, color='FFFFFF', bold=True)
                elif ws1.cell(row, col).value == '180天内过期':
                    ws1.cell(row, col).fill = PatternFill(start_color='F1C40F', end_color='F1C40F', fill_type='solid')
                elif ws1.cell(row, col).value == '有效':
                    ws1.cell(row, col).fill = header_fill_green
                    ws1.cell(row, col).font = Font(name='微软雅黑', size=10, color='FFFFFF', bold=True)
    
    ws2 = wb.create_sheet("设备型号统计")
    header2 = ['设备型号', '数量', '占比(%)']
    ws2.append(header2)
    
    model_counts = df['Model'].value_counts().reset_index()
    model_counts.columns = ['设备型号', '数量']
    total = len(df)
    for _, row in model_counts.iterrows():
        pct = (row['数量'] / total * 100) if total > 0 else 0
        ws2.append([row['设备型号'], row['数量'], round(pct, 1)])
    
    for col in range(1, 4):
        ws2.column_dimensions[ws2.cell(1, col).column_letter].width = 20
        ws2.cell(1, col).font = header_font
        ws2.cell(1, col).fill = header_fill_blue
        ws2.cell(1, col).alignment = header_alignment
        ws2.cell(1, col).border = thin_border
    for row in range(2, ws2.max_row + 1):
        for col in range(1, 4):
            ws2.cell(row, col).font = data_font
            ws2.cell(row, col).alignment = data_alignment
            ws2.cell(row, col).border = thin_border
    
    def extract_major_version(version):
        version = str(version).strip()
        p_index = version.upper().find('P')
        if p_index > 0:
            return version[:p_index]
        return version
    
    df['OS版本大版本'] = df['OS Version'].apply(extract_major_version)
    ws3 = wb.create_sheet("ONTAP版本统计")
    header3 = ['ONTAP版本', '数量', '占比(%)']
    ws3.append(header3)
    
    os_counts = df['OS版本大版本'].value_counts().reset_index()
    os_counts.columns = ['ONTAP版本', '数量']
    for _, row in os_counts.iterrows():
        pct = (row['数量'] / total * 100) if total > 0 else 0
        ws3.append([row['ONTAP版本'], row['数量'], round(pct, 1)])
    
    for col in range(1, 4):
        ws3.column_dimensions[ws3.cell(1, col).column_letter].width = 20
        ws3.cell(1, col).font = header_font
        ws3.cell(1, col).fill = header_fill_blue
        ws3.cell(1, col).alignment = header_alignment
        ws3.cell(1, col).border = thin_border
    for row in range(2, ws3.max_row + 1):
        for col in range(1, 4):
            ws3.cell(row, col).font = data_font
            ws3.cell(row, col).alignment = data_alignment
            ws3.cell(row, col).border = thin_border
    
    ws4 = wb.create_sheet("EOS状态统计")
    header4 = ['状态类型', '正常/有效', '180天内过期', '90天内过期', '已过期', '暂无数据']
    ws4.append(header4)
    
    os_eos_status = df['OS EoLS状态'].value_counts()
    hw_eos_status = df['硬件EOS状态'].value_counts()
    maint_status = df['维保状态'].value_counts()
    
    ws4.append(['OS EoLS状态', os_eos_status.get('正常', 0), os_eos_status.get('180天内过期', 0), os_eos_status.get('90天内过期', 0), os_eos_status.get('已过期', 0), os_eos_status.get('暂无', 0)])
    ws4.append(['硬件EOS状态', hw_eos_status.get('正常', 0), hw_eos_status.get('180天内过期', 0), hw_eos_status.get('90天内过期', 0), hw_eos_status.get('已过期', 0), hw_eos_status.get('暂无', 0)])
    ws4.append(['维保状态', maint_status.get('有效', 0), maint_status.get('180天内过期', 0), maint_status.get('90天内过期', 0), maint_status.get('已过期', 0), maint_status.get('暂无', 0)])
    
    for col in range(1, 6):
        ws4.column_dimensions[ws4.cell(1, col).column_letter].width = 15
        ws4.cell(1, col).font = header_font
        ws4.cell(1, col).fill = header_fill_blue
        ws4.cell(1, col).alignment = header_alignment
        ws4.cell(1, col).border = thin_border
    for row in range(2, ws4.max_row + 1):
        ws4.cell(row, 1).font = Font(name='微软雅黑', size=10, bold=True)
        for col in range(1, 6):
            ws4.cell(row, col).font = data_font
            ws4.cell(row, col).alignment = data_alignment
            ws4.cell(row, col).border = thin_border
    
    ws5 = wb.create_sheet("即将到期预警")
    header5 = ['System Name', 'Cluster Name', '类型', '到期日期', '剩余天数']
    ws5.append(header5)
    
    warning_list = []
    for _, row in df.iterrows():
        if pd.notna(row['维保到期时间']) and row['维保到期时间'] >= today:
            days_left = (row['维保到期时间'] - today).days
            if days_left <= 365:
                warning_list.append({
                    'System Name': row['System Name'],
                    'Cluster Name': row['Cluster Name'],
                    '类型': '维保到期',
                    '到期日期': row['维保到期时间'].strftime('%Y-%m-%d'),
                    '剩余天数': days_left
                })
        
        if pd.notna(row['OS EoLS日期']) and row['OS EoLS日期'] >= today:
            days_left = (row['OS EoLS日期'] - today).days
            if days_left <= 365:
                warning_list.append({
                    'System Name': row['System Name'],
                    'Cluster Name': row['Cluster Name'],
                    '类型': 'OS EoLS',
                    '到期日期': row['OS EoLS日期'].strftime('%Y-%m-%d'),
                    '剩余天数': days_left
                })
        
        if pd.notna(row['硬件EOS日期']) and row['硬件EOS日期'] >= today:
            days_left = (row['硬件EOS日期'] - today).days
            if days_left <= 365:
                warning_list.append({
                    'System Name': row['System Name'],
                    'Cluster Name': row['Cluster Name'],
                    '类型': '硬件EOS',
                    '到期日期': row['硬件EOS日期'].strftime('%Y-%m-%d'),
                    '剩余天数': days_left
                })
    
    warning_list.sort(key=lambda x: x['剩余天数'])
    for item in warning_list:
        ws5.append([item['System Name'], item['Cluster Name'], item['类型'], item['到期日期'], item['剩余天数']])
    
    for col in range(1, 6):
        ws5.column_dimensions[ws5.cell(1, col).column_letter].width = 18
        ws5.cell(1, col).font = header_font
        ws5.cell(1, col).fill = header_fill_yellow
        ws5.cell(1, col).alignment = header_alignment
        ws5.cell(1, col).border = thin_border
    for row in range(2, ws5.max_row + 1):
        for col in range(1, 6):
            ws5.cell(row, col).font = data_font
            ws5.cell(row, col).alignment = data_alignment
            ws5.cell(row, col).border = thin_border
            if ws5.cell(row, 5).value:
                days = int(ws5.cell(row, 5).value)
                if days <= 90:
                    ws5.cell(row, 5).fill = header_fill_red
                    ws5.cell(row, 5).font = Font(name='微软雅黑', size=10, color='FFFFFF', bold=True)
                elif days <= 180:
                    ws5.cell(row, 5).fill = header_fill_yellow
                    ws5.cell(row, 5).font = Font(name='微软雅黑', size=10, bold=True)
    
    output_file = os.path.join(output_dir, f'{prefix}设备盘点分析报告.xlsx')
    wb.save(output_file)
    print(f"  设备盘点Excel报告已保存: {output_file}")
    
    model_pie_data = [{'name': row['设备型号'], 'value': row['数量']} for _, row in model_counts.iterrows()]
    os_pie_data = [{'name': row['ONTAP版本'], 'value': row['数量']} for _, row in os_counts.iterrows()]
    
    eos_bar_data = {
        'categories': ['OS EoLS状态', '硬件EOS状态', '维保状态'],
        '正常': [os_eos_status.get('正常', 0), hw_eos_status.get('正常', 0), maint_status.get('有效', 0)],
        '180天内过期': [os_eos_status.get('180天内过期', 0), hw_eos_status.get('180天内过期', 0), maint_status.get('180天内过期', 0)],
        '90天内过期': [os_eos_status.get('90天内过期', 0), hw_eos_status.get('90天内过期', 0), maint_status.get('90天内过期', 0)],
        '已过期': [os_eos_status.get('已过期', 0), hw_eos_status.get('已过期', 0), maint_status.get('已过期', 0)],
        '暂无数据': [os_eos_status.get('暂无', 0), hw_eos_status.get('暂无', 0), maint_status.get('暂无', 0)]
    }
    
    charts = {
        'modelPie': model_pie_data,
        'osPie': os_pie_data,
        'eosBar': {
            'categories': eos_bar_data['categories'],
            '正常': [int(x) for x in eos_bar_data['正常']],
            '180天内过期': [int(x) for x in eos_bar_data['180天内过期']],
            '90天内过期': [int(x) for x in eos_bar_data['90天内过期']],
            '已过期': [int(x) for x in eos_bar_data['已过期']],
            '暂无数据': [int(x) for x in eos_bar_data['暂无数据']]
        }
    }
    
    warning_table_rows = ''
    for item in warning_list[:50]:
        color = 'color:#E74C3C;font-weight:bold;' if item['剩余天数'] <= 90 else ('color:#F39C12;font-weight:bold;' if item['剩余天数'] <= 180 else '')
        warning_table_rows += f'''<tr>
            <td>{item['System Name']}</td>
            <td>{item['Cluster Name']}</td>
            <td>{item['类型']}</td>
            <td>{item['到期日期']}</td>
            <td style="{color}">{item['剩余天数']}</td>
        </tr>'''
    
    model_table_rows = ''
    for _, row in model_counts.iterrows():
        pct = (row['数量'] / len(df) * 100)
        model_table_rows += f'''<tr><td>{row['设备型号']}</td><td>{row['数量']}</td><td>{pct:.1f}%</td></tr>'''
    
    os_table_rows = ''
    for _, row in os_counts.iterrows():
        pct = (row['数量'] / len(df) * 100)
        os_table_rows += f'''<tr><td>{row['ONTAP版本']}</td><td>{row['数量']}</td><td>{pct:.1f}%</td></tr>'''
    
    maintenance_1y = len([w for w in warning_list if w['类型'] == '维保到期' and w['剩余天数'] <= 365])
    oseols_1y = len([w for w in warning_list if w['类型'] == 'OS EoLS' and w['剩余天数'] <= 365])
    hweos_1y = len([w for w in warning_list if w['类型'] == '硬件EOS' and w['剩余天数'] <= 365])
    
    html_content = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{prefix}设备盘点分析报告</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <style>
        body {{ font-family: 'Microsoft YaHei', sans-serif; background: #f5f7fa; padding: 20px; }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        h1 {{ text-align: center; color: #333; margin-bottom: 30px; font-size: 28px; }}
        h2 {{ color: #4472C4; margin: 20px 0 15px; font-size: 20px; border-bottom: 2px solid #4472C4; padding-bottom: 8px; }}
        .chart-box {{ background: white; border-radius: 8px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); padding: 20px; margin-bottom: 25px; }}
        .chart-title {{ font-size: 16px; font-weight: bold; color: #333; margin-bottom: 10px; text-align: center; }}
        .chart-subtitle {{ font-size: 12px; color: #888; text-align: center; margin-bottom: 15px; }}
        .chart {{ width: 100%; height: 400px; }}
        .grid-container {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(500px, 1fr)); gap: 20px; }}
        .summary-table {{ width: 100%; border-collapse: collapse; margin-top: 15px; }}
        .summary-table th, .summary-table td {{ border: 1px solid #ddd; padding: 12px; text-align: center; font-size: 13px; }}
        .summary-table th {{ background: #4472C4; color: white; font-weight: bold; }}
        .summary-table tr:nth-child(even) {{ background: #f9f9fa; }}
        .warning {{ color: #E74C3C; font-weight: bold; }}
        .caution {{ color: #F39C12; font-weight: bold; }}
        .normal {{ color: #27AE60; font-weight: bold; }}
        .note {{ font-size: 12px; color: #888; margin-top: 10px; padding: 10px; background: #f8f9fa; border-radius: 4px; }}
        .summary-box {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 25px; }}
        .summary-item {{ background: white; padding: 20px; border-radius: 8px; text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
        .summary-item .number {{ font-size: 36px; font-weight: bold; color: #4472C4; }}
        .summary-item .label {{ font-size: 14px; color: #666; margin-top: 5px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 {prefix}设备盘点分析报告</h1>
        
        <div class="summary-box">
            <div class="summary-item">
                <div class="number">{len(df)}</div>
                <div class="label">设备总数</div>
            </div>
            <div class="summary-item">
                <div class="number">{df['Model'].nunique()}</div>
                <div class="label">设备型号</div>
            </div>
            <div class="summary-item">
                <div class="number">{df['OS版本大版本'].nunique()}</div>
                <div class="label">ONTAP版本</div>
            </div>
            <div class="summary-item">
                <div class="number caution">{maintenance_1y}</div>
                <div class="label">维保1年内到期</div>
            </div>
            <div class="summary-item">
                <div class="number caution">{oseols_1y}</div>
                <div class="label">OS EoLS 1年内到期</div>
            </div>
            <div class="summary-item">
                <div class="number caution">{hweos_1y}</div>
                <div class="label">硬件EOS 1年内到期</div>
            </div>
        </div>
        
        <div class="chart-box">
            <h2>设备型号分布</h2>
            <div class="grid-container">
                <div id="chart-model-pie" class="chart"></div>
                <div>
                    <h3 style="color:#666;margin:0 0 15px;">设备型号统计表</h3>
                    <table class="summary-table">
                        <tr><th>设备型号</th><th>数量</th><th>占比</th></tr>
                        {model_table_rows}
                    </table>
                </div>
            </div>
        </div>
        
        <div class="chart-box">
            <h2>ONTAP版本分布</h2>
            <div class="grid-container">
                <div id="chart-os-pie" class="chart"></div>
                <div>
                    <h3 style="color:#666;margin:0 0 15px;">ONTAP版本统计表</h3>
                    <table class="summary-table">
                        <tr><th>ONTAP版本</th><th>数量</th><th>占比</th></tr>
                        {os_table_rows}
                    </table>
                </div>
            </div>
        </div>
        
        <div class="chart-box">
            <h2>EOS与维保状态统计</h2>
            <div id="chart-eos-bar" class="chart"></div>
        </div>
        
        <div class="chart-box">
            <h2>四、即将到期预警（1年内）</h2>
            <table class="summary-table">
                <tr><th>System Name</th><th>Cluster Name</th><th>类型</th><th>到期日期</th><th>剩余天数</th></tr>
                {warning_table_rows}
            </table>
            <div class="note">
                <strong>预警规则：</strong>
                <span class="warning">红色（≤90天）- 3个月内到期</span> | 
                <span class="caution">黄色（≤180天）- 6个月内到期</span> | 
                <span>普通（≤365天）- 1年内到期</span>
            </div>
        </div>
    </div>
    
    <script>
        var charts = {json.dumps(charts, ensure_ascii=False, default=_json_default)};
        
        var modelChart = echarts.init(document.getElementById('chart-model-pie'));
        modelChart.setOption({{
            tooltip: {{trigger: 'item', formatter: '{{b}}: {{c}} ({{d}}%)'}},
            legend: {{bottom: 0, left: 'center', type: 'scroll'}},
            series: [{{type: 'pie', radius: ['40%', '70%'], data: charts.modelPie}}]
        }});
        
        var osChart = echarts.init(document.getElementById('chart-os-pie'));
        osChart.setOption({{
            tooltip: {{trigger: 'item', formatter: '{{b}}: {{c}} ({{d}}%)'}},
            legend: {{bottom: 0, left: 'center', type: 'scroll'}},
            series: [{{type: 'pie', radius: ['40%', '70%'], data: charts.osPie}}]
        }});
        
        var eosChart = echarts.init(document.getElementById('chart-eos-bar'));
        eosChart.setOption({{
            tooltip: {{trigger: 'axis', axisPointer: {{type: 'shadow'}}}},
            legend: {{data: ['正常', '180天内过期', '90天内过期', '已过期', '暂无数据'], bottom:0}},
            grid: {{left:'3%', right:'4%', bottom:'15%', top:'10%', containLabel:true}},
            xAxis: {{type: 'category', data: charts.eosBar.categories}},
            yAxis: {{type: 'value', name: '数量'}},
            series: [
                {{name: '正常', type: 'bar', stack: 'total', data: charts.eosBar['正常'], itemStyle: {{color: '#27AE60'}}}},
                {{name: '180天内过期', type: 'bar', stack: 'total', data: charts.eosBar['180天内过期'], itemStyle: {{color: '#F1C40F'}}}},
                {{name: '90天内过期', type: 'bar', stack: 'total', data: charts.eosBar['90天内过期'], itemStyle: {{color: '#F39C12'}}}},
                {{name: '已过期', type: 'bar', stack: 'total', data: charts.eosBar['已过期'], itemStyle: {{color: '#E74C3C'}}}},
                {{name: '暂无数据', type: 'bar', stack: 'total', data: charts.eosBar['暂无数据'], itemStyle: {{color: '#BDC3C7'}}}}
            ]
        }});
        
        window.addEventListener('resize', function() {{
            modelChart.resize();
            osChart.resize();
            eosChart.resize();
        }});
    </script>
</body>
</html>'''
    
    html_output = os.path.join(output_dir, f'{prefix}设备盘点分析报告.html')
    with open(html_output, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f"  设备盘点HTML报告已保存: {html_output}")

def generate_all_device_reports(source_base, output_base):
    """为所有客户目录生成设备盘点报告"""
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
                        generate_device_report(source_dir, output_dir)
                    except Exception as e:
                        print("  report failed (%s): %s" % (dir_name, e))
        else:
            output_dir = output_base
            os.makedirs(output_dir, exist_ok=True)
            generate_device_report(source_base, output_dir)
