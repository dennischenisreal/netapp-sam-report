'Shared candidate: use explicit local input, configuration and output paths.'
import os
import sys
import json
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

import generate_capacity_report as cap


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>__TITLE__</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <style>
        body { font-family: 'Microsoft YaHei', sans-serif; background: #f5f7fa; padding: 20px; }
        .container { max-width: 1400px; margin: 0 auto; }
        h1 { text-align: center; color: #333; margin-bottom: 30px; font-size: 28px; }
        h2 { color: #4472C4; margin: 20px 0 15px; font-size: 20px; border-bottom: 2px solid #4472C4; padding-bottom: 8px; }
        .chart-box { background: white; border-radius: 8px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); padding: 20px; margin-bottom: 25px; }
        .chart { width: 100%; height: 420px; }
        .note { font-size: 12px; color: #888; margin-top: 10px; padding: 10px; background: #f8f9fa; border-radius: 4px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>__TITLE__</h1>
        <div class="note"><strong>说明：</strong>按集群分别统计各集群内 Volume 容量最大的 TOP 10，图表格式与容量趋势报告中的"Volume容量趋势总览（TOP 10）"一致。数据来源：Volume List Sheet（Used Size (TB)）。</div>
        __BOXES__
    </div>
    <script>
        var charts = __CHARTS__;
        function initChart(id, config) {
            var chart = echarts.init(document.getElementById(id));
            chart.setOption({
                tooltip: {trigger:'axis', formatter: function(p){ var r = p[0].axisValue + '<br/>'; p.forEach(function(item){ var unit = config.title.includes('Inode') ? ' 亿' : ' TB'; r += item.marker + item.seriesName + ': ' + item.value + unit + '<br/>'; }); return r; }},
                legend: {data: config.series.map(function(s){return s.name;}), bottom:0, itemWidth:25, itemHeight:10},
                grid: {left:'3%', right:'4%', bottom:'15%', top:'10%', containLabel:true},
                xAxis: {type:'category', boundaryGap:false, data:config.xAxis},
                yAxis: {type:'value', name:'容量 (TB)'},
                series: config.series.map(function(s){ return { name: s.name, type: config.type, smooth: s.smooth || false, data: s.data, symbol:'circle', symbolSize:8, lineStyle:{width:3}, itemStyle:{borderWidth:2, borderColor:'#fff'} }; })
            });
            window.addEventListener('resize', function(){chart.resize();});
        }
        charts.forEach(function(config, idx){ initChart('chart-cluster-' + idx, config); });
    </script>
</body>
</html>"""


def build_cluster_volume_trend(volume_data, months, cluster, top_n=10):
    from trend_model import volume_series
    return volume_series(volume_data, months, top_n=top_n, cluster=cluster)


def create_excel(trends_by_cluster, months, output_dir, prefix):
    wb = Workbook()
    wb.remove(wb.active)
    for cluster, trend in trends_by_cluster.items():
        ws = wb.create_sheet(str(cluster)[:31])
        ws.append(['Volume名称', '站点', 'Cluster Name'] + [str(month) + '容量(TB)' for month in months] + ['容量增长(TB)', '增长率(%)'])
        for item in trend:
            ws.append([item['volume'], item['site'], item['cluster']] + [cap.display(value) for value in item['values']] +
                      [cap.display(item['growth']), cap.display(item['growth_percent'])])
        cap.style_sheet(ws)
    output = os.path.join(output_dir, prefix + '集群Volume容量趋势TOP10.xlsx')
    wb.save(output)
    return output


def create_html(trends_by_cluster, months, output_dir, prefix):
    charts = []
    boxes = []
    for idx, (cluster, trend) in enumerate(trends_by_cluster.items()):
        charts.append({
            'title': cluster + ' Volume容量趋势总览（TOP 10）',
            'type': 'line',
            'xAxis': months,
            'series': [{'name': v['label'], 'data': [d['value'] for d in v['data']], 'smooth': False} for v in trend]
        })
        boxes.append('<div class="chart-box"><h2>' + cluster + ' 集群 Volume容量趋势总览（TOP 10）</h2><div id="chart-cluster-' + str(idx) + '" class="chart"></div></div>')

    title = prefix + ' 集群Volume容量趋势（TOP 10）'
    html = HTML_TEMPLATE.replace('__TITLE__', title).replace('__BOXES__', '\n        '.join(boxes)).replace('__CHARTS__', json.dumps(charts, ensure_ascii=False))
    out = os.path.join(output_dir, prefix + '集群Volume容量趋势TOP10.html')
    with open(out, 'w', encoding='utf-8') as f:
        f.write(html)
    print('HTML报告已保存:', out)
    return out


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return
    source_dir = sys.argv[1]
    output_dir = sys.argv[2]
    clusters = [c.strip() for c in sys.argv[3].split(',') if c.strip()]
    top_n = int(sys.argv[4]) if len(sys.argv) > 4 else 10
    prefix = os.path.basename(source_dir.rstrip('\\/'))
    os.makedirs(output_dir, exist_ok=True)

    cap.set_cluster_filter(clusters)
    cluster_data, volume_data, months, controller_to_cluster = cap.extract_data(source_dir)
    if not months:
        print('没有有效数据，退出')
        return

    trends_by_cluster = {}
    for cluster in clusters:
        trends_by_cluster[cluster] = build_cluster_volume_trend(volume_data, months, cluster, top_n)

    create_excel(trends_by_cluster, months, output_dir, prefix)
    create_html(trends_by_cluster, months, output_dir, prefix)
    print('\n完成. 输出目录:', output_dir)


if __name__ == '__main__':
    main()
