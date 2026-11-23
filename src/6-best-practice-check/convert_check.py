import json
import os
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'NetApp Capacity Trend and Case Report'))
from trend_model import TB, evaluate_convert, load_history, number
from sam_runtime import sample_period
from sam_settings import history_roots


HISTORY_ROOTS = [Path(__file__).resolve().parents[2] / '.local/data/history']


def check(input_dir, customer_name, extractor_factory, scan, extract_identity):
    paths = list(scan(input_dir))
    sample = os.environ.get('SAM_SAMPLE_DATE') or sample_period(input_dir, [Path(path) for path in paths]).get('sample_date')
    results = []
    evidence = {'schema_version': 1, 'basis': 'Used Size (TB)', 'sample_date': sample,
                'thresholds': {'size_tb': 30, 'usage_percent': 50, 'growth_percent': 25,
                               'window_months': [5, 7], 'minimum_samples': 4, 'maximum_gap_months': 3},
                'sources': [], 'evaluations': [], 'warnings': []}
    if not sample:
        results.append({'note': True, 'incomplete': True, 'message': '当前ASUP采样日期不明确，Convert建议未评估；不会使用今天代替采样日期。'})
    else:
        asof = date.fromisoformat(sample)
        roots = [Path(os.environ['SAM_HISTORY_ROOT'])] if os.environ.get('SAM_HISTORY_ROOT') else history_roots(HISTORY_ROOTS)
        history, sources, warnings = load_history([root / customer_name for root in roots], year_hint=asof.year, cutoff=asof)
        evidence.update(sources=sources, warnings=warnings)
        current = {}
        uuids = {}
        extractor = extractor_factory()
        try:
            for archive in paths:
                selected = extractor.extract_files(archive, ['volume.xml'])
                path = selected.get('volume.xml')
                if not path:
                    continue
                _, cluster = extract_identity(archive, extractor)
                tree = ET.parse(path)
                for element in tree.iter():
                    if element.tag.rsplit('}', 1)[-1] != 'ROW':
                        continue
                    fields = {child.tag.rsplit('}', 1)[-1]: (child.text or '').strip() for child in element}
                    name, svm = fields.get('vol', ''), fields.get('vs', '')
                    if fields.get('styleEx', '').lower() != 'flexvol' or name.lower() == 'vol0' or name.lower().startswith('fg_oss') or 'bucket' in name.lower():
                        continue
                    if not cluster or not svm or not name:
                        raise ValueError('Convert当前卷缺少完整身份')
                    key = (cluster, svm, name)
                    uuid = fields.get('vol_uuid') or fields.get('uuid', '')
                    if uuid and key in uuids and uuids[key] != uuid:
                        raise ValueError('Convert当前卷UUID冲突: ' + repr(key))
                    if uuid:
                        uuids[key] = uuid
                    size, used = number(fields.get('size')), number(fields.get('used'))
                    record = {'size_tb': size / TB if size is not None else None,
                              'used_tb': used / TB if used is not None else None,
                              'used_pct': number(fields.get('pcnt_used')), 'uuid': uuid, 'archive': str(archive)}
                    previous = current.get(key)
                    if previous and previous['uuid'] and record['uuid'] and previous['uuid'] != record['uuid']:
                        raise ValueError('Convert当前卷UUID冲突: ' + repr(key))
                    if previous is None or (record['size_tb'] is not None and (previous['size_tb'] is None or record['size_tb'] > previous['size_tb'])):
                        current[key] = record
        finally:
            extractor.cleanup()
        for key, record in sorted(current.items()):
            decision = evaluate_convert(record, history.get(key, []), asof)
            evidence['evaluations'].append({'identity': key, 'current': record, **decision})
            if decision['eligible']:
                start = decision['start_date'][:7] if decision['start_precision'] == 'month' else decision['start_date']
                growth = decision['growth_percent']
                suggestion = '建议纳入FlexGroup Convert评估；确认版本/兼容性、业务影响及备份/回退，不代表可直接转换。'
                results.append({'cluster_name': key[0], 'vserver': key[1], 'volume_name': key[2],
                                'size_tb': round(record['size_tb'], 2), 'used_percent': record['used_pct'],
                                'growth_percent': round(growth, 1), 'suggestion': suggestion,
                                'window': f'{start} → {decision["end_date"]}', 'sample_count': decision['sample_count'],
                                'used_range': f'{decision["start_used_tb"]:.2f} → {decision["end_used_tb"]:.2f}'})
        counts = Counter(item['status'] for item in evidence['evaluations'])
        evidence['status_counts'] = dict(counts)
        missing = sum(counts[key] for key in ('insufficient_history', 'zero_baseline', 'missing_current_metrics'))
        results.append({'note': True, 'incomplete': bool(missing),
                        'message': f'Convert按已用容量增长评估普通卷{len(current)}个；建议{counts["recommended"]}个，证据不足{missing}个。使用5–7个月窗口、至少4个有效月份；缺数据不表示无需评估。趋势TOP20不作为全集。'})
        if any(source['year_from_log_context'] for source in sources):
            results.append({'note': True, 'message': f'旧SAM文件缺年份时按当前日志年份{asof.year}归类，并在证据中标注；缺日只作月精度，不冒充具体采样日。'})
        if any(source.get('duplicate_dataset_of') for source in sources):
            results.append({'note': True, 'message': '部分仅有月份的历史SAM拥有完全相同Volume List，不能确认独立采样；已合并采样计数，详细来源见convert_evidence.json，未擅自改写其月份。'})
    output_dir = os.environ.get('SAM_REPORT_OUTPUT_DIR')
    if output_dir:
        Path(output_dir, 'convert_evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    return results
