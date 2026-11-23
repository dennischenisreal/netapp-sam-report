"""Local candidate installer. Never installs into user Skill or legacy folders."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def selected_environment():
    temporary = ROOT / '.local/test-temp'
    temporary.mkdir(parents=True, exist_ok=True)
    environment = {key: value for key, value in os.environ.items() if not key.startswith('SAM_')}
    environment.update(SAM_PROJECT_ROOT=str(ROOT), SAM_PROJECT_CONFIG=str(ROOT / '.local/config/project.json'),
                       SAM_SHARED_SKILL_DIR=str(ROOT / '.local/runtime/skills/NetApp-SAM-Report'),
                       PYTHONIOENCODING='utf-8', PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1',
                       TEMP=str(temporary), TMP=str(temporary))
    return environment


def install():
    manifest = json.loads((ROOT / 'source-manifest.json').read_text(encoding='utf-8'))
    settings = json.loads((ROOT / 'config/project.example.json').read_text(encoding='utf-8'))
    sys.path.insert(0, str(ROOT / 'tools'))
    from project_settings import SKILL_NAMES
    from source_manifest import verify_manifest
    verify_manifest(manifest, ROOT / 'src', settings['version'],
                    set(SKILL_NAMES) | {'legacy-controller-firmware', 'legacy-case'})
    destinations = []
    for entry in manifest['files']:
        source = (ROOT / entry['source']).resolve()
        source.relative_to(ROOT / 'src')
        if digest(source) != entry['sha256']:
            raise ValueError('Candidate source fingerprint mismatch')
        label = entry['target']['root']
        prefix = {'legacy-controller-firmware': '.local/runtime/legacy/ASUP Analysis',
                  'legacy-case': '.local/runtime/legacy/Case Analysis'}.get(label, '.local/runtime/skills/' + label)
        target = (ROOT / prefix / entry['target']['relative_path']).resolve()
        target.relative_to(ROOT / '.local/runtime')
        if target.exists() and (not target.is_file() or digest(target) != entry['sha256']):
            raise FileExistsError('Existing candidate runtime differs; use a fresh isolated directory')
        destinations.append((source, target, entry['sha256']))
    config = ROOT / '.local/config'
    for name in ('project.json', 'customer-profiles.local.json'):
        if (config / name).exists():
            raise FileExistsError('Existing local candidate configuration must not be overwritten')
    if shutil.which('7z'):
        settings['seven_zip_path'] = shutil.which('7z')
    for key in ('skills_root', 'legacy_root', 'knowledge_root', 'logs_root', 'reports_root', 'trend_output_root'):
        target = (ROOT / settings[key].replace('{project}/', '')).resolve()
        target.relative_to(ROOT / '.local')
        target.mkdir(parents=True, exist_ok=True)
    for source, target, checksum in destinations:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if digest(target) != checksum:
            raise RuntimeError('Installed candidate checksum mismatch')
    config.mkdir(parents=True, exist_ok=True)
    (config / 'project.json').write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
    shutil.copy2(ROOT / 'config/customer-profiles.example.json', config / 'customer-profiles.local.json')
    result = subprocess.run([sys.executable, '-B', str(ROOT / 'sam.py'), 'init-lock',
                             str(ROOT / 'source-manifest.json'), '--apply'],
                            cwd=ROOT, env=selected_environment(), capture_output=True, encoding='utf-8')
    if result.returncode:
        raise RuntimeError('Candidate lock initialization failed: ' + result.stderr)
    return {'status': 'installed_local_candidate_only', 'files': len(destinations), 'production_written': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('install', 'doctor', 'test', 'release-check'))
    arguments = parser.parse_args()
    if arguments.command == 'install':
        print(json.dumps(install(), indent=2))
        return 0
    return subprocess.call([sys.executable, '-B', str(ROOT / 'sam.py'), arguments.command],
                           cwd=ROOT, env=selected_environment())


if __name__ == '__main__':
    raise SystemExit(main())
