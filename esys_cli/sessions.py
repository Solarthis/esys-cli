"""Shared vehicle locks for native and independent diagnostic backends."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path

from .core import CliError, save_json


class Session:
    def __init__(self, path, job):
        self.path, self.job, self.retained = path, job, False

    def retain(self):
        self.retained = True
        job = json.loads((self.job / 'job.json').read_text(encoding='utf-8'))
        save_json(self.path, {'job': str(self.job), 'state': 'unverified_write', 'native_pid': job.get('pid')})


@contextmanager
def vehicle_session(profile, job, lock_root):
    lock_root = Path(lock_root)
    lock_root.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(profile['expected_vin'].encode('ascii')).hexdigest()[:20]
    path = lock_root / (name + '.lock')
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise CliError('Vehicle session is locked; inspect its job before recovery', 5, {'lock': str(path)}) from exc
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump({'job': str(job), 'pid': os.getpid()}, stream)
    session = Session(path, job)
    try:
        yield session
    finally:
        if not session.retained:
            path.unlink(missing_ok=True)
