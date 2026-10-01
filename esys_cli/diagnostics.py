"""Dataset-independent reads and evidence-backed UDS fault clearing."""
from contextlib import contextmanager
import ipaddress
import json
import math
import os
from pathlib import Path
import time
import uuid

from .core import CliError, plan_digest, vin
from .diagnostic_transport import Transport
from .sessions import vehicle_session
from .settings import state_root
from . import uds


def validate_profile(value):
    keys = {'schema_version', 'kind', 'transport', 'host', 'port', 'source', 'identity_ecu',
            'ecus', 'expected_vin', 'protocol_version', 'activation_type'}
    if not isinstance(value, dict) or set(value) != keys or type(value['schema_version']) is not int or value['schema_version'] != 1 or value['kind'] != 'uds-profile':
        raise CliError('Invalid independent diagnostic profile schema')
    value = dict(value)
    if value['transport'] not in ('hsfz', 'doip'):
        raise CliError('Transport must be hsfz or doip')
    try:
        host = ipaddress.IPv4Address(value['host'])
    except (ValueError, TypeError) as exc:
        raise CliError('Diagnostic host must be a literal IPv4 address') from exc
    if not isinstance(value['host'], str) or host.is_multicast or host.is_unspecified or int(host) == 0xFFFFFFFF:
        raise CliError('Diagnostic host must be a unicast IPv4 address')
    value['host'] = str(host)
    if type(value['port']) is not int or not 1 <= value['port'] <= 65535:
        raise CliError('Invalid diagnostic TCP port')
    if type(value['protocol_version']) is not int or value['protocol_version'] not in (2, 3) or type(value['activation_type']) is not int or not 0 <= value['activation_type'] <= 255:
        raise CliError('Invalid DoIP protocol version or activation type')
    hsfz = value['transport'] == 'hsfz'
    def physical(address):
        return type(address) is int and (1 <= address <= 0xDE if hsfz else 1 <= address <= 0x0DFF or 0x1000 <= address <= 0x7FFF)
    ecus = value['ecus']
    if not isinstance(ecus, list) or not 1 <= len(ecus) <= 32 or any(not physical(ecu) for ecu in ecus) or len(set(ecus)) != len(ecus):
        raise CliError('Specify 1 to 32 unique physical ECUs; functional/broadcast addresses are unsupported')
    if not physical(value['identity_ecu']):
        raise CliError('Identity ECU must be a physical address')
    source = value['source']
    if type(source) is not int or not (0xF0 <= source <= 0xFE if hsfz else 0x0E00 <= source <= 0x0FFF):
        raise CliError('Tester address is outside the supported tester range')
    value['expected_vin'] = vin(value['expected_vin'])
    value['ecus'] = sorted(ecus)
    return value


def load_profile(path):
    return validate_profile(json.loads(Path(path).read_text(encoding='utf-8-sig')))


def label(profile, ecu):
    return f'0x{ecu:02X}' if profile['transport'] == 'hsfz' else f'0x{ecu:04X}'


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise CliError('Output already exists; choose a new evidence filename') from exc


class Job:
    def __init__(self, root, command, profile):
        self.path = Path(root).resolve() / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:12])
        self.path.mkdir(parents=True)
        self.data = {'schema_version': 1, 'command': command, 'backend': 'independent-uds',
                     'profile': profile, 'state': 'prepared', 'write_attempted': False}
        self.observed_bytes = 0
        self.update()

    def update(self, **values):
        self.data.update(values)
        temporary = self.path / ('job-' + uuid.uuid4().hex + '.tmp')
        write_new(temporary, self.data)
        temporary.replace(self.path / 'job.json')

    def observe(self, direction, frame):
        self.observed_bytes += len(frame)
        if self.observed_bytes > 64 * 1024 * 1024:
            raise CliError('Diagnostic evidence exceeded the per-job limit', 3)
        with (self.path / 'transport.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'time': time.time(), 'direction': direction, 'frame_hex': frame.hex()}) + '\n')
            stream.flush()
            os.fsync(stream.fileno())


class Diagnostics:
    def __init__(self, jobs_root, timeout=5, *, lock_root=None):
        if not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise CliError('Diagnostic timeout must be positive and at most 120 seconds')
        self.jobs_root, self.timeout = Path(jobs_root), timeout
        self.lock_root = Path(lock_root) if lock_root else state_root() / 'session-locks'

    @contextmanager
    def connection(self, profile, command):
        job = Job(self.jobs_root, command, profile)
        with vehicle_session(profile, job.path, self.lock_root) as session:
            try:
                with Transport(profile, self.timeout, job.observe) as client:
                    yield job, client
            except BaseException as exc:
                if job.data['write_attempted']:
                    session.retained = True
                    try:
                        job.update(state='write_unverified', error=str(exc))
                        session.retain()
                    except OSError:
                        pass
                    raise CliError(f'Fault-clear outcome is unverified: {exc}', 4, {'job': str(job.path)}) from exc
                job.update(state='failed', error=str(exc))
                if isinstance(exc, CliError):
                    exc.details.setdefault('job', str(job.path))
                raise

    def identity(self, client, profile):
        observed = uds.read_vin(client, profile['identity_ecu'])
        if observed != profile['expected_vin']:
            raise CliError('Fresh diagnostic VIN does not match the profile', 3)
        return observed

    def snapshot(self, client, profile):
        return {label(profile, ecu): uds.read_faults(client, ecu) for ecu in profile['ecus']}

    def faults(self, profile):
        profile = validate_profile(profile)
        with self.connection(profile, 'diag-faults') as (job, client):
            observed = self.identity(client, profile)
            result = {'job': str(job.path), 'vin': observed, 'identity_source': 'live_uds_f190',
                      'faults': self.snapshot(client, profile), 'write_attempted': False}
            job.update(state='verified', result=result)
            return result

    def read_vin(self, profile):
        profile = validate_profile(profile)
        with self.connection(profile, 'diag-vin') as (job, client):
            result = {'job': str(job.path), 'vin': self.identity(client, profile),
                      'identity_source': 'live_uds_f190', 'write_attempted': False}
            job.update(state='verified', result=result)
            return result

    def read_did(self, profile, ecu, did):
        profile = validate_profile(profile)
        if ecu not in profile['ecus']:
            raise CliError('Requested ECU is not in the explicit diagnostic profile scope')
        if type(did) is not int or not 0 <= did <= 65535:
            raise CliError('DID must be a 16-bit integer')
        with self.connection(profile, 'diag-read-did') as (job, client):
            self.identity(client, profile)
            payload = uds.read_did(client, ecu, did)
            result = {'job': str(job.path), 'ecu': label(profile, ecu), 'did': f'0x{did:04X}',
                      'hex': payload.hex().upper(), 'write_attempted': False}
            job.update(state='verified', result=result)
            return result

    def clear_plan(self, profile, output):
        profile = validate_profile(profile)
        if Path(output).exists():
            raise CliError('Output already exists; choose a new evidence filename')
        with self.connection(profile, 'diag-clear-plan') as (job, client):
            self.identity(client, profile)
            before = self.snapshot(client, profile)
            now = time.time()
            plan = {'schema_version': 1, 'kind': 'uds-clear-plan', 'profile': profile,
                    'created_at': now, 'expires_at': now + 300, 'before': before, 'group': 'FFFFFF'}
            plan['plan_hash'] = plan_digest(plan)
            write_new(output, plan)
            write_new(job.path / 'plan.json', plan)
            result = {'job': str(job.path), 'plan': str(Path(output).resolve()), 'plan_hash': plan['plan_hash'],
                      'before': before, 'expires_at': plan['expires_at'], 'write_attempted': False}
            job.update(state='verified', result=result)
            return result

    def clear(self, plan_path, accepted_hash, stationary):
        plan = json.loads(Path(plan_path).read_text(encoding='utf-8-sig'))
        keys = {'schema_version', 'kind', 'profile', 'created_at', 'expires_at', 'before', 'group', 'plan_hash'}
        if not isinstance(plan, dict) or set(plan) != keys or plan['schema_version'] != 1 or plan['kind'] != 'uds-clear-plan' or plan['group'] != 'FFFFFF':
            raise CliError('Invalid fault-clear plan schema')
        if plan_digest(plan) != plan['plan_hash'] or accepted_hash != plan['plan_hash'] or stationary is not True:
            raise CliError('Fault clearing requires an intact plan, its exact acceptance hash and --vehicle-stationary')
        now = time.time()
        if any(type(plan[key]) not in (int, float) or not math.isfinite(plan[key]) for key in ('created_at', 'expires_at')) or not plan['created_at'] <= now < plan['expires_at'] or plan['expires_at'] - plan['created_at'] != 300:
            raise CliError('Fault-clear plan expired or has invalid timing; capture a fresh plan')
        profile = validate_profile(plan['profile'])
        if not isinstance(plan['before'], dict) or set(plan['before']) != {label(profile, ecu) for ecu in profile['ecus']}:
            raise CliError('Fault-clear plan has incomplete ECU scope')
        with self.connection(profile, 'diag-clear') as (job, client):
            self.identity(client, profile)
            before = self.snapshot(client, profile)
            if before != plan['before']:
                raise CliError('Current fault state differs from the accepted plan; capture a fresh plan')
            if time.time() >= plan['expires_at']:
                raise CliError('Fault-clear plan expired during preflight')
            write_new(job.path / 'accepted-plan.json', plan)
            write_new(job.path / 'before.json', before)
            after = {}
            for ecu in profile['ecus']:
                job.update(state='clearing', write_attempted=True, current_ecu=label(profile, ecu))
                uds.clear_faults(client, ecu)
                after[label(profile, ecu)] = uds.read_faults(client, ecu)
                job.update(after=after)
            self.identity(client, profile)
            # Bits 4/6 indicate tests not completed, not evidence of a failed test.
            remaining = sum(bool(dtc['status'] & 0xAF) for snapshot in after.values() for dtc in snapshot['dtcs'])
            result = {'job': str(job.path), 'plan_hash': plan['plan_hash'], 'write_attempted': True,
                      'clear_acknowledged': True, 'all_faults_absent': remaining == 0,
                      'remaining_fault_count': remaining, 'before': before, 'after': after,
                      'verification': 'uds_positive_clear_and_immediate_readback', 'physical_repair_verified': False}
            job.update(state='verified', result=result)
            return result
