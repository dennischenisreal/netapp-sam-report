import copy
import hashlib
import json
import os
from pathlib import Path

from sam_settings import CONFIG_PATH, configuration, configuration_path


_LOADED = {}
_MESSAGE_KEYS = {'title_check4', 'title_check9', 'title_check14',
                 'log_check9', 'log_check14_skip', 'log_check14_start'}


def profile_path():
    override = os.environ.get('SAM_CUSTOMER_PROFILES')
    if override is not None:
        if not override.strip():
            raise ValueError('SAM_CUSTOMER_PROFILES must not be empty')
        path = Path(override)
        if not path.is_absolute():
            raise ValueError('SAM_CUSTOMER_PROFILES must be an absolute local path')
        return path.resolve()
    location = Path(__file__).resolve()
    if 'SAM_PROJECT_CONFIG' in os.environ or 'SAM_PROJECT_ROOT' in os.environ:
        configuration()
        return configuration_path().parent / 'customer-profiles.local.json'
    if location.parent.parent.name.lower() == 'src':
        return location.parents[2] / 'config' / 'customer-profiles.local.json'
    return CONFIG_PATH.parent / 'customer-profiles.local.json'


def _conditions(value):
    if not isinstance(value, list) or not value:
        raise ValueError('Customer matching conditions must not be empty')
    for item in value:
        if not isinstance(item, dict) or set(item) != {'token', 'transform'}:
            raise ValueError('Invalid customer matching condition')
        if not isinstance(item['token'], str) or not item['token']:
            raise ValueError('Customer matching tokens must not be empty')
        if item['transform'] not in ('identity', 'lower', 'upper'):
            raise ValueError('Invalid customer matching normalization')


def _validate(value):
    if not isinstance(value, dict) or set(value) != {'schema_version', 'naming', 'best_practice'}:
        raise ValueError('Invalid customer profile structure')
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ValueError('Unsupported customer profile schema')
    naming = value['naming']
    if not isinstance(naming, dict) or set(naming) != {'sam', 'best_practice'}:
        raise ValueError('Both existing customer naming modes must be configured')
    for mode, fallback in (('sam', 'sam_parent'), ('best_practice', 'basename_strip')):
        settings = naming[mode]
        if not isinstance(settings, dict) or set(settings) != {'basename_aliases', 'rules', 'fallback'}:
            raise ValueError('Invalid customer naming structure')
        if settings['fallback'] != fallback:
            raise ValueError('Existing customer fallback behavior must be preserved')
        aliases = settings['basename_aliases']
        if not isinstance(aliases, dict) or any(not isinstance(key, str) or not key or
                not isinstance(name, str) or not name for key, name in aliases.items()):
            raise ValueError('Invalid customer basename aliases')
        if not isinstance(settings['rules'], list):
            raise ValueError('Customer naming rules must be ordered')
        for rule in settings['rules']:
            if not isinstance(rule, dict) or set(rule) != {'any', 'name'} or not isinstance(rule['name'], str) or not rule['name']:
                raise ValueError('Invalid customer naming rule')
            _conditions(rule['any'])
    practice = value['best_practice']
    if not isinstance(practice, dict) or set(practice) != {'special_customer_match', 'check14_clusters', 'messages'}:
        raise ValueError('Invalid best practice customer scope')
    _conditions(practice['special_customer_match'])
    clusters = practice['check14_clusters']
    if not isinstance(clusters, list) or not clusters or any(not isinstance(item, str) or
            not item or item != item.strip() or item != item.lower() for item in clusters):
        raise ValueError('Check14 cluster scope must be explicit lowercase identities')
    if len(set(clusters)) != len(clusters):
        raise ValueError('Duplicate Check14 cluster identity')
    messages = practice['messages']
    if not isinstance(messages, dict) or set(messages) != _MESSAGE_KEYS or any(
            not isinstance(text, str) or not text.strip() for text in messages.values()):
        raise ValueError('Existing customer-facing check text must be configured')
    return value


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate customer profile key')
        value[key] = item
    return value


def load_profiles():
    path = profile_path()
    if not path.is_file():
        raise FileNotFoundError('Local customer rules are missing; refusing to infer check applicability')
    payload = path.read_bytes()
    checksum = hashlib.sha256(payload).hexdigest()
    previous = _LOADED.get(path)
    if previous is not None and previous[0] != checksum:
        raise RuntimeError('Customer rules changed during this process; start a new run')
    if previous is None:
        value = _validate(json.loads(payload.decode('utf-8-sig'), object_pairs_hook=_unique_object))
        _LOADED[path] = (checksum, value)
    return copy.deepcopy(_LOADED[path][1])


def _matches(text, conditions):
    for condition in conditions:
        transform = condition['transform']
        transformed = text if transform == 'identity' else getattr(text, transform)()
        if condition['token'] in transformed:
            return True
    return False


def customer_name(directory, mode):
    original = str(directory)
    normalized = original.replace('\\', '/')
    settings = load_profiles()['naming'][mode]
    basename = os.path.basename(normalized)
    if basename in settings['basename_aliases']:
        return settings['basename_aliases'][basename]
    for rule in settings['rules']:
        if _matches(normalized, rule['any']):
            return rule['name']
    if settings['fallback'] == 'sam_parent':
        parts = normalized.split('/')
        return parts[-2] if len(parts) >= 2 else basename
    return os.path.basename(original.rstrip(os.sep))


def check_enabled(key, customer, profiles=None):
    profiles = load_profiles() if profiles is None else profiles
    special = _matches(customer, profiles['best_practice']['special_customer_match'])
    if key in ('check4', 'check14'):
        return special
    if key == 'check9':
        return not special
    return True


def target_clusters(profiles=None):
    profiles = load_profiles() if profiles is None else profiles
    return set(profiles['best_practice']['check14_clusters'])


def check_message(key):
    return load_profiles()['best_practice']['messages'][key]
