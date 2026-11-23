"""Portable, local-only support for reviewed synthetic acceptance archives."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile


PROJECT = Path(__file__).resolve().parents[1]
TEMPLATES = PROJECT / 'packaging/templates'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def checked_path(name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        raise ValueError('Unsafe archive path')
    relative = PurePosixPath(name)
    if relative.is_absolute() or '..' in relative.parts or relative.as_posix() != name or name == '.':
        raise ValueError('Unsafe archive path')
    if any(part.casefold() in {'.local', '.git'} for part in relative.parts):
        raise ValueError('Private runtime or Git metadata in archive')
    if relative.name == 'release.lock.json' or relative.as_posix() == 'config/project.json' or relative.name.endswith('.local.json'):
        raise ValueError('Private runtime configuration in archive')
    return relative


def extract(archive, root):
    root.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive) as stream:
        names = stream.namelist()
        if len(names) != len(set(name.casefold() for name in names)):
            raise ValueError('Duplicate or case-colliding archive paths')
        for name in names:
            checked_path(name)
            info = stream.getinfo(name)
            if info.is_dir() or info.external_attr >> 16 & 0o170000 == 0o120000:
                raise ValueError('Archive contains a directory entry or symbolic link')
            if info.file_size > 2_000_000:
                raise ValueError('Archive member exceeds reviewed size limit')
        if sum(info.file_size for info in stream.infolist()) > 20_000_000:
            raise ValueError('Archive exceeds reviewed total size limit')
        manifest = json.loads(stream.read('package-manifest.json'))
        entries = manifest['files']
        if not entries or len(entries) != len({entry['path'].casefold() for entry in entries}):
            raise ValueError('Empty or duplicate package manifest')
        approved = {entry['path']: entry for entry in entries}
        if set(names) != set(approved) | {'package-manifest.json'}:
            raise ValueError('Archive does not match exact package inventory')
        for name, entry in approved.items():
            checked_path(name)
            payload = stream.read(name)
            if len(payload) != entry['bytes'] or hashlib.sha256(payload).hexdigest() != entry['sha256']:
                raise ValueError('Archive fingerprint differs from approved candidate')
        stream.extractall(root)
    return len(approved) + 1
