import argparse
import hashlib
import importlib.metadata
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from project_settings import configuration_path, runtime_roots


PROJECT = Path(__file__).resolve().parents[1]
WORKSPACE = PROJECT.parent
EXTENSIONS = {'.py', '.json', '.md', '.txt', '.bat', '.yaml', '.yml', '.toml', '.lock', '.in', '.mjs', '.ps1'}
EXCLUDED = {'__pycache__', 'output', 'outputs', '.git', '.venv', 'node_modules', '.local'}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def source_roots():
    roots = runtime_roots(PROJECT)
    roots['managed-project'] = PROJECT
    return roots


def source_files():
    for label, root in source_roots().items():
        if not root.is_dir():
            raise FileNotFoundError(root)
        for path in sorted(root.rglob('*')):
            relative = path.relative_to(root)
            if path.is_file() and (path.suffix.lower() in EXTENSIONS or path.name == '.gitignore'):
                if not any(part.lower() in EXCLUDED for part in relative.parts):
                    yield label, relative, path
    for name in ('AGENTS.md', 'README.md', '.gitignore', '.gitattributes'):
        path = WORKSPACE / name
        if path.is_file():
            yield 'workspace-policy', Path(name), path
    active_config = configuration_path(PROJECT)
    if not active_config.is_relative_to(PROJECT) or any(part.lower() in EXCLUDED for part in active_config.relative_to(PROJECT).parts):
        yield 'active-configuration', Path('project.json'), active_config


def workbook_signature(path):
    from openpyxl import load_workbook

    workbook = load_workbook(path, data_only=False)
    sheets = []
    try:
        for sheet in workbook:
            values = list(sheet.values)
            payload = json.dumps(values, ensure_ascii=False, default=str).encode('utf-8')
            sheets.append({
                'name': sheet.title, 'rows': sheet.max_row, 'columns': sheet.max_column,
                'values_sha256': hashlib.sha256(payload).hexdigest(),
                'header': list(values[0]) if values else [],
                'merged_ranges': sorted(str(item) for item in sheet.merged_cells.ranges),
                'hyperlinks': {cell.coordinate: cell.hyperlink.location or cell.hyperlink.target
                               for row in sheet for cell in row if cell.hyperlink},
            })
    finally:
        workbook.close()
    return {'path': str(path), 'sha256': digest(path), 'sheets': sheets,
            'status': 'existing_output_reference_not_independent_source_verification'}


def create():
    destination = PROJECT / '.local' / 'baselines' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    destination.mkdir(parents=True, exist_ok=False)
    records = []
    for label, relative, source in source_files():
        target = destination / 'files' / label / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        original_hash = digest(source)
        shutil.copy2(source, target)
        if digest(target) != original_hash:
            raise RuntimeError(f'Backup verification failed: {source}')
        records.append({'source': str(source), 'backup': str(target.relative_to(destination)),
                        'sha256': original_hash, 'bytes': source.stat().st_size})
    reports = []
    for customer in sorted(path.name for path in (WORKSPACE / 'Report').iterdir() if path.is_dir()) if (WORKSPACE / 'Report').is_dir() else ():
        candidates = list((WORKSPACE / 'Report' / customer).glob('SAM_Report_*.xlsx'))
        if candidates:
            reports.append(workbook_signature(max(candidates, key=lambda path: path.stat().st_mtime_ns)))
    manifest = {
        'created_at': datetime.now().astimezone().isoformat(), 'python': sys.version,
        'python_executable': sys.executable,
        'dependencies': {name: importlib.metadata.version(name) for name in ('openpyxl', 'pandas', 'py7zr')},
        'sources': records, 'reports': reports,
        'scope': 'Code backup and existing-output comparison baseline; no customer report generated.',
    }
    manifest_path = destination / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(json.dumps({'manifest': str(manifest_path), 'files': len(records),
                      'report_references': len(reports)}, ensure_ascii=False))


def verify(manifest_path, restore=False):
    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    checked = []
    for record in manifest['sources']:
        backup = (manifest_path.parent / record['backup']).resolve()
        backup.relative_to(manifest_path.parent / 'files')
        if digest(backup) != record['sha256']:
            raise RuntimeError(f'Backup corrupted: {backup}')
        checked.append((record, backup))
    if restore:
        for record, backup in checked:
            source = Path(record['source'])
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, source)
    print(json.dumps({'verified_files': len(checked), 'restored': restore}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify', type=Path)
    parser.add_argument('--restore', type=Path)
    arguments = parser.parse_args()
    if arguments.verify or arguments.restore:
        verify(arguments.verify or arguments.restore, restore=arguments.restore is not None)
    else:
        create()
