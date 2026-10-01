"""CLI wiring for vendor-independent diagnostics and the loopback demo."""
from pathlib import Path
import uuid

from .core import CliError
from .diagnostics import Diagnostics, load_profile, validate_profile, write_new


def hex_number(value):
    try:
        return int(value, 16)
    except (ValueError, TypeError) as exc:
        raise CliError(f'Expected a hexadecimal integer: {value}') from exc


def add_parser(commands):
    parser = commands.add_parser('diag', help='Independent UDS diagnostics; no E-Sys or datasets')
    parser.add_argument('--request-timeout', type=float, default=5)
    actions = parser.add_subparsers(dest='diag_action', required=True)
    profile = actions.add_parser('profile').add_subparsers(dest='diag_profile_action', required=True)
    init = profile.add_parser('init')
    init.add_argument('--transport', choices=['hsfz', 'doip'], required=True)
    for name in ('host', 'vin', 'identity-ecu', 'output'):
        init.add_argument('--' + name, required=True)
    init.add_argument('--ecu', action='append', required=True)
    init.add_argument('--port', type=int)
    init.add_argument('--source')
    init.add_argument('--protocol-version', type=int, choices=[2, 3], default=2)
    init.add_argument('--activation-type', default='00')
    for command in ('vin', 'faults', 'read-did', 'clear-plan'):
        action = actions.add_parser(command)
        action.add_argument('--profile', required=True)
        if command == 'read-did':
            action.add_argument('--ecu', required=True)
            action.add_argument('--did', required=True)
        elif command == 'clear-plan':
            action.add_argument('--output', required=True)
    clear = actions.add_parser('clear', help='Clear only the ECUs in a fresh accepted plan')
    clear.add_argument('--plan', required=True)
    clear.add_argument('--accept-plan', required=True)
    clear.add_argument('--vehicle-stationary', action='store_true')
    demo = actions.add_parser('demo', help='Exercise read/plan/clear against a loopback simulator only')
    demo.add_argument('--transport', choices=['hsfz', 'doip'], default='hsfz')


def dispatch(args):
    if args.diag_action == 'profile':
        hsfz = args.transport == 'hsfz'
        profile = validate_profile({'schema_version': 1, 'kind': 'uds-profile', 'transport': args.transport,
                    'host': args.host, 'port': args.port if args.port is not None else (6801 if hsfz else 13400),
                    'source': hex_number(args.source) if args.source is not None else (0xF4 if hsfz else 0x0E00),
                    'identity_ecu': hex_number(args.identity_ecu), 'ecus': [hex_number(value) for value in args.ecu],
                    'expected_vin': args.vin, 'protocol_version': args.protocol_version,
                    'activation_type': hex_number(args.activation_type)})
        write_new(args.output, profile)
        return {'profile': str(Path(args.output).resolve()), 'identity_source': 'configured_not_live',
                'requires_esys': False, 'requires_datasets': False}
    if args.diag_action == 'demo':
        from .simulator import Simulator
        root = Path(args.jobs_root).resolve() / ('simulation-' + uuid.uuid4().hex[:12])
        workflow = Diagnostics(root / 'jobs', args.request_timeout, lock_root=root / 'locks')
        with Simulator(args.transport) as simulator:
            observed = workflow.read_vin(simulator.profile)
            plan = workflow.clear_plan(simulator.profile, root / 'clear-plan.json')
            result = workflow.clear(root / 'clear-plan.json', plan['plan_hash'], True)
            return {'simulation': True, 'transport': args.transport, 'network_scope': '127.0.0.1',
                    'vendor_dependencies_used': False, 'vin': observed['vin'], 'clear': result}
    workflow = Diagnostics(args.jobs_root, args.request_timeout)
    if args.diag_action == 'clear':
        return workflow.clear(args.plan, args.accept_plan, args.vehicle_stationary)
    profile = load_profile(args.profile)
    if args.diag_action == 'clear-plan':
        return workflow.clear_plan(profile, args.output)
    if args.diag_action == 'read-did':
        return workflow.read_did(profile, hex_number(args.ecu), hex_number(args.did))
    if args.diag_action == 'vin':
        return workflow.read_vin(profile)
    return workflow.faults(profile)
