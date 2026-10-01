import argparse
import json
import math
import os
from pathlib import Path
import sys

from .core import CliError, address, check_identity, create_plan, load_profile, parse_fa, parse_svt, parse_tal, projects, save_json, validate_profile
from .settings import state_root


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CliError(message)


def parser():
    p = Parser(prog='esys', description='Typed E-Sys automation; JSON results on stdout.')
    p.add_argument('--esys-root', default=os.environ.get('ESYS_ROOT', r'C:\EC-Apps\ESG\E-Sys'))
    p.add_argument('--data-root', default=os.environ.get('ESYS_DATA_ROOT', r'C:\Data'))
    p.add_argument('--jobs-root', default=os.environ.get('ESYS_JOBS_ROOT', str(state_root() / 'jobs')))
    p.add_argument('--timeout', type=float, default=120)
    commands = p.add_subparsers(dest='command', required=True)
    from .diagnostic_cli import add_parser
    add_parser(commands)
    doctor = commands.add_parser('doctor', help='Inspect installation; optional metadata probe')
    doctor.add_argument('--native', action='store_true')
    commands.add_parser('capabilities')
    commands.add_parser('projects')
    dataset = commands.add_parser('dataset', help='Read-only management of external datasets').add_subparsers(dest='action', required=True)
    dataset.add_parser('inspect')
    dataset.add_parser('manifest').add_argument('--output', required=True)
    dataset.add_parser('verify').add_argument('--manifest', required=True)
    inspect = commands.add_parser('inspect')
    inspect.add_argument('kind', choices=['fa', 'svt', 'tal'])
    inspect.add_argument('file')
    profile = commands.add_parser('profile').add_subparsers(dest='action', required=True)
    init = profile.add_parser('init')
    for key in ('fa', 'series', 'project', 'host', 'output'):
        init.add_argument('--' + key, required=True)
    init.add_argument('--port', type=int, default=6801)
    job = commands.add_parser('job')
    job.add_argument('file')
    capture = commands.add_parser('capture', help='Fresh verified FA/SVT and optional NCD backup')
    capture.add_argument('--profile', required=True)
    capture.add_argument('--include-ncd', action='store_true')
    read = commands.add_parser('read')
    read.add_argument('kind', choices=['fa', 'svt', 'ncd', 'vcm-master', 'vcm-backup', 'vin', 'software-version'])
    read.add_argument('--profile', required=True)
    read.add_argument('--svt')
    read.add_argument('--ecu')
    read.add_argument('--vcm-type')
    calculate = commands.add_parser('calculate-tal', help='Connected TAL generation with verified vehicle identity')
    for key in ('profile', 'fa', 'svt-ist', 'svt-soll', 'filter'):
        calculate.add_argument('--' + key, required=True)
    plan = commands.add_parser('plan', help='Validate scope and produce an immutable-content plan')
    for key in ('profile', 'fa', 'svt-ist', 'svt-soll', 'tal', 'output'):
        plan.add_argument('--' + key, required=True)
    plan.add_argument('--allow-ecu', action='append', required=True)
    execute = commands.add_parser('execute', help='Execute one reviewed TAL plan and verify its target')
    execute.add_argument('--plan', required=True)
    execute.add_argument('--accept-plan', required=True)
    execute.add_argument('--power-confirmed', action='store_true')
    execute.add_argument('--write-timeout', type=float, default=3600)
    return p


def dispatch(args):
    if args.command == 'diag':
        from .diagnostic_cli import dispatch as diagnostic_dispatch
        return diagnostic_dispatch(args)
    if args.command == 'dataset':
        from .datasets import Dataset
        dataset = Dataset(args.data_root)
        if args.action == 'manifest':
            return dataset.manifest(args.output)
        if args.action == 'verify':
            return dataset.verify(args.manifest)
        return dataset.inspect()
    if args.command == 'inspect':
        return {'fa': parse_fa, 'svt': parse_svt, 'tal': parse_tal}[args.kind](args.file)
    if args.command == 'projects':
        return {'projects': projects(args.data_root)}
    if args.command == 'profile':
        if Path(args.output).exists():
            raise CliError('Profile output already exists; choose a new filename')
        fa = parse_fa(args.fa)
        value = {'schema_version': 1, 'expected_vin': fa['vin'], 'series': args.series,
                 'project': args.project, 'host': args.host, 'port': args.port}
        value = validate_profile(value)
        check_identity(fa, value)
        if not any(item['project'] == value['project'] for item in projects(args.data_root)):
            raise CliError('Profile project is not present in the dataset')
        write_new_json(args.output, value)
        return {'profile': str(Path(args.output).resolve()), 'identity_source': 'configured_from_file'}
    if args.command == 'job':
        return json.loads(Path(args.file).read_text(encoding='utf-8-sig'))
    if args.command == 'doctor':
        root = Path(args.esys_root)
        required = ['E-Sys.bat', 'jre/bin/java.exe', 'lib/esysCore.jar', 'lib/environment']
        checks = {name: (root / name).exists() for name in required}
        result = {'installation': str(root), 'checks': checks, 'ready': all(checks.values()),
                  'projects': projects(args.data_root)}
        if not result['ready']:
            raise CliError('E-Sys installation is incomplete', details=result)
        if args.native:
            from .native import NativeRunner
            runner = NativeRunner(args.esys_root, args.data_root, args.jobs_root, args.timeout)
            job = runner.new_job('doctor')
            step = runner.run(job, ['-version'])
            result['native'] = {'job': str(job), 'version_output': Path(step['stdout']).read_text(encoding='utf-8', errors='replace').strip()}
            runner.update(job, state='verified')
        return result
    if args.command == 'capabilities':
        return {'cli_commands': ['doctor', 'capabilities', 'diag', 'projects', 'dataset', 'profile', 'inspect', 'job', 'read', 'capture', 'calculate-tal', 'plan', 'execute'],
                'dataset_provider': 'external-filesystem',
                'programming': {'maturity': 'experimental', 'live_vehicle_validated': False},
                'native_backend': 'com.bmw.esys.domain.batch.EsysBatch',
                'read_kinds': ['fa', 'svt', 'ncd', 'vcm-master', 'vcm-backup', 'vin', 'software-version'],
                'supported_tal_operations': ['blFlash', 'swDeploy', 'cdDeploy'],
                'native_only_not_exposed': ['VCM writes', 'PDX import/update', 'certificates', 'ECU mode switching', 'secure tokens', 'backend authentication'],
                'fault_memory': {'supported': True, 'backend': 'independent-uds', 'transports': ['hsfz', 'doip'],
                                 'requires_esys': False, 'requires_datasets': False, 'live_vehicle_validated': False,
                                 'services': ['read_vin', 'read_did', 'read_faults', 'plan_clear', 'clear_faults'],
                                 'clear_requires': ['fresh_plan', 'exact_hash', 'stationary_attestation']}}
    if args.command == 'plan':
        result = create_plan(args.profile, args.fa, args.svt_ist, args.svt_soll, args.tal, args.allow_ecu, args.data_root)
        write_new_json(args.output, result)
        return result
    if args.command in ('capture', 'read', 'calculate-tal', 'execute'):
        from .native import NativeRunner
        from .workflow import Workflow
        workflow = Workflow(NativeRunner(args.esys_root, args.data_root, args.jobs_root, args.timeout))
        if args.command == 'execute':
            if not math.isfinite(args.write_timeout) or not 0 < args.write_timeout <= 86400:
                raise CliError('Write observation timeout must be positive and at most 86400 seconds')
            return workflow.execute(args.plan, args.accept_plan, args.power_confirmed, args.write_timeout)
        profile = load_profile(args.profile)
        if args.command == 'capture':
            return workflow.capture(profile, args.include_ncd)
        if args.command == 'calculate-tal':
            return workflow.calculate(profile, args.fa, args.svt_ist, args.svt_soll, args.filter)
        if args.kind == 'software-version':
            if not args.ecu:
                raise CliError('software-version requires --ecu')
            args.ecu = address(args.ecu)
        elif args.ecu:
            raise CliError('--ecu is only used with software-version')
        if args.svt and args.kind != 'ncd':
            raise CliError('--svt is only used with ncd')
        if args.vcm_type and args.kind not in ('vcm-master', 'vcm-backup'):
            raise CliError('--vcm-type is only used with VCM reads')
        return workflow.read(profile, args.kind, args.svt, args.ecu, args.vcm_type or 'FA')
    raise CliError('Command is not implemented')


def write_new_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
    except FileExistsError as exc:
        raise CliError('Output already exists; choose a new filename') from exc


def main(argv=None):
    command = None
    try:
        args = parser().parse_args(argv)
        command = args.command
        if not math.isfinite(args.timeout) or not 0 < args.timeout <= 86400:
            raise CliError('Timeout must be positive and at most 86400 seconds')
        result = {'schema_version': 1, 'status': 'ok', 'command': command, 'data': dispatch(args)}
        code = 0
    except CliError as exc:
        code = exc.code
        result = {'schema_version': 1, 'status': 'write_unverified' if code == 4 else 'error',
                  'command': command, 'error': str(exc), 'details': exc.details}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        code = 2
        result = {'schema_version': 1, 'status': 'error', 'command': command, 'error': str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return code
