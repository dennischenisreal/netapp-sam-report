import json
import re
import warnings
from zipfile import BadZipFile
from datetime import date
from pathlib import Path

from openpyxl import load_workbook


def parse_report_period(path):
    path = Path(path)
    sidecar = path.with_suffix('.run.json')
    if sidecar.is_file():
        metadata = json.loads(sidecar.read_text(encoding='utf-8'))
        period = metadata.get('period', {})
        sample_date = period.get('sample_date') or metadata.get('sample_date')
        if sample_date:
            sampled = date.fromisoformat(sample_date[:10])
            return sampled.year, sampled.month, sampled.day
        if period.get('month'):
            year, month = period.get('year'), int(period['month'])
            if year is not None:
                date(int(year), month, 1)
            elif not 1 <= month <= 12:
                raise ValueError('Invalid month in report metadata')
            return int(year) if year is not None else None, month, 0
    month_match = re.search(r'(?<!\d)(1[0-2]|0?[1-9])月(?:(3[01]|[12]\d|0?[1-9])日?)?', path.stem)
    if month_match:
        year_match = re.search(r'(?<!\d)(20\d{2})年', path.stem[:month_match.start()])
        if not year_match:
            year_match = re.search(r'(?<!\d)(20\d{2})年?(?!\d)', str(path.parent))
        year = int(year_match[1]) if year_match else None
        month, day = int(month_match[1]), int(month_match[2]) if month_match[2] else 0
        date(year or 2000, month, day or 1)
        return year, month, day
    compact = re.search(r'(?<!\d)(20\d{2})[-_]?([01]\d)[-_]?([0-3]\d)(?!\d)', path.stem)
    if compact:
        sampled = date(*map(int, compact.groups()))
        return sampled.year, sampled.month, sampled.day
    return None


def select_report_files(base_dir, required_sheets=()):
    candidates = []
    for path in sorted(Path(base_dir).iterdir()):
        if not path.is_file() or path.suffix.lower() != '.xlsx' or path.name.startswith('~$'):
            continue
        try:
            period = parse_report_period(path)
            if period is None:
                warnings.warn(f'未识别报告所属日期，已跳过: {path.name}', stacklevel=2)
                continue
            if required_sheets:
                workbook = load_workbook(path, read_only=True)
                try:
                    if not set(required_sheets).issubset(workbook.sheetnames):
                        warnings.warn(f'报告缺少所需Sheet，已跳过: {path.name}', stacklevel=2)
                        continue
                finally:
                    workbook.close()
            candidates.append((path, period))
        except (ValueError, OSError, KeyError, TypeError, BadZipFile, json.JSONDecodeError) as error:
            warnings.warn(f'报告日期或文件无法读取，已跳过 {path.name}: {error}', stacklevel=2)
    years = {period[0] for _, period in candidates if period[0] is not None}
    selected = {}
    ranks = {}
    for path, (year, month, day) in candidates:
        if year is None and len(years) > 1:
            warnings.warn(f'跨年数据中报告年份不明确，已跳过: {path.name}', stacklevel=2)
            continue
        if year is None and len(years) == 1:
            year = next(iter(years))
            warnings.warn(f'旧报告缺少年份，按同目录唯一明确年份 {year} 归类: {path.name}', stacklevel=2)
        key = (year or 0, month)
        rank = (day, path.stat().st_mtime_ns, path.name)
        if key not in ranks or rank > ranks[key]:
            selected[key], ranks[key] = path, rank
    cross_year = len({year for year, _ in selected}) > 1
    files = {}
    for (year, month), path in sorted(selected.items()):
        label = f'{year}年{month}月' if cross_year else f'{month}月'
        files[label] = str(path)
    return files
