from contextlib import contextmanager
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET


class CliError(Exception):
    def __init__(self, message, code=2, details=None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def tag(node):
    return node.tag.rsplit('}', 1)[-1]


def xml(path, expected):
    raw = Path(path).read_bytes()
    # Remove NULs so UTF-16/32 declarations cannot bypass the DTD check.
    if re.search(br'<!\s*(DOCTYPE|ENTITY)\b', raw.replace(b'\x00', b''), re.I):
        raise CliError('DTD and entity declarations are not accepted')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise CliError(f'Invalid XML: {exc}') from exc
    accepted = (expected,) if isinstance(expected, str) else expected
    if tag(root).lower() not in [name.lower() for name in accepted]:
        raise CliError(f'Expected {expected} XML, found {tag(root)}')
    return root


def first(root, name):
    return next((node for node in root.iter() if tag(node) == name), None)


def text(root, name):
    node = first(root, name)
    return (node.text or '').strip() if node is not None else ''


def vin(value):
    value = str(value).strip().upper()
    if not re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', value):
        raise CliError('Expected a valid 17-character VIN')
    return value


def address(value, decimal=False):
    try:
        number = int(str(value), 10 if decimal else 16)
    except (ValueError, TypeError) as exc:
        raise CliError(f'Invalid ECU address: {value}') from exc
    if not 0 <= number <= 254:
        raise CliError('ECU address must be physical, from 0x00 to 0xFE')
    return f'0x{number:02X}'


def part(node):
    process_class = text(node, 'processClass').upper()
    ident = text(node, 'id').upper()
    versions = [text(node, name) for name in ('mainVersion', 'subVersion', 'patchVersion')]
    if not re.fullmatch(r'[A-Z]{4}', process_class) or not re.fullmatch(r'[0-9A-F]{8}', ident):
        raise CliError('Invalid software part identifier')
    if any(not re.fullmatch(r'\d{1,3}', value) for value in versions):
        raise CliError('Invalid software part version')
    version = '_'.join(f'{int(value):03d}' for value in versions)
    return {'process_class': process_class, 'id': ident, 'version': version,
            'sgbmid': f'{process_class}_{ident}_{version}'}


def parse_fa(path):
    root = xml(path, ('fa', 'faList'))
    entries = [node for node in root.iter() if tag(node) == 'fa']
    if len(entries) != 1:
        raise CliError('FA must contain exactly one vehicle order')
    headers = [node for node in entries[0] if tag(node) == 'header']
    standards = [node for node in entries[0] if tag(node) == 'standardFA']
    if len(headers) != 1 or len(standards) != 1:
        raise CliError('FA has no identity header or standardFA')
    header, standard = headers[0], standards[0]
    return {'vin': vin(header.get('vinLong', '')), 'series': standard.get('series', '').upper(),
            'type_key': standard.get('typeKey', ''), 'identity_source': 'file'}


def parse_svt(path):
    root = xml(path, 'svt')
    ecus, seen = [], set()
    for ecu in (node for node in root.iter() if tag(node) == 'ecu'):
        diag = first(ecu, 'diagnosticAddress')
        if diag is None:
            raise CliError('SVT ECU has no diagnostic address')
        addr = address(diag.get('physicalOffset'), decimal=True)
        if addr in seen:
            raise CliError(f'Duplicate ECU address {addr}')
        seen.add(addr)
        detail = first(ecu, 'ecuDetailInfo')
        bus = first(ecu, 'ecuDiagBusConnectionInfo')
        ecus.append({'name': ecu.get('baseVariant', ''), 'address': addr,
                     'uds': detail is not None and detail.get('ISO14229Enabled') == 'true',
                     'bus': bus.get('busType', '') if bus is not None else '',
                     'parts': [part(p) for p in ecu.iter() if tag(p) == 'partIdentification']})
    if not ecus:
        raise CliError('SVT contains no ECUs')
    return {'ecus': ecus, 'identity_source': 'file'}


def parse_tal(path):
    root = xml(path, 'tal')
    lines = []
    for line in (node for node in root if tag(node) == 'talLine'):
        operations = [tag(child) for child in line]
        lines.append({'name': line.get('baseVariant', ''), 'address': address(line.get('diagAddress')),
                      'operations': operations,
                      'operation_parts': [{'operation': tag(child),
                                           'parts': [part(p) for p in child.iter() if tag(p) == 'sgbmid']}
                                          for child in line],
                      'parts': [part(p) for p in line.iter() if tag(p) == 'sgbmid']})
    return {'project': text(root, 'mcdProjectName'), 'series': text(root, 'vehicleInfo'),
            'vin': text(root, 'vin'), 'status': root.get('status', ''), 'lines': lines}


def load_profile(path):
    try:
        profile = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (ValueError, OSError) as exc:
        raise CliError(f'Cannot read profile: {exc}') from exc
    return validate_profile(profile)


def validate_profile(profile):
    if isinstance(profile, dict):
        profile = dict(profile)
    if not isinstance(profile, dict) or profile.get('schema_version') != 1:
        raise CliError('Profile schema_version must be 1')
    required = {'schema_version', 'expected_vin', 'series', 'project', 'host', 'port'}
    if set(profile) != required:
        raise CliError('Profile must contain exactly schema_version, expected_vin, series, project, host, port')
    profile['expected_vin'] = vin(profile['expected_vin'])
    series, project, host = profile['series'], profile['project'], profile['host']
    if not isinstance(series, str) or not re.fullmatch(r'[A-Z0-9]{4}', series):
        raise CliError('Invalid vehicle series')
    if not isinstance(project, str) or not re.fullmatch(re.escape(series) + r'_\d{2}_\d{2}_\d{3}_V_\d{3}_\d{3}_\d{3}', project):
        raise CliError('Project must match the profile series and version format')
    if not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,251}[A-Za-z0-9]|[A-Za-z0-9]', host) or '..' in host:
        raise CliError('Host must be an IPv4 address or hostname')
    if type(profile['port']) is not int or not 1 <= profile['port'] <= 65535:
        raise CliError('Port must be an integer from 1 to 65535')
    return profile


def check_identity(fa, profile):
    if fa['vin'] != profile['expected_vin'] or fa['series'] != profile['series']:
        raise CliError('FA identity does not match the vehicle profile')


def projects(data_root):
    from .datasets import Dataset
    return Dataset(data_root).projects()


def state(svt, allowed):
    ecus = {ecu['address']: ecu for ecu in svt['ecus']}
    result = {}
    for addr in allowed:
        if addr not in ecus:
            raise CliError(f'SVT is missing scoped ECU {addr}')
        ecu = ecus[addr]
        result[addr] = {'name': ecu['name'], 'parts': sorted(p['sgbmid'] for p in ecu['parts'])}
    return result


def plan_digest(value):
    body = {key: item for key, item in value.items() if key != 'plan_hash'}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def create_plan(profile_path, fa, ist, soll, tal, allowed, data_root):
    paths = {'profile': profile_path, 'fa': fa, 'ist': ist, 'soll': soll, 'tal': tal}
    inputs = {key: {'path': str(Path(path).resolve()), 'sha256': sha256(path)} for key, path in paths.items()}
    profile = load_profile(profile_path)
    check_identity(parse_fa(fa), profile)
    data_root = Path(data_root).resolve()
    from .datasets import Dataset
    dataset = Dataset(data_root)
    if not dataset.has_project(profile['series'], profile['project']):
        raise CliError('Profile project is missing from PSdZData')
    allowed = sorted(set(address(value) for value in allowed))
    parsed = parse_tal(tal)
    before, after = parse_svt(ist), parse_svt(soll)
    if not allowed or not parsed['lines'] or {line['address'] for line in parsed['lines']} != set(allowed):
        raise CliError('TAL ECU scope must exactly match --allow-ecu')
    if parsed['project'] != profile['project'] or parsed['series'] != profile['series']:
        raise CliError('TAL project/series does not match the profile')
    if parsed['vin'] and vin(parsed['vin']) != profile['expected_vin']:
        raise CliError('TAL VIN does not match the profile')
    if parsed['status'] != 'Executable':
        raise CliError('TAL must have Executable status')
    pre, post = state(before, allowed), state(after, allowed)
    if pre == post:
        raise CliError('Scoped pre-state already equals target; refusing a redundant plan')
    payloads = {}
    supported = {'blFlash': 'BTLD', 'swDeploy': 'SWFL', 'cdDeploy': 'CAFD'}
    covered = {addr: set() for addr in allowed}
    for line in parsed['lines']:
        addr = line['address']
        if line['name'] != pre[addr]['name'] or line['name'] != post[addr]['name']:
            raise CliError('TAL and SVT ECU names do not agree')
        old_hardware = [p for p in pre[addr]['parts'] if p.startswith('HWEL_')]
        new_hardware = [p for p in post[addr]['parts'] if p.startswith('HWEL_')]
        if not old_hardware or old_hardware != new_hardware:
            raise CliError('Physical hardware identities must agree between scoped SVTs')
        if not line['operations'] or any(op not in supported for op in line['operations']):
            raise CliError('Only blFlash, swDeploy and cdDeploy TAL operations are supported')
        for group in line['operation_parts']:
            operation, references = group['operation'], group['parts']
            expected = supported[operation]
            if not any(p['process_class'] == expected for p in references):
                raise CliError(f'{operation} has no expected software reference')
            if any(p['process_class'] not in (expected, 'HWEL') for p in references):
                raise CliError(f'{operation} contains an incompatible software reference')
            covered[addr].update(p['sgbmid'] for p in references if p['process_class'] == expected)
        for item in line['parts']:
            if item['sgbmid'] not in post[addr]['parts']:
                raise CliError('TAL software reference is absent from target SVT')
            pc = item['process_class']
            if pc == 'HWEL':
                continue
            if pc not in supported.values():
                raise CliError(f'Unsupported TAL software class: {pc}')
            for path in dataset.payload_paths(item):
                payloads[str(path)] = {'path': str(path), 'sha256': sha256(path)}
    for addr in allowed:
        old, new = set(pre[addr]['parts']), set(post[addr]['parts'])
        if any(p.split('_', 1)[0] not in supported.values() for p in old ^ new):
            raise CliError('Unsupported software class changes in scoped target')
        if not (new - old).issubset(covered[addr]):
            raise CliError('TAL operations do not cover every changed target software component')
        for pc in supported.values():
            removed = {p for p in old - new if p.startswith(pc + '_')}
            targets = {p for p in new if p.startswith(pc + '_')}
            if removed and (not targets or not targets.issubset(covered[addr])):
                raise CliError('TAL does not cover the full target class for removed software components')
    for item in inputs.values():
        if sha256(item['path']) != item['sha256']:
            raise CliError('An input changed during planning')
    plan = {'schema_version': 1, 'profile': profile, 'data_root': str(data_root), 'allowed_ecus': allowed,
            'inputs': inputs, 'payloads': sorted(payloads.values(), key=lambda p: p['path']),
            'pre_state': pre, 'post_state': post, 'operations': parsed['lines']}
    plan['plan_hash'] = plan_digest(plan)
    return plan


def verify_plan(plan):
    try:
        if not isinstance(plan, dict) or plan.get('schema_version') != 1 or plan_digest(plan) != plan.get('plan_hash'):
            raise CliError('Plan hash or schema is invalid')
        for item in list(plan['inputs'].values()) + plan['payloads']:
            if sha256(item['path']) != item['sha256']:
                raise CliError(f'Planned artifact changed: {item["path"]}')
        args = [plan['inputs'][name]['path'] for name in ('profile', 'fa', 'ist', 'soll', 'tal')]
        rebuilt = create_plan(*args, plan['allowed_ecus'], plan['data_root'])
        if rebuilt != plan:
            raise CliError('Plan no longer agrees with its parsed artifacts')
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise CliError(f'Cannot validate plan: {exc}') from exc
    return plan
