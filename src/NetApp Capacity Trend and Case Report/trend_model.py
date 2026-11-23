import calendar
import hashlib
import math
import json
from datetime import date
from pathlib import Path

from openpyxl import load_workbook
from report_dates import parse_report_period, select_report_files


SITE_COLUMNS = ('SVM名字', 'Site Name', 'SVM Name', 'SVM')
TB = 1024 ** 4


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(str(value).strip().rstrip('%'))
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError):
        return None


def text(value):
    return '' if value is None or str(value).strip().lower() in ('nan', 'n/a', 'none') else str(value).strip()


def identity(row):
    key = (text(row.get('Cluster Name')), next((text(row.get(column)) for column in SITE_COLUMNS if text(row.get(column))), ''), text(row.get('Volume Name')))
    if not all(key):
        raise ValueError('趋势卷身份不完整，不能按卷名猜测匹配: ' + repr(key))
    return key


def growth(values):
    if not values or values[0] is None or values[-1] is None:
        return None, None
    delta = values[-1] - values[0]
    return delta, delta / values[0] * 100 if values[0] > 0 else None


def index_rows(rows):
    indexed = {}
    for row in rows:
        key = identity(row)
        if key in indexed and indexed[key] != row:
            raise ValueError('同一采样期存在冲突卷记录: ' + repr(key))
        indexed[key] = row
    return indexed


def dataset_fingerprint(indexed):
    payload = [(key, sorted((str(name), value) for name, value in row.items())) for key, row in sorted(indexed.items())]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, default=str).encode('utf-8')).hexdigest()


def volume_series(volume_data, months, metric='Used (TB)', top_n=20, cluster=None):
    indexes = {month: index_rows(volume_data[month].to_dict('records')) for month in months}
    latest = indexes[months[-1]]
    ranked = sorted(((key, number(row.get(metric))) for key, row in latest.items()
                     if (cluster is None or key[0] == cluster) and number(row.get(metric)) is not None),
                    key=lambda item: (-item[1], item[0]))[:top_n]
    results = []
    for key, _ in ranked:
        values = [number(indexes[month].get(key, {}).get(metric)) for month in months]
        delta, percent = growth(values)
        results.append({'cluster': key[0], 'site': key[1], 'volume': key[2],
                        'label': ' / '.join(key), 'values': values,
                        'growth': delta, 'growth_percent': percent,
                        'data': [{'month': month, 'value': value} for month, value in zip(months, values)]})
    return results


def load_history(source_dirs, year_hint=None, cutoff=None):
    selected = {}
    warnings = []
    for source in source_dirs:
        if not Path(source).is_dir():
            warnings.append('历史SAM目录不存在: ' + str(source))
            continue
        for report in select_report_files(source, ('Volume List',)).values():
            path = Path(report)
            year, month, day = parse_report_period(path)
            inferred = year is None
            year = year or year_hint
            if year is None:
                warnings.append('报告年份不明确，未用于Convert: ' + str(path))
                continue
            sampled = date(year, month, day or calendar.monthrange(year, month)[1])
            if cutoff and (sampled > cutoff or (not day and (year, month) == (cutoff.year, cutoff.month))):
                continue
            rank = (sampled, path.stat().st_mtime_ns, str(path))
            key = (year, month)
            if key not in selected or rank > selected[key][0]:
                selected[key] = (rank, path, 'month' if not day else 'day', inferred)
    history = {}
    sources = []
    datasets = {}
    for _, (rank, path, precision, inferred) in sorted(selected.items()):
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        source = {'path': str(path), 'sha256': digest, 'sample_date': rank[0].isoformat(),
                  'date_precision': precision, 'year_from_log_context': inferred}
        sources.append(source)
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            iterator = workbook['Volume List'].values
            headers = next(iterator)
            rows = [dict(zip(headers, values)) for values in iterator if any(value is not None for value in values)]
            indexed = index_rows(rows)
            source['dataset_sha256'] = dataset_fingerprint(indexed)
            if precision == 'month' and source['dataset_sha256'] in datasets:
                source['duplicate_dataset_of'] = datasets[source['dataset_sha256']]
                warnings.append('仅有月份的历史SAM存在完全相同Volume List，不重复计为独立采样: ' + str(path))
            if precision == 'month':
                datasets.setdefault(source['dataset_sha256'], str(path))
            for key, row in indexed.items():
                history.setdefault(key, []).append({**source, 'used_tb': number(row.get('Used Size (TB)')),
                                                    'size_tb': number(row.get('Volume Size (TB)'))})
        finally:
            workbook.close()
    return history, sources, warnings


def evaluate_convert(current, history, asof):
    """约六个月采用5至7个日历月、至少4个有效月份、相邻不超过3个月；不做外推。"""
    result = {'eligible': False, 'status': 'insufficient_history', 'basis': 'Used Size (TB)',
              'end_date': asof.isoformat(), 'sample_count': 0}
    if current['size_tb'] is None or current['used_pct'] is None or current['used_tb'] is None:
        return {**result, 'status': 'missing_current_metrics'}
    if current['size_tb'] < 30 or current['used_pct'] < 50:
        return {**result, 'status': 'below_current_threshold'}
    end_month = asof.year * 12 + asof.month
    points = {}
    for item in history:
        sampled = date.fromisoformat(item['sample_date'])
        ordinal = sampled.year * 12 + sampled.month
        if sampled <= asof and ordinal < end_month and number(item.get('used_tb')) is not None:
            points[ordinal] = item
    baseline = [ordinal for ordinal in points if 5 <= end_month - ordinal <= 7]
    if not baseline:
        return result
    start_month = min(baseline, key=lambda ordinal: (abs(end_month - ordinal - 6), -ordinal))
    window = sorted(ordinal for ordinal in points if start_month <= ordinal < end_month)
    points_in_window = [points[ordinal] for ordinal in window]
    effective = []
    sampling_keys = set()
    for ordinal in window:
        point = points[ordinal]
        sample_key = ('dataset', point['dataset_sha256']) if point.get('date_precision') == 'month' and point.get('dataset_sha256') else ('month', ordinal)
        if sample_key not in sampling_keys:
            effective.append(ordinal)
            sampling_keys.add(sample_key)
    ordinals = effective + [end_month]
    start = points[start_month]
    start_used = start['used_tb']
    delta, percent = growth([start_used, current['used_tb']])
    size_start = start.get('size_tb')
    result.update({'start_date': start['sample_date'], 'start_precision': start['date_precision'],
                   'elapsed_months': end_month - start_month, 'sample_count': len(ordinals),
                   'reported_months': len(window) + 1,
                   'start_used_tb': start_used, 'end_used_tb': current['used_tb'],
                   'growth_tb': delta, 'growth_percent': percent,
                   'provisioned_growth_tb': current['size_tb'] - size_start if size_start is not None else None,
                   'sources': points_in_window})
    if len(ordinals) < 4 or max(right - left for left, right in zip(ordinals, ordinals[1:])) > 3:
        return result
    if percent is None:
        return {**result, 'status': 'zero_baseline'}
    return {**result, 'eligible': percent >= 25, 'status': 'recommended' if percent >= 25 else 'below_growth_threshold'}
