import importlib.util
from pathlib import Path


_settings_path = Path(__file__).resolve().parents[1] / 'src/NetApp-SAM-Report/sam_settings.py'
_settings_spec = importlib.util.spec_from_file_location('_sam_project_path_contract', _settings_path)
_settings = importlib.util.module_from_spec(_settings_spec)
_settings_spec.loader.exec_module(_settings)


SKILL_NAMES = (
    'NetApp-SAM-Report', '1-netapp-controller-eos-ma',
    '2-netapp-capacity-aggr-vol-fg', '3-jv-case',
    '4-netapp-firmware-shelf-io-sp', '5-netapp-network-ems',
    '6-best-practice-check', 'NetApp Capacity Trend and Case Report',
)
PATH_FIELDS = (
    'skills_root', 'legacy_root', 'knowledge_root', 'logs_root',
    'reports_root', 'trend_output_root',
)


def expand_path(value, project):
    return _settings.expand_path(value, project)


def configuration_path(project):
    return _settings.configuration_path(project)


def load_configuration(project):
    path = configuration_path(project)
    if not path.is_file():
        raise FileNotFoundError('Local SAM configuration is missing; configure it explicitly')
    config = _settings.read_configuration(path)
    if not isinstance(config, dict) or type(config.get('schema_version')) is not int or config['schema_version'] != 1:
        raise ValueError('Unsupported SAM management configuration schema')
    if not isinstance(config.get('version'), str) or not config['version'].strip():
        raise ValueError('Configuration version is required')
    for name in PATH_FIELDS:
        if name not in config:
            raise ValueError('Missing configuration path: ' + name)
        expand_path(config[name], project)
    history = config.get('history_roots')
    if not isinstance(history, list):
        raise ValueError('history_roots must be a list')
    for value in history:
        expand_path(value, project)
    if 'contracts' in config and not isinstance(config['contracts'], dict):
        raise ValueError('contracts must be an object')
    return config


def runtime_roots(project, config=None):
    project = Path(project).resolve()
    config = load_configuration(project) if config is None else config
    skills = expand_path(config['skills_root'], project)
    legacy = expand_path(config['legacy_root'], project)
    roots = {name: skills / name for name in SKILL_NAMES}
    roots['legacy-controller-firmware'] = legacy / 'ASUP Analysis'
    roots['legacy-case'] = legacy / 'Case Analysis'
    source = project / 'src'
    for root in roots.values():
        resolved = root.resolve()
        if resolved.is_relative_to(source) or source.is_relative_to(resolved):
            raise ValueError('Deployment roots must not overlap managed business source')
    return roots
