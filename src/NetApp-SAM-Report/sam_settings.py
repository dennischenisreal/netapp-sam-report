import json
import os
from pathlib import Path


CONFIG_PATH = Path(__file__).resolve().parents[2] / 'config/project.json'


def expand_path(value, project):
    project = Path(project).resolve()
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Configuration paths must be non-empty strings')
    try:
        rendered = value.format(home=Path.home().as_posix(), project=project.as_posix(),
                                workspace=project.parent.as_posix())
    except (KeyError, ValueError, IndexError, AttributeError) as error:
        raise ValueError('Unsupported configuration path placeholder') from error
    path = Path(rendered)
    if not path.is_absolute():
        if path.drive or path.root:
            raise ValueError('Ambiguous drive-relative configuration path')
        path = project / path
    return path.resolve()


def project_root():
    override = os.environ.get('SAM_PROJECT_ROOT')
    if override is not None:
        if not override.strip() or not Path(override).is_absolute():
            raise ValueError('SAM_PROJECT_ROOT must be a non-empty absolute path')
        return Path(override).resolve()
    return Path(CONFIG_PATH).resolve().parent.parent


def configuration_path(project=None):
    base = Path(project).resolve() if project is not None else project_root()
    override = os.environ.get('SAM_PROJECT_CONFIG')
    if override is not None:
        if not override.strip():
            raise ValueError('SAM_PROJECT_CONFIG must not be empty')
        return expand_path(override, base)
    if project is not None or 'SAM_PROJECT_ROOT' in os.environ:
        return base / 'config' / 'project.json'
    return Path(CONFIG_PATH).resolve()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate project configuration key')
        result[key] = value
    return result


def read_configuration(path):
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=unique_object)
    if not isinstance(value, dict) or type(value.get('schema_version')) is not int or value['schema_version'] != 1:
        raise ValueError('Unsupported SAM project configuration schema')
    return value


def configuration():
    path = configuration_path()
    if not path.is_file():
        if 'SAM_PROJECT_CONFIG' in os.environ or 'SAM_PROJECT_ROOT' in os.environ:
            raise FileNotFoundError('Explicit SAM project configuration is missing')
        return {}
    return read_configuration(path)


def path_setting(name, fallback):
    settings = configuration()
    return expand_path(settings[name], project_root()) if name in settings else Path(fallback)


def history_roots(fallback):
    settings = configuration()
    if 'history_roots' not in settings:
        return fallback
    values = settings['history_roots']
    if not isinstance(values, list):
        raise ValueError('history_roots must be a list')
    return [expand_path(value, project_root()) for value in values]
