import json
import os
from pathlib import Path
import sys


_root = Path(os.environ['CANDIDATE_ALLOWED_ROOT']).resolve()
_python = Path(sys.base_prefix).resolve()
_stream = open(os.environ['CANDIDATE_GUARD_LOG'], 'a', encoding='utf-8')
_stream.write(json.dumps({'event': 'guard_started', 'pid': os.getpid()}) + '\n')
_stream.flush()


def _guard(event, arguments):
    if event not in {'open', 'os.listdir', 'os.scandir'} or not arguments:
        return
    if not isinstance(arguments[0], (str, bytes, os.PathLike)):
        return
    path = Path(os.fsdecode(arguments[0])).resolve()
    allowed = path.is_relative_to(_root) or path.is_relative_to(_python)
    _stream.write(json.dumps({'event': event, 'pid': os.getpid(), 'allowed': allowed, 'path': str(path)}) + '\n')
    _stream.flush()
    if not allowed:
        raise PermissionError('Candidate isolation rejected access outside candidate and Python runtime')


sys.addaudithook(_guard)
