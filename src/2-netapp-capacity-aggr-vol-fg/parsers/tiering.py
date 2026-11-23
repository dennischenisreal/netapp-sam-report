from collections import defaultdict

from parsers.identity import VolumeIdentityError, volume_identity


def _is_constituent(volume):
    return volume.get('volume_style', '').lower() == 'flexgroup-constituent'


def local_volume_names(volumes):
    names = defaultdict(dict)
    for volume in volumes:
        key = volume_identity(volume)
        name = volume['volume_name']
        ordinal = volume.get('name_ordinal', '')
        if ordinal.isdigit():
            name = f'{name}({ordinal})'
        names[name][key] = volume
    return names


class TieringIndex:
    def __init__(self, volumes):
        self.volumes = {volume_identity(volume): volume for volume in volumes}
        self.children = defaultdict(set)
        self.samples = {}
        self.findings = []
        self.matches = []
        self.duplicates = 0
        self._index_children()

    def _index_children(self):
        physical_children = {}
        parents = [volume for volume in self.volumes.values()
                   if volume.get('volume_style', '').lower() == 'flexgroup']
        for child in self.volumes.values():
            if not _is_constituent(child):
                continue
            key = volume_identity(child)
            if child.get('volume_uuid'):
                physical = key[:2] + (child['volume_uuid'],)
                previous = physical_children.get(physical)
                if previous is not None and previous != key:
                    raise VolumeIdentityError(f'同一集群/SVM中同UUID对应不同子卷名，禁止重复累计或擅自认定重命名: {previous!r}, {key!r}')
                physical_children[physical] = key
            candidates = [parent for parent in parents if volume_identity(parent)[:2] == key[:2]]
            if child.get('fg_uuid'):
                candidates = [parent for parent in candidates
                              if child['fg_uuid'] in (parent.get('fg_uuid'), parent.get('volume_uuid'))]
            elif child.get('fg_msid'):
                candidates = [parent for parent in candidates if parent.get('fg_msid') == child['fg_msid']]
            else:
                candidates = []
            if len(candidates) != 1:
                self.findings.append({'kind': 'unresolved_parent', 'identity': key,
                                      'candidate_count': len(candidates)})
                continue
            parent = candidates[0]
            if child.get('fg_msid') and parent.get('fg_msid') and child['fg_msid'] != parent['fg_msid']:
                raise VolumeIdentityError(f'FlexGroup父子卷UUID与MSID关系冲突: {key!r}')
            self.children[volume_identity(parent)].add(key)

    def add_archive(self, archive, local_volumes, blocks):
        names = local_volume_names(local_volumes)
        for block in blocks:
            if block.get('perf_tb') is None and block.get('bucket_tb') is None and not block.get('bucket_values'):
                continue
            candidates = names.get(block['volume_name'], {})
            if len(candidates) != 1:
                self.findings.append({'kind': 'unresolved_tier_block', 'archive': archive,
                                      'block': block['volume_name'], 'candidate_count': len(candidates)})
                continue
            key, volume = next(iter(candidates.items()))
            if key not in self.volumes:
                raise VolumeIdentityError(f'分层块身份未在本次卷清单登记: {key!r}')
            if len(block.get('bucket_values', {})) > 1:
                self.findings.append({'kind': 'multiple_bucket_lines', 'archive': archive, 'identity': key,
                                      'bucket_names': sorted(block['bucket_values'])})
            sample = dict(block, archive=archive, identity=key, volume_uuid=volume.get('volume_uuid', ''))
            self.matches.append({'archive': archive, 'block': block['volume_name'], 'identity': key,
                                 'volume_uuid': volume.get('volume_uuid', ''),
                                 'name_ordinal': volume.get('name_ordinal', '')})
            previous = self.samples.get(key)
            if previous is not None:
                self.duplicates += 1
            if previous is None or self._score(sample) > self._score(previous):
                self.samples[key] = sample

    @staticmethod
    def _score(sample):
        values = [sample.get(field) for field in ('perf_tb', 'bucket_tb') if sample.get(field) is not None]
        return len(values), sum(values)

    def usage(self, volume):
        key = volume_identity(volume)
        direct = self.samples.get(key)
        children = self.children.get(key, set())
        result = {'identity': key, 'expected_children': len(children), 'fields': {}}
        for field in ('perf_tb', 'bucket_tb'):
            if direct is not None and direct.get(field) is not None:
                result[field] = direct[field]
                result['fields'][field] = {'method': 'direct', 'sources': [direct['archive']],
                                          'block': direct['volume_name']}
            elif children and all(child in self.samples and self.samples[child].get(field) is not None for child in children):
                result[field] = sum(self.samples[child][field] for child in children)
                result['fields'][field] = {'method': 'unique_constituents', 'children': sorted(children),
                                          'sources': [self.samples[child]['archive'] for child in sorted(children)]}
            else:
                result[field] = None
                result['fields'][field] = {'method': 'missing_data',
                                          'missing_children': sorted(child for child in children
                                                                     if child not in self.samples or self.samples[child].get(field) is None)}
        return result

    def audit(self, reported_volumes):
        usages = []
        missing = []
        for volume in reported_volumes:
            if not volume.get('tiering_policy') or volume['tiering_policy'].lower() == 'none':
                continue
            usage = self.usage(volume)
            usages.append(usage)
            fields = [field for field in ('perf_tb', 'bucket_tb') if usage[field] is None]
            if fields:
                missing.append({'identity': volume_identity(volume), 'fields': fields})
        return {'schema_version': 1, 'status': 'needs_review' if missing or self.findings else 'ok',
                'identity': ['cluster', 'svm', 'volume'], 'findings': self.findings,
                'matched_blocks': self.matches, 'duplicate_samples_not_summed': self.duplicates,
                'child_counts': [{'identity': key, 'count': len(children)} for key, children in sorted(self.children.items())],
                'missing_tiering_fields': missing, 'volume_usage': usages}
