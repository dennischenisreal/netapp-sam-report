import argparse
import hashlib
import importlib.metadata
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from baseline import source_roots, EXTENSIONS, EXCLUDED
from project_settings import load_configuration, expand_path
from source_manifest import build_manifest, verify_manifest


PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / 'src'
LOCK = PROJECT / 'release.lock.json'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def configuration():
    return load_configuration(PROJECT)


def expanded(value):
    return expand_path(value, PROJECT)


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def imported_files():
    for label, root in source_roots().items():
        if label == 'managed-project':
            continue
        if not root.is_dir():
            raise FileNotFoundError(root)
        for path in sorted(root.rglob('*')):
            relative = path.relative_to(root)
            if path.is_file() and path.suffix.lower() in EXTENSIONS and not any(part.lower() in EXCLUDED for part in relative.parts):
                yield label, relative, path


def import_runtime():
    if SOURCE.exists() or LOCK.exists():
        raise RuntimeError('Managed source already exists; refusing to overwrite it from a runtime copy')
    records = []
    for label, relative, installed in imported_files():
        target = SOURCE / label / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(installed, target)
        checksum = digest(installed)
        if digest(target) != checksum:
            raise RuntimeError('Source import verification failed')
        records.append({'source': target.relative_to(PROJECT).as_posix(), 'installed': str(installed),
                        'sha256': checksum, 'installed_sha256': checksum})
    save(LOCK, {'schema_version': 1, 'version': configuration()['version'],
                'imported_at': datetime.now().astimezone().isoformat(), 'files': records})
    return {'imported_files': len(records), 'source': str(SOURCE), 'deleted_files': 0}


def release_state():
    lock = json.loads(LOCK.read_text(encoding='utf-8'))
    findings = []
    recorded = set()
    roots = source_roots()
    for item in lock['files']:
        source = (PROJECT / item['source']).resolve()
        source.relative_to(SOURCE.resolve())
        installed = Path(item['installed']).resolve()
        relative = source.relative_to(SOURCE.resolve())
        expected = (roots[relative.parts[0]] / Path(*relative.parts[1:])).resolve()
        expected.relative_to(roots[relative.parts[0]].resolve())
        if installed != expected:
            raise ValueError('Deployment map points outside the registered runtime: ' + str(installed))
        recorded.add(source)
        if not source.is_file():
            findings.append({'kind': 'source_missing', 'path': str(source)})
            continue
        checksum = digest(source)
        if checksum != item['sha256']:
            findings.append({'kind': 'source_unsealed', 'path': str(source)})
        if not installed.is_file() or digest(installed) != checksum:
            findings.append({'kind': 'deployment_drift', 'path': str(installed)})
    for path in SOURCE.rglob('*'):
        if path.is_file() and path.suffix.lower() in EXTENSIONS and '__pycache__' not in path.parts and path.resolve() not in recorded:
            findings.append({'kind': 'unregistered_source', 'path': str(path)})
    return lock, findings


def register_sources():
    lock, findings = release_state()
    added = []
    for finding in findings:
        if finding['kind'] != 'unregistered_source':
            continue
        source = Path(finding['path']).resolve()
        relative = source.relative_to(SOURCE.resolve())
        installed = (source_roots()[relative.parts[0]] / Path(*relative.parts[1:])).resolve()
        lock['files'].append({'source': source.relative_to(PROJECT.resolve()).as_posix(), 'installed': str(installed),
                              'sha256': digest(source), 'installed_sha256': digest(installed) if installed.is_file() else None})
        added.append(str(source))
    save(LOCK, lock)
    return {'registered': added}


def portable_manifest(output=None):
    config = configuration()
    roots = source_roots()
    labels = set(roots) - {'managed-project'}
    manifest = build_manifest(SOURCE, config['version'], labels)
    if LOCK.exists():
        lock, findings = release_state()
        if lock['version'] != config['version'] or any(item['kind'] != 'deployment_drift' for item in findings):
            raise RuntimeError('Resolve source release findings before exporting a manifest')
        if {item['source']: item['sha256'] for item in lock['files']} != {
                item['source']: item['sha256'] for item in manifest['files']}:
            raise RuntimeError('Source manifest does not match the existing local release lock')
    if output is None:
        return manifest
    target = Path(output).resolve()
    target.relative_to((PROJECT / '.local').resolve())
    if not target.parent.is_dir():
        raise FileNotFoundError('Create the local evidence directory explicitly before export')
    payload = json.dumps(manifest, ensure_ascii=False, indent=2)
    with target.open('x', encoding='utf-8') as stream:
        stream.write(payload)
    return {'manifest': str(target), 'files': len(manifest['files']),
            'scope': 'Local candidate only; source names still require security review'}


def initialize_lock(manifest_path, apply=False):
    if LOCK.exists():
        raise FileExistsError('Existing local release lock must not be overwritten')
    config = configuration()
    roots = source_roots()
    manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8-sig'))
    verify_manifest(manifest, SOURCE, config['version'], set(roots) - {'managed-project'})
    records = []
    for item in manifest['files']:
        target = item['target']
        root = roots[target['root']].resolve()
        installed = (root / target['relative_path']).resolve()
        installed.relative_to(root)
        if installed.is_relative_to(SOURCE.resolve()):
            raise ValueError('Deployment target overlaps managed source')
        current = digest(installed) if installed.is_file() else None
        if installed.exists() and not installed.is_file():
            raise ValueError('Deployment target is not a regular file')
        if current not in (None, item['sha256']):
            raise RuntimeError('Existing runtime differs from the verified source; reconcile it first')
        records.append({'source': item['source'], 'installed': str(installed),
                        'sha256': item['sha256'], 'installed_sha256': current})
    result = {'mode': 'preview', 'files': len(records), 'installed_files_written': 0,
              'lock_written': False}
    if apply:
        lock = {'schema_version': 1, 'version': config['version'], 'files': records,
                'initialized_at': datetime.now().astimezone().isoformat()}
        with LOCK.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(lock, ensure_ascii=False, indent=2))
        result.update({'mode': 'initialized', 'lock_written': True})
    return result


def doctor():
    config = configuration()
    lock, findings = release_state()
    for key in ('skills_root', 'legacy_root', 'knowledge_root', 'logs_root'):
        if not expanded(config[key]).is_dir():
            findings.append({'kind': 'path_missing', 'name': key, 'path': str(expanded(config[key]))})
    dependencies = {}
    for line in (PROJECT / 'requirements.lock.txt').read_text(encoding='utf-8').splitlines():
        if not line or line.startswith('#'):
            continue
        name, version = line.split('==', 1)
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual = None
        dependencies[name] = actual
        if version != actual:
            findings.append({'kind': 'dependency_drift', 'package': name, 'expected': version, 'actual': actual})
    return {'status': 'ok' if not findings else 'needs_attention', 'version': lock['version'],
            'python': sys.version, 'managed_files': len(lock['files']), 'dependencies': dependencies, 'findings': findings}


def deploy(apply=False):
    lock, findings = release_state()
    if any(item['kind'] in ('source_missing', 'unregistered_source') for item in findings):
        raise RuntimeError('Source files missing or unregistered; resolve the release map first')
    changes = []
    for item in lock['files']:
        source = (PROJECT / item['source']).resolve()
        source.relative_to(SOURCE.resolve())
        checksum = digest(source)
        installed = Path(item['installed']).resolve()
        current = digest(installed) if installed.is_file() else None
        if current not in (item['installed_sha256'], checksum, None):
            raise RuntimeError('Installed file was edited independently; reconcile it first: ' + str(installed))
        if current != checksum:
            changes.append((item, source, installed, checksum))
    if not apply:
        return {'mode': 'preview', 'changed_files': [str(item[2]) for item in changes], 'deleted_files': 0}
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    backup = PROJECT / '.local' / 'deployments' / timestamp
    backup.mkdir(parents=True, exist_ok=False)
    shutil.copy2(LOCK, backup / 'previous_release.lock.json')
    journal = []
    for index, (item, source, installed, checksum) in enumerate(changes):
        if installed.is_file():
            target = backup / str(index)
            shutil.copy2(installed, target)
            if digest(target) != digest(installed):
                raise RuntimeError('Deployment backup verification failed')
        journal.append({'installed': str(installed), 'backup': str(index) if installed.is_file() else None})
    save(backup / 'journal.json', journal)
    try:
        for item, source, installed, checksum in changes:
            installed.parent.mkdir(parents=True, exist_ok=True)
            temporary = installed.with_suffix(installed.suffix + '.sam-deploy-tmp')
            shutil.copy2(source, temporary)
            if digest(temporary) != checksum:
                raise RuntimeError('Deployment checksum mismatch')
            temporary.replace(installed)
        for item in lock['files']:
            item['sha256'] = digest(PROJECT / item['source'])
            item['installed_sha256'] = item['sha256']
        lock['version'] = configuration()['version']
        save(LOCK, lock)
    except BaseException:
        for item in journal:
            if item['backup'] is not None:
                shutil.copy2(backup / item['backup'], item['installed'])
            else:
                installed = Path(item['installed']).resolve()
                if installed.is_file():
                    installed.unlink()
        raise
    return {'mode': 'applied', 'changed_files': len(changes), 'backup': str(backup), 'deleted_files': 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('import-runtime', 'register-sources', 'doctor', 'release-check', 'deploy', 'source-manifest', 'init-lock'))
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--manifest', type=Path)
    args = parser.parse_args()
    if args.command == 'source-manifest':
        result = portable_manifest(args.output)
    elif args.command == 'init-lock':
        if args.manifest is None:
            parser.error('init-lock requires --manifest')
        result = initialize_lock(args.manifest, args.apply)
    else:
        result = import_runtime() if args.command == 'import-runtime' else register_sources() if args.command == 'register-sources' else doctor() if args.command == 'doctor' else deploy(args.apply) if args.command == 'deploy' else {'findings': release_state()[1]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get('findings'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
