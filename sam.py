"""维护入口；原Skill入口仍可独立使用。"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT / 'tools'))
from manage import configuration, doctor, expanded, deploy, release_state, portable_manifest, initialize_lock
from project_settings import configuration_path


def runtime_environment():
    return {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1',
            'SAM_PROJECT_CONFIG': str(configuration_path(PROJECT).resolve()),
            'SAM_PROJECT_ROOT': str(PROJECT.resolve())}


def main():
    parser = argparse.ArgumentParser(description='SAM Report managed entrypoint')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('doctor')
    commands.add_parser('release-check')
    commands.add_parser('test')
    deployment = commands.add_parser('deploy')
    deployment.add_argument('--apply', action='store_true')
    manifest = commands.add_parser('source-manifest')
    manifest.add_argument('--output', type=Path)
    initialization = commands.add_parser('init-lock')
    initialization.add_argument('manifest', type=Path)
    initialization.add_argument('--apply', action='store_true')
    report = commands.add_parser('report')
    report.add_argument('input_dir', type=Path)
    report.add_argument('--output', type=Path)
    report.add_argument('--interactive', action='store_true')
    trend = commands.add_parser('trend')
    trend.add_argument('source_dir', type=Path)
    trend.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command in ('source-manifest', 'init-lock'):
        result = portable_manifest(args.output) if args.command == 'source-manifest' else initialize_lock(args.manifest, args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command in ('doctor', 'release-check', 'deploy'):
        result = doctor() if args.command == 'doctor' else deploy(args.apply) if args.command == 'deploy' else {'findings': release_state()[1]}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get('findings') else 0
    if args.command == 'test':
        state = doctor()
        if state['findings']:
            print(json.dumps(state, ensure_ascii=False, indent=2))
            return 1
        return subprocess.call([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', str(PROJECT / 'tests'), '-v'], cwd=PROJECT)
    if 'SAM_PROJECT_CONFIG' in os.environ or 'SAM_PROJECT_ROOT' in os.environ:
        print('SAM_PROJECT_CONFIG currently applies only to management commands; production adapters are not migrated.')
        return 2
    state = doctor()
    if state['findings']:
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 1
    config = configuration()
    skills = expanded(config['skills_root'])
    env = runtime_environment()
    if args.command == 'report':
        code = "import runpy,sys;sys.path.insert(0,sys.argv[1]);module=runpy.run_path(sys.argv[1]+'/run.py');module['process_directory'](sys.argv[2],output_path=sys.argv[3],use_defaults=sys.argv[4]=='default')"
        output = args.output or expanded(config['reports_root'])
        command = [sys.executable, '-B', '-c', code, str(skills / 'NetApp-SAM-Report'), str(args.input_dir.resolve()), str(output.resolve()), 'interactive' if args.interactive else 'default']
    else:
        code = "import sys;sys.path.insert(0,sys.argv[1]);from generate_capacity_report import generate_capacity_report;generate_capacity_report(sys.argv[2],sys.argv[3])"
        command = [sys.executable, '-B', '-c', code, str(skills / 'NetApp Capacity Trend and Case Report'), str(args.source_dir.resolve()), str(args.output.resolve())]
    return subprocess.call(command, env=env, cwd=PROJECT.parent)


if __name__ == '__main__':
    raise SystemExit(main())
