from contextlib import contextmanager
import json
from pathlib import Path
import re
import shutil

from .core import CliError, check_identity, parse_fa, parse_svt, parse_tal, save_json, sha256, state, verify_plan, xml
from .native import properties


def artifact(path):
    return {'path': str(path), 'bytes': path.stat().st_size, 'sha256': sha256(path)}


class Workflow:
    def __init__(self, runner):
        self.runner = runner

    def config(self, job, name, values):
        path = job / (name + '.properties')
        path.write_text(properties(values), encoding='ascii')
        return path

    def connection(self, job, profile):
        return self.config(job, 'connection', {'PROJECT': profile['project'], 'VEHICLEINFO': profile['series'],
                                               'CONNECTION': 'icom_ethernet', 'URL': f'tcp://{profile["host"]}:{profile["port"]}'})

    def stage(self, job, name, source, expected=None):
        source = Path(source)
        digest = expected or sha256(source)
        target = job / 'inputs' / name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, target)
        if sha256(source) != digest or sha256(target) != digest:
            raise CliError('Artifact changed during staging')
        return target

    @contextmanager
    def live(self, profile, command):
        job = self.runner.new_job(command)
        with self.runner.session(profile, job):
            try:
                self.runner.assert_no_other_session()
                yield job
            except Exception as exc:
                self.runner.update(job, state='failed', error=str(exc))
                raise

    def read_xml(self, job, kind, profile, folder):
        output = job / folder
        output.mkdir(parents=True, exist_ok=True)
        self.runner.run(job, ['-read' + kind, '-out', str(output), '-connection', str(self.connection(job, profile))])
        files = list(output.rglob('*.xml'))
        if len(files) != 1:
            raise CliError(f'Native {kind} must produce exactly one XML artifact', 3, {'job': str(job)})
        parsed = parse_fa(files[0]) if kind == 'fa' else parse_svt(files[0])
        if kind == 'fa':
            check_identity(parsed, profile)
        return files[0], parsed

    def coding(self, job, profile, svt_path):
        output = job / 'NCD'
        output.mkdir(exist_ok=True)
        self.runner.run(job, ['-readncd', str(svt_path), '-out', str(output), '-connection', str(self.connection(job, profile))])
        files = [p for p in output.rglob('*') if p.is_file()]
        if not any(p.suffix.lower() == '.ncd' and p.stat().st_size for p in files):
            raise CliError('Native NCD read produced no nonempty coding backup', 3, {'job': str(job)})
        return files

    def capture(self, profile, include_ncd=False):
        with self.live(profile, 'capture') as job:
            fa_path, fa = self.read_xml(job, 'fa', profile, 'FA')
            svt_path, svt = self.read_xml(job, 'svt', profile, 'SVT')
            files = [fa_path, svt_path]
            if include_ncd:
                files += self.coding(job, profile, svt_path)
            result = {'job': str(job), 'identity_source': 'live', 'fa': fa, 'svt': svt,
                      'files': [artifact(path) for path in files], 'write_attempted': False}
            self.runner.update(job, state='verified', result=result)
            return result

    def read(self, profile, kind, svt=None, ecu=None, vcm_type=None):
        with self.live(profile, 'read-' + kind) as job:
            fa_path, fa = self.read_xml(job, 'fa', profile, 'FA')
            result = {'job': str(job), 'identity_source': 'live', 'fa': fa, 'files': [artifact(fa_path)], 'write_attempted': False}
            if kind in ('svt', 'ncd', 'software-version'):
                svt_path, parsed = self.read_xml(job, 'svt', profile, 'SVT')
                result['svt'] = parsed
                result['files'].append(artifact(svt_path))
                if kind == 'ncd':
                    if svt:
                        selected = parse_svt(svt)
                        allowed = [item['address'] for item in selected['ecus']]
                        if state(parsed, allowed) != state(selected, allowed):
                            raise CliError('Supplied SVT does not match fresh scoped ECU state')
                        svt_path = self.stage(job, 'ncd-svt.xml', svt)
                    result['files'] += [artifact(p) for p in self.coding(job, profile, svt_path)]
                elif kind == 'software-version':
                    from .core import address
                    selected = next((item for item in parsed['ecus'] if item['address'] == address(ecu)), None)
                    versions = [item for item in selected['parts'] if item['process_class'] in ('BTLD', 'SWFL')] if selected else []
                    if not versions:
                        raise CliError('Fresh SVT contains no firmware versions for the requested ECU', 3)
                    result.update(ecu=selected['address'], firmware_versions=versions, version_source='fresh_svt')
            elif kind == 'vin':
                result.update(vin=fa['vin'], vin_source='fresh_fa')
            elif kind in ('vcm-master', 'vcm-backup'):
                allowed = ['FA', 'FP', 'ISTUFEN', 'SVTSOLL'] if kind == 'vcm-master' else ['FA', 'ISTUFEN']
                if vcm_type not in allowed:
                    raise CliError(f'VCM type must be one of {allowed}')
                output = job / 'VCM'
                output.mkdir()
                command = '-readVcmMaster' if kind == 'vcm-master' else '-readVcmBackup'
                self.runner.run(job, [command, vcm_type, '-out', str(output), '-connection', str(self.connection(job, profile))])
                files = [p for p in output.rglob('*') if p.is_file() and p.stat().st_size]
                if not files:
                    raise CliError('VCM read produced no artifacts', 3)
                result['files'] += [artifact(path) for path in files]
            self.runner.update(job, state='verified', result=result)
            return result

    def calculate(self, profile, fa, ist, soll, filter_path):
        check_identity(parse_fa(fa), profile)
        parse_svt(ist)
        parse_svt(soll)
        xml(filter_path, 'talFilter')
        # The installed batch engine opens a connection before calculating a TAL.
        with self.live(profile, 'calculate-tal') as job:
            self.read_xml(job, 'fa', profile, 'calculation-FA')
            staged = {name: self.stage(job, name + '.xml', path) for name, path in [('fa', fa), ('ist', ist), ('soll', soll), ('filter', filter_path)]}
            output = job / 'generated.tal.xml'
            idr = job / 'IDR'
            idr.mkdir()
            config = self.config(job, 'talcalculation', {'PROJECT': profile['project'], 'VEHICLEINFO': profile['series'],
                     'CONNECTION': 'icom_ethernet', 'URL': f'tcp://{profile["host"]}:{profile["port"]}',
                     'FA': staged['fa'].as_posix(), 'SVT_IST': staged['ist'].as_posix(), 'SVT_SOLL': staged['soll'].as_posix(),
                     'TAL_TYPE': 'normal', 'GENERATED_TAL': output.as_posix(), 'TAL_FILE': output.as_posix(),
                     'TAL_FILTER': staged['filter'].as_posix(), 'IDR_BACKUP_PATH': idr.as_posix()})
            self.runner.run(job, ['-talcalculation', str(config)])
            if not output.is_file():
                raise CliError('Native TAL calculation produced no TAL artifact', 3, {'job': str(job)})
            parsed = parse_tal(output)
            if parsed['project'] != profile['project'] or parsed['series'] != profile['series']:
                raise CliError('Generated TAL project does not match profile', 3)
            if parsed['vin'] and parsed['vin'].upper() != profile['expected_vin']:
                raise CliError('Generated TAL VIN does not match profile', 3)
            result = {'job': str(job), 'config': str(config), 'tal': str(output), 'parsed': parsed,
                      'identity_source': 'live', 'write_attempted': False}
            self.runner.update(job, state='verified', result=result)
            return result

    def execute(self, plan_path, accepted_hash, power_confirmed, write_timeout=3600):
        plan = verify_plan(json.loads(Path(plan_path).read_text(encoding='utf-8-sig')))
        if accepted_hash != plan['plan_hash'] or not power_confirmed:
            raise CliError('Execution requires the exact --accept-plan hash and --power-confirmed')
        if Path(plan['data_root']).resolve() != self.runner.data_root:
            raise CliError('Runner dataset root differs from the reviewed plan')
        profile = plan['profile']
        job = self.runner.new_job('execute')
        write_started = False
        with self.runner.session(profile, job) as session:
            try:
                self.runner.assert_no_other_session()
                staged = {name: self.stage(job, name + '.xml', item['path'], item['sha256'])
                          for name, item in plan['inputs'].items() if name != 'profile'}
                self.read_xml(job, 'fa', profile, 'pre-FA')
                _, pre = self.read_xml(job, 'svt', profile, 'pre-SVT')
                if state(pre, plan['allowed_ecus']) != plan['pre_state']:
                    raise CliError('Fresh scoped ECU state differs from reviewed plan; generate a new plan')
                verify_plan(plan)
                idr = job / 'IDR'
                idr.mkdir()
                config = self.config(job, 'talexecution', {'TAL': staged['tal'].as_posix(), 'FA': staged['fa'].as_posix(),
                         'SVT': staged['soll'].as_posix(), 'PROJECT': profile['project'], 'VEHICLEINFO': profile['series'],
                         'CONNECTION': 'icom_ethernet', 'URL': f'tcp://{profile["host"]}:{profile["port"]}',
                         'IDR_BACKUP_PATH': idr.as_posix(), 'CODING_TYPE': 'fa', 'CODING_USE_LOCAL_NCD': 'false',
                         'PARALLEL_PROGRAMMING': 'false'})
                for name, path in staged.items():
                    if sha256(path) != plan['inputs'][name]['sha256']:
                        raise CliError(f'Staged {name} changed after preflight; refusing execution')
                old_timeout = self.runner.timeout
                self.runner.timeout = write_timeout
                write_started = True
                try:
                    step = self.runner.run(job, ['-talexecution', str(config)], mutating=True)
                finally:
                    self.runner.timeout = old_timeout
                output = Path(step['stdout']).read_text(encoding='utf-8', errors='replace')
                finished = re.search(r'(?im)^\s*TAL[- ]Execution finished with status:\s*"?Finished"?\.?\s*$', output)
                if not finished or re.search(r'\[ERROR\]|finished with status:\s*"?\w*Error', output, re.I):
                    raise CliError('Native output does not establish a finished TAL execution', 4)
                self.read_xml(job, 'fa', profile, 'post-FA')
                _, post = self.read_xml(job, 'svt', profile, 'post-SVT')
                if state(post, plan['allowed_ecus']) != plan['post_state']:
                    raise CliError('Fresh post-write SVT differs from the reviewed target', 4)
                result = {'job': str(job), 'plan_hash': plan['plan_hash'], 'write_attempted': True,
                          'verification': 'scoped_svt_and_finished_tal', 'pre': pre, 'post': post,
                          'calibration_verified': False}
                self.runner.update(job, state='verified', result=result)
                return result
            except BaseException as exc:
                if write_started:
                    session.retained = True
                    try:
                        self.runner.update(job, state='write_unverified', error=str(exc))
                        session.retain()
                    except OSError:
                        session.retained = True
                    raise CliError(f'Write outcome is unverified: {exc}', 4, {'job': str(job)}) from exc
                self.runner.update(job, state='failed', error=str(exc))
                raise
