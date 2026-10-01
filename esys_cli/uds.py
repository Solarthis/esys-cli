"""Small, strict UDS service codecs; no manufacturer fault-description database."""
from .core import CliError, vin


STATUS_FLAGS = ('test_failed', 'failed_this_operation_cycle', 'pending', 'confirmed',
                'test_not_completed_since_clear', 'failed_since_clear',
                'test_not_completed_this_operation_cycle', 'warning_indicator_requested')


def read_did(transport, ecu, did):
    if type(did) is not int or not 0 <= did <= 65535:
        raise CliError('DID must be a 16-bit integer')
    identifier = did.to_bytes(2, 'big')
    response = transport.request(ecu, b'\x22' + identifier)
    if len(response) < 4 or response[:3] != b'\x62' + identifier:
        raise CliError('ReadDataByIdentifier response is empty or has a different DID', 3)
    return response[3:]


def read_vin(transport, ecu):
    raw = read_did(transport, ecu, 0xF190)
    if len(raw) != 17:
        raise CliError('VIN DID did not return exactly 17 bytes', 3)
    try:
        return vin(raw.decode('ascii'))
    except (UnicodeError, CliError) as exc:
        raise CliError('VIN DID returned invalid identity data', 3) from exc


def read_faults(transport, ecu):
    response = transport.request(ecu, b'\x19\x02\xff')
    if len(response) < 3 or response[:2] != b'\x59\x02' or (len(response) - 3) % 4:
        raise CliError('Malformed ReadDTCInformation response', 3)
    available = response[2]
    if available == 0:
        raise CliError('ECU reports no supported DTC status bits; cannot verify fault state', 3)
    dtcs, seen = [], set()
    for offset in range(3, len(response), 4):
        code = response[offset:offset + 3].hex().upper()
        status = response[offset + 3]
        if code in seen or status == 0 or status & ~available:
            raise CliError('DTC response contains duplicate or inconsistent status records', 3)
        seen.add(code)
        dtcs.append({'code': code, 'status': status,
                     'status_flags': [name for bit, name in enumerate(STATUS_FLAGS) if status & (1 << bit)]})
    return {'status_availability_mask': available, 'requested_status_mask': 255,
            'dtcs': sorted(dtcs, key=lambda item: item['code'])}


def clear_faults(transport, ecu):
    response = transport.request(ecu, b'\x14\xff\xff\xff')
    if response != b'\x54':
        raise CliError('Malformed ClearDiagnosticInformation acknowledgement', 3)
