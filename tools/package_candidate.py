"""Repackage an exact reviewed source inventory without Git or deployment."""
import argparse
import json
import os
from pathlib import Path
import zipfile

from acceptance_support import PROJECT, checked_path, digest, save


def package(destination):
    destination = destination.resolve()
    destination.relative_to((PROJECT / '.local').resolve())
    if destination.exists():
        raise FileExistsError('Refusing to overwrite an existing package directory')
    manifest = json.loads((PROJECT / 'package-manifest.json').read_text(encoding='utf-8'))
    entries = manifest['files']
    if not entries or len(entries) != len({entry['path'].casefold() for entry in entries}):
        raise ValueError('Empty or duplicate package inventory')
    names = {entry['path'] for entry in entries} | {'package-manifest.json'}
    for name in names:
        relative = checked_path(name)
        current = PROJECT
        for part in relative.parts:
            current = current / part
            if current.is_symlink() or current.is_junction():
                raise ValueError('Linked source is not a reviewed file')
    for entry in entries:
        source = PROJECT / entry['path']
        if source.stat().st_size != entry['bytes'] or digest(source) != entry['sha256']:
            raise ValueError('Source changed; refresh and review inventory before packaging')
    observed = set()
    for directory, folders, files in os.walk(PROJECT, followlinks=False):
        folders[:] = [name for name in folders if name not in {'.local', '.git', '__pycache__', 'node_modules', '.venv'}]
        for name in folders + files:
            path = Path(directory) / name
            if path.is_symlink() or path.is_junction():
                raise ValueError('Linked source tree is not a reviewed package')
        observed.update((Path(directory) / name).relative_to(PROJECT).as_posix() for name in files
                        if (Path(directory) / name).relative_to(PROJECT).as_posix() != 'release.lock.json')
    if observed != names:
        raise ValueError('Unreviewed files outside the exact source inventory')
    destination.mkdir(parents=True)
    archive = destination / 'netapp-sam-report-shared-candidate.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as stream:
        for name in sorted(names):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            stream.writestr(info, (PROJECT / name).read_bytes())
    result = {'status': 'local_repackage_only', 'archive': str(archive), 'archive_sha256': digest(archive),
              'archive_files': len(names), 'production_written': False, 'git_written': False}
    save(destination / 'build.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps(package(parser.parse_args().output), indent=2))
