from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

from .core import CliError, save_json
from .settings import state_root


def properties(values):
    def escape(value):
        output = ''
        for char in str(value):
            if char in '\\=:#!':
                output += '\\' + char
            elif char in '\n\r\t':
                output += {'\n': '\\n', '\r': '\\r', '\t': '\\t'}[char]
            elif ord(char) > 127:
                data = char.encode('utf-16-be')
                output += ''.join(f'\\u{int.from_bytes(data[i:i+2], "big"):04x}' for i in range(0, len(data), 2))
            else:
                output += char
        return output
    return ''.join(f'{escape(key)}={escape(value)}\n' for key, value in values.items())


class Session:
    def __init__(self, path, job):
        self.path, self.job, self.retained = path, job, False

    def retain(self):
        self.retained = True
        job = json.loads((self.job / 'job.json').read_text(encoding='utf-8'))
        save_json(self.path, {'job': str(self.job), 'state': 'unverified_write', 'native_pid': job.get('pid')})


class NativeRunner:
    def __init__(self, esys_root, data_root, jobs_root, timeout=120, *, lock_root=None):
        self.esys_root = Path(esys_root).resolve()
        self.data_root = Path(data_root).resolve()
        self.jobs_root = Path(jobs_root).resolve()
        self.timeout = timeout
        # Independent of --jobs-root so agents cannot accidentally create parallel sessions.
        self.lock_root = Path(lock_root).resolve() if lock_root else state_root() / 'session-locks'
        self.processes = {}

    def update(self, job, **values):
        path = job / 'job.json'
        current = json.loads(path.read_text(encoding='utf-8'))
        current.update(values)
        save_json(path, current)
        return current

    def new_job(self, command):
        plant_id = '0'
        vendor_config = self.esys_root / 'config/Esys.properties'
        if vendor_config.is_file():
            matches = re.findall(r'^PlantIDdec\s*=\s*(\d+)\s*$', vendor_config.read_text(encoding='utf-8', errors='replace'), re.M)
            if len(matches) == 1 and int(matches[0]) <= 65535:
                plant_id = matches[0]
        job = self.jobs_root / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:12])
        home = job / 'runtime' / 'data'
        for directory in ('', 'CAF/NCD', 'CERT', 'Etc', 'ExecutedTAL', 'FA', 'FP', 'FV', 'IDR', 'KDS', 'Logs', 'ODX', 'Rules', 'SFA', 'SVT', 'SWE', 'SWT', 'TAL', 'TalFilter'):
            (home / directory).mkdir(parents=True, exist_ok=True)
        values = {'Path.Home': home.as_posix(), 'Path.PsdzHome': self.data_root.as_posix(),
                  'Path.NcdRootDir': (home / 'CAF/NCD').as_posix(), 'Path.OdxRules': (self.data_root / 'Rules').as_posix(),
                  'language': 'en', 'loglevel': '20000', 'deleteLogOnExit': 'false', 'PlantIDdec': plant_id,
                  'TalExecution.doNotCheckSWE': 'false', 'TalExecution.doExpectedSgbmidCheck': 'true'}
        (job / 'runtime.properties').write_text(properties(values), encoding='ascii')
        save_json(job / 'job.json', {'schema_version': 1, 'job': str(job), 'command': command,
                                    'state': 'prepared', 'write_attempted': False, 'steps': []})
        return job

    def argv(self, job, arguments):
        source = (self.esys_root / 'E-Sys.bat').read_text(encoding='utf-8', errors='replace')
        entries = re.findall(r'^set CLASSPATH=%CLASSPATH%;%SP%([^\r\n]+)', source, re.M | re.I)
        if not entries:
            raise CliError('Cannot discover vendor classpath from E-Sys.bat')
        base = (self.esys_root / 'lib').resolve()
        classpath = []
        for entry in entries:
            relative = entry.replace('\\', '/').lstrip('/')
            path = (base / relative).resolve()
            if not path.is_relative_to(base) or '%' in relative:
                raise CliError('Vendor classpath contains an unsupported external entry')
            classpath.append(str(path))
        java = self.esys_root / 'jre/bin/java.exe'
        if not java.is_file():
            raise CliError('Bundled Java executable is missing')
        return [str(java), '-Xmx1024m', '-XX:ThreadStackSize=1024', '-XX:+UseG1GC',
                '-Dfile.encoding=UTF-8', '-Djavax.net.ssl.trustStoreType=jks', '-cp', ';'.join(classpath),
                'com.bmw.esys.domain.batch.EsysBatch', *map(str, arguments), '-prop', str(job / 'runtime.properties')]

    def run(self, job, arguments, mutating=False):
        environment = os.environ.copy()
        environment['PATH'] = os.pathsep.join([str(self.esys_root / 'jre/bin'), r'C:\EDIABAS\Bin', r'C:\EC-Apps\EDIABAS\Bin', environment.get('PATH', '')])
        return self.run_process(job, self.argv(job, arguments), mutating,
                                cwd=self.esys_root / 'lib/environment', env=environment)

    def run_process(self, job, argv, mutating=False, **kwargs):
        record = json.loads((job / 'job.json').read_text(encoding='utf-8'))
        prefix = f'{len(record["steps"]):02d}'
        stdout, stderr = job / f'{prefix}.stdout.txt', job / f'{prefix}.stderr.txt'
        result = {'argv': list(map(str, argv)), 'stdout': str(stdout), 'stderr': str(stderr), 'mutating': mutating}
        with stdout.open('wb') as out, stderr.open('wb') as err:
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            try:
                process = subprocess.Popen(argv, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                                           creationflags=flags, **kwargs)
            except OSError as exc:
                raise CliError(f'Cannot start native process: {exc}', 3, {'job': str(job)}) from exc
            self.processes[process.pid] = process
            self.update(job, pid=process.pid, state='running_write' if mutating else 'running_read',
                        write_attempted=record['write_attempted'] or mutating)
            try:
                result['exit_code'] = process.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired as exc:
                if not mutating:
                    process.kill()
                    process.wait()
                    self.processes.pop(process.pid, None)
                result.update(exit_code=None, timed_out=True, pid=process.pid)
                self.update(job, state='write_unverified' if mutating else 'failed', steps=record['steps'] + [result])
                raise CliError('Native write exceeded observation timeout; process was not terminated' if mutating else 'Native read exceeded timeout',
                               4 if mutating else 3, {'job': str(job), 'pid': process.pid}) from exc
        self.processes.pop(process.pid, None)
        self.update(job, state='native_write_finished' if mutating else 'native_read_finished', steps=record['steps'] + [result])
        if result['exit_code'] != 0:
            self.update(job, state='write_unverified' if mutating else 'failed')
            raise CliError(f'Native E-Sys exited with code {result["exit_code"]}', 4 if mutating else 3,
                           {'job': str(job), 'stdout': str(stdout), 'stderr': str(stderr)})
        return result

    def assert_no_other_session(self):
        if os.name != 'nt':
            return
        command = "@(Get-Process -Name java,javaw,E-Sys -ErrorAction SilentlyContinue | Select-Object Id,Path,ProcessName) | ConvertTo-Json -Compress"
        executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        try:
            result = subprocess.run([str(executable), '-NoProfile', '-NonInteractive', '-Command', command],
                                    capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode != 0:
                raise ValueError('Process inspection failed')
            entries = json.loads(result.stdout) if result.stdout.strip() else []
            if isinstance(entries, dict):
                entries = [entries]
            for entry in entries:
                path = entry.get('Path')
                if not path or Path(path).is_relative_to(self.esys_root):
                    raise CliError('Another E-Sys/Java session is running or cannot be excluded', 5, {'pid': entry.get('Id')})
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            raise CliError('Cannot verify E-Sys session exclusion', 5) from exc

    @contextmanager
    def session(self, profile, job):
        self.lock_root.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha256(profile['expected_vin'].encode('ascii')).hexdigest()[:20]
        path = self.lock_root / (name + '.lock')
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
