import os
from pathlib import Path

from sam_settings import configuration, configuration_path, path_setting, project_root


def require_configuration():
    if not configuration_path().is_file():
        raise FileNotFoundError('Explicit candidate local configuration is required; production fallback is disabled')
    configuration()


require_configuration()


def package_path(key):
    defaults = {
        'logs_root': project_root() / '.local/data/asup',
        'legacy_root': project_root() / '.local/runtime/legacy',
        'trend_output_root': project_root() / '.local/trends',
        'seven_zip_path': Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / '7-Zip/7z.exe',
    }
    return path_setting(key, defaults[key])


def legacy_settings():
    value = configuration().get('legacy_compat')
    if not isinstance(value, dict) or set(value) != {'default_sites', 'firmware_naming'}:
        raise ValueError('Explicit legacy compatibility configuration is required')
    if not isinstance(value['default_sites'], list) or any(not isinstance(site, str) or not site for site in value['default_sites']):
        raise ValueError('Invalid default site list')
    if not isinstance(value['firmware_naming'], list):
        raise ValueError('Invalid firmware naming rules')
    for rule in value['firmware_naming']:
        if not isinstance(rule, dict) or set(rule) != {'any', 'name'} or not isinstance(rule['name'], str):
            raise ValueError('Invalid firmware naming rule')
        if not isinstance(rule['any'], list) or not rule['any']:
            raise ValueError('Empty firmware naming conditions')
        for condition in rule['any']:
            if not isinstance(condition, dict) or set(condition) != {'field', 'operation', 'value'}:
                raise ValueError('Invalid firmware condition structure')
            if condition['field'] not in {'path', 'lower_path', 'basename'} or condition['operation'] not in {'contains', 'equals'}:
                raise ValueError('Unsupported firmware condition')
            if not isinstance(condition['value'], str) or not condition['value']:
                raise ValueError('Empty firmware condition value')
    return value


def legacy_sites():
    return list(legacy_settings()['default_sites'])


def firmware_name(directory):
    fields = {'path': directory, 'lower_path': directory.lower(), 'basename': os.path.basename(directory)}
    for rule in legacy_settings()['firmware_naming']:
        for condition in rule['any']:
            actual = fields[condition['field']]
            matched = condition['value'] in actual if condition['operation'] == 'contains' else condition['value'] == actual
            if matched:
                return rule['name']
    return os.path.basename(directory)
