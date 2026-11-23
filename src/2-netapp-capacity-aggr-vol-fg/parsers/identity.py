class VolumeIdentityError(ValueError):
    pass


def volume_identity(volume):
    key = (volume.get('cluster_name', ''), volume.get('site_name', ''), volume.get('volume_name', ''))
    if not all(key):
        raise VolumeIdentityError(f'卷身份不完整，禁止按卷名猜测或合并: {key!r}')
    return key


class VolumeIndex:
    def __init__(self):
        self._records = {}
        self._uuids = {}

    def add(self, volume):
        key = volume_identity(volume)
        uuid = volume.get('volume_uuid', '')
        previous_uuid = self._uuids.get(key)
        if uuid and previous_uuid and uuid != previous_uuid:
            raise VolumeIdentityError(f'相同集群/SVM/卷名存在不同UUID，不能静默选择容量较大的记录: {key!r}, {previous_uuid}, {uuid}')
        if uuid:
            self._uuids[key] = uuid
        previous = self._records.get(key)
        current_size = volume.get('size_tb')
        previous_size = previous.get('size_tb') if previous is not None else None
        if previous is None or (current_size is not None and (previous_size is None or current_size > previous_size)):
            self._records[key] = volume

    def values(self):
        return self._records.values()

    def __len__(self):
        return len(self._records)
