import hashlib
import re
from pathlib import Path, PurePosixPath

from baseline import EXTENSIONS, EXCLUDED


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def relative_path(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        raise ValueError('Manifest paths must be canonical relative POSIX paths')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or path.as_posix() != value or value == '.':
        raise ValueError('Unsafe manifest path')
    return path


def source_files(source, labels):
    source = Path(source).resolve()
    if not source.is_dir():
        raise FileNotFoundError('Managed source directory is missing')
    files = {}
    identities = set()
    for path in sorted(source.rglob('*')):
        relative = path.relative_to(source)
        if any(part.lower() in EXCLUDED for part in relative.parts):
            continue
        if path.is_symlink() or path.is_junction():
            raise ValueError('Linked source files or directories are not portable')
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
            continue
        if len(relative.parts) < 2 or relative.parts[0] not in labels:
            raise ValueError('Source file has no registered deployment root')
        path.resolve().relative_to(source)
        name = 'src/' + relative.as_posix()
        relative_path(name)
        if name.casefold() in identities:
            raise ValueError('Case-insensitive source path collision')
        identities.add(name.casefold())
        files[name] = path
    if not files:
        raise ValueError('Refusing an empty source manifest')
    return files


def build_manifest(source, version, labels):
    files = []
    for name, path in source_files(source, labels).items():
        relative = PurePosixPath(name)
        files.append({'source': name,
                      'target': {'root': relative.parts[1],
                                 'relative_path': PurePosixPath(*relative.parts[2:]).as_posix()},
                      'sha256': digest(path)})
    return {'schema_version': 1, 'version': version, 'files': files}


def verify_manifest(manifest, source, version, labels):
    if not isinstance(manifest, dict) or set(manifest) != {'schema_version', 'version', 'files'}:
        raise ValueError('Unexpected source manifest fields')
    if type(manifest['schema_version']) is not int or manifest['schema_version'] != 1:
        raise ValueError('Unsupported source manifest schema')
    if manifest['version'] != version:
        raise ValueError('Source manifest version differs from local configuration')
    if not isinstance(manifest['files'], list) or not manifest['files']:
        raise ValueError('Source manifest must contain files')
    available = source_files(source, labels)
    recorded = set()
    for item in manifest['files']:
        if not isinstance(item, dict) or set(item) != {'source', 'target', 'sha256'}:
            raise ValueError('Unexpected source manifest entry fields')
        relative = relative_path(item['source'])
        if len(relative.parts) < 3 or relative.parts[0] != 'src' or relative.parts[1] not in labels:
            raise ValueError('Manifest entry has no registered source root')
        name = relative.as_posix()
        if name in recorded:
            raise ValueError('Duplicate source manifest entry')
        recorded.add(name)
        expected = {'root': relative.parts[1],
                    'relative_path': PurePosixPath(*relative.parts[2:]).as_posix()}
        if item['target'] != expected:
            raise ValueError('Manifest target differs from registered source mapping')
        checksum = item['sha256']
        if not isinstance(checksum, str) or not re.fullmatch('[0-9a-f]{64}', checksum):
            raise ValueError('Invalid source fingerprint')
        if name not in available or digest(available[name]) != checksum:
            raise ValueError('Manifest source is missing or has changed: ' + name)
    if recorded != set(available):
        raise ValueError('Manifest does not cover every managed source file')
    return manifest
