"""保留原入口；按完整卷身份、真实采样月份和可追溯空值生成趋势。"""
import html
import json
import os
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from report_dates import select_report_files, parse_report_period
from trend_model import SITE_COLUMNS, growth, number, text, volume_series, index_rows, dataset_fingerprint


SITE_COLUMN_CANDIDATES = SITE_COLUMNS
CLUSTER_FILTER = None


def set_cluster_filter(clusters):
    global CLUSTER_FILTER
    CLUSTER_FILTER = [str(cluster).strip() for cluster in clusters] if clusters else None
    return CLUSTER_FILTER


def get_site_name(row):
    return next((text(row[column].iloc[0]) for column in SITE_COLUMNS if column in row.columns and not row.empty), '')


def extract_data(base_dir):
    files = select_report_files(base_dir, ('Controller Capacity', 'Volume List'))
    cluster_data, volume_data, mappings = {}, {}, {}
    datasets = {}
    for month, path in files.items():
        controllers = pd.read_excel(path, sheet_name='Controller Capacity')
        volumes = pd.read_excel(path, sheet_name='Volume List')
        if CLUSTER_FILTER is not None:
            controllers = controllers[controllers['Cluster Name'].isin(CLUSTER_FILTER)]
            volumes = volumes[volumes['Cluster Name'].isin(CLUSTER_FILTER)]
        controllers['Used (TB)'] = pd.to_numeric(controllers['Used (TB)'], errors='coerce')
        volumes = volumes[~volumes['Volume Name'].astype(str).str.lower().str.startswith('fg_oss')].copy()
        volumes['Used (TB)'] = pd.to_numeric(volumes['Used Size (TB)'], errors='coerce')
        volumes['Files Used (亿)'] = pd.to_numeric(volumes['Files Used (亿)'], errors='coerce')
        for frame in (controllers, volumes):
            frame.attrs.update(source=path, period=parse_report_period(path))
        fingerprint = dataset_fingerprint(index_rows(volumes.to_dict('records')))
        if not volumes.attrs['period'][2]:
            if fingerprint in datasets:
                volumes.attrs['duplicate_dataset_of'] = datasets[fingerprint]
            datasets.setdefault(fingerprint, month)
        cluster_data[month], volume_data[month] = controllers, volumes
        for row in controllers.to_dict('records'):
            node, cluster = text(row.get('Node')), text(row.get('Cluster Name'))
            if node in mappings and mappings[node] != cluster:
                raise ValueError('同名节点跨集群，不能使用旧的单节点映射: ' + node)
            mappings[node] = cluster
    return cluster_data, volume_data, list(files), mappings


def get_cluster_groups(controllers, controller_to_cluster):
    groups = {}
    for controller in sorted(controllers):
        groups.setdefault(controller_to_cluster.get(controller, controller), []).append(controller)
    return dict(sorted(groups.items()))


def controller_series(cluster_data, months):
    indexes, keys = {}, set()
    for month in months:
        indexed = {}
        for row in cluster_data[month].to_dict('records'):
            key = (text(row.get('Cluster Name')), text(row.get('Node')))
            if not all(key) or key in indexed:
                raise ValueError('控制器身份缺失或重复: ' + repr(key))
            indexed[key] = row
        indexes[month] = indexed
        keys.update(indexed)
    result = []
    for key in sorted(keys):
        values = [number(indexes[month].get(key, {}).get('Used (TB)')) for month in months]
        delta, percent = growth(values)
        result.append({'cluster': key[0], 'node': key[1], 'label': ' / '.join(key), 'values': values,
                       'growth': delta, 'growth_percent': percent})
    return result


def display(value):
    return 'N/A' if value is None else round(value, 2)


def style_sheet(sheet):
    side = Side(style='thin', color='D9E2F3')
    for row in sheet:
        for cell in row:
            cell.font = Font(name='微软雅黑', size=11 if cell.row == 1 else 10,
                             bold=cell.row == 1, color='1F4E78' if cell.row == 1 else '222222')
            cell.fill = PatternFill('solid', fgColor='BDD7EE' if cell.row == 1 else ('F2F6FC' if cell.row % 2 == 0 else 'FFFFFF'))
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            cell.border = Border(left=side, right=side, top=side, bottom=side)
            if sheet.cell(1, cell.column).value == '增长率(%)' and cell.row > 1 and number(cell.value) is not None and number(cell.value) > 20:
                cell.fill = PatternFill('solid', fgColor='FFC7CE')
    for column in sheet.columns:
        sheet.column_dimensions[column[0].column_letter].width = 32 if 'Volume' in str(column[0].value) else 21
    sheet.row_dimensions[1].height = 36
    sheet.freeze_panes = 'D2'
    sheet.auto_filter.ref = sheet.dimensions


def tables(cluster_data, volume_data, months):
    result = [('集群容量趋势', ['集群名称', '控制器'] + [f'{month}容量(TB)' for month in months] + ['容量增长(TB)', '增长率(%)'],
               [[item['cluster'], item['node']] + [display(value) for value in item['values']] + [display(item['growth']), display(item['growth_percent'])]
                for item in controller_series(cluster_data, months)])]
    for title, metric, unit, delta_title in [('Volume容量趋势', 'Used (TB)', '容量(TB)', '容量增长(TB)'),
                                          ('Volume Inode趋势', 'Files Used (亿)', 'Inode(亿)', 'Inode增长(亿)')]:
        result.append((title, ['Volume名称', '站点', 'Cluster Name'] + [f'{month}{unit}' for month in months] + [delta_title, '增长率(%)'],
                       [[item['volume'], item['site'], item['cluster']] + [display(value) for value in item['values']] + [display(item['growth']), display(item['growth_percent'])]
                        for item in volume_series(volume_data, months, metric)]))
    return result


def create_excel_report(cluster_data, volume_data, months, controller_to_cluster, output_dir, prefix):
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, header, rows in tables(cluster_data, volume_data, months):
        sheet = workbook.create_sheet(title)
        sheet.append(header)
        for row in rows:
            sheet.append(row)
        style_sheet(sheet)
    sources = workbook.create_sheet('采样与口径')
    sources.append(['月份', '采样日期/月份', '日期精度', '源SAM报告', '数据说明'])
    for month in months:
        year, month_number, day = volume_data[month].attrs['period']
        date_text = f'{year or "年份未明确"}-{month_number:02}' + (f'-{day:02}' if day else '')
        duplicate = volume_data[month].attrs.get('duplicate_dataset_of')
        sources.append([month, date_text, '日' if day else '月', volume_data[month].attrs['source'],
                        f'与{duplicate}的Volume List完全相同；月精度，不能证实独立采样' if duplicate else '按源SAM记录展示，缺年/日不猜测'])
    sources.append(['口径', '使用量增长不是卷扩容；首末任一缺测则增长N/A；起点0则增长率N/A；中间缺测不补0。'])
    sources.merge_cells(start_row=sources.max_row, start_column=2, end_row=sources.max_row, end_column=5)
    sources.append(['身份', '按Cluster + SVM + Volume匹配；Convert不受趋势TOP20限制，直接读取历史SAM全部Volume List。'])
    sources.merge_cells(start_row=sources.max_row, start_column=2, end_row=sources.max_row, end_column=5)
    style_sheet(sources)
    sources.column_dimensions['D'].width = 90
    sources.column_dimensions['E'].width = 60
    for row_number in range(2, sources.max_row + 1):
        sources.row_dimensions[row_number].height = 42
    sources.auto_filter.ref = f'A1:E{len(months)+1}'
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    output = os.path.join(output_dir, prefix + '容量趋势分析报告.xlsx')
    workbook.save(output)
    return output


def generate_html_report(cluster_data, volume_data, months, controller_to_cluster, output_dir, prefix):
    charts = []
    controllers = controller_series(cluster_data, months)
    for cluster in sorted({item['cluster'] for item in controllers}):
        charts.append({'title': cluster + ' 集群容量趋势', 'unit': 'TB',
                       'series': [{'name': item['node'], 'data': item['values']} for item in controllers if item['cluster'] == cluster]})
    for metric, title, unit in [('Used (TB)', 'Volume容量趋势', 'TB'), ('Files Used (亿)', 'Volume Inode趋势', '亿')]:
        items = volume_series(volume_data, months, metric)
        charts.append({'title': title + '总览（TOP 20）', 'unit': unit,
                       'series': [{'name': item['label'], 'data': item['values']} for item in items]})
        for item in items:
            charts.append({'title': item['label'] + ' ' + title, 'unit': unit,
                           'series': [{'name': item['label'], 'data': item['values']}]})
    boxes = ''.join(f'<section><h2>{html.escape(chart["title"])}</h2><div class="chart" id="chart-{index}"></div></section>' for index, chart in enumerate(charts))
    table_html = ''
    for title, header, rows in tables(cluster_data, volume_data, months):
        table_html += '<section><h2>' + html.escape(title) + '</h2><div class="scroll"><table><thead><tr>'
        table_html += ''.join('<th>' + html.escape(str(value)) + '</th>' for value in header) + '</tr></thead><tbody>'
        table_html += ''.join('<tr>' + ''.join('<td>' + html.escape(str(value)) + '</td>' for value in row) + '</tr>' for row in rows)
        table_html += '</tbody></table></div></section>'
    payload = json.dumps({'months': months, 'charts': charts}, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c')
    content = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title><script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>body{font-family:"Microsoft YaHei",sans-serif;background:#f4f7fb;color:#222;margin:30px}section{background:white;padding:24px;margin:20px 0;border-radius:10px}.chart{height:470px}.scroll{overflow:auto}table{border-collapse:collapse;width:100%}th,td{border:1px solid #d9e2f3;padding:10px;text-align:center}th{background:#bdd7ee;color:#1f4e78}tbody tr:nth-child(even){background:#f2f6fc}</style>
<h1>__TITLE__</h1><p>缺测显示N/A，图线保留断点；真实0保留为0。增长按首末已用量计算，不将扩容当作数据增长。图表需联网加载ECharts，表格可离线查看。</p>
__BOXES____TABLES__<script id="trend-data" type="application/json">__DATA__</script><script>
const payload=JSON.parse(document.getElementById('trend-data').textContent);
payload.charts.forEach((config,index)=>{const chart=echarts.init(document.getElementById('chart-'+index));chart.setOption({tooltip:{trigger:'axis',valueFormatter:value=>value==null?'N/A':value+' '+config.unit},legend:{type:'scroll',bottom:0},grid:{left:80,right:25,bottom:85},xAxis:{type:'category',data:payload.months},yAxis:{type:'value',name:config.unit},series:config.series.map(item=>({...item,type:'line',connectNulls:false,smooth:false,symbolSize:7}))});window.addEventListener('resize',()=>chart.resize());});
</script></html>'''
    warnings = [f'{month}与{frame.attrs["duplicate_dataset_of"]}的Volume List完全相同，只有月精度，不能证实独立采样。' for month, frame in volume_data.items() if frame.attrs.get('duplicate_dataset_of')]
    notice = '<p>' + html.escape(' '.join(warnings)) + '</p>' if warnings else ''
    content = content.replace('__TITLE__', html.escape(prefix + '容量趋势分析报告')).replace('__BOXES__', notice + boxes).replace('__TABLES__', table_html).replace('__DATA__', payload)
    output = os.path.join(output_dir, prefix + '容量趋势分析报告.html')
    Path(output).write_text(content, encoding='utf-8')
    return output


def generate_capacity_report(source_dir, output_dir):
    cluster_data, volume_data, months, mappings = extract_data(source_dir)
    if not months:
        print('没有有效的SAM采样数据: ' + source_dir)
        return
    prefix = os.path.basename(source_dir.rstrip('\\/'))
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    excel = create_excel_report(cluster_data, volume_data, months, mappings, output_dir, prefix)
    webpage = generate_html_report(cluster_data, volume_data, months, mappings, output_dir, prefix)
    print('容量趋势报告:', excel, webpage)
    return excel, webpage


def generate_all_capacity_reports(source_base, output_base):
    directories = [path for path in Path(source_base).iterdir() if path.is_dir()]
    for path in directories or [Path(source_base)]:
        try:
            generate_capacity_report(str(path), str(Path(output_base) / path.name) if directories else output_base)
        except Exception as error:
            print('容量趋势生成失败:', path, error)
            raise
