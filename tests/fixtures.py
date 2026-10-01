import json
from pathlib import Path

VIN = 'WBA00000000000001'
PROJECT = 'F025_26_03_550_V_004_000_000'


def fa(vin=VIN):
    return f'<fa><header vinLong="{vin}"/><standardFA series="F025" typeKey="WX92"/></fa>'


def part_xml(pc, ident, version):
    a, b, c = version.split('_')
    return f'<processClass>{pc}</processClass><id>{ident}</id><mainVersion>{a}</mainVersion><subVersion>{b}</subVersion><patchVersion>{c}</patchVersion>'


def svt(version='001_000_000', addr=41):
    return f'<svt xmlns="fixture"><standardSVT><ecu baseVariant="DSC"><diagnosticAddresses><diagnosticAddress physicalOffset="{addr}"/></diagnosticAddresses><standardSVK><partIdentification>{part_xml("HWEL", "00000628", "050_001_001")}</partIdentification><partIdentification>{part_xml("SWFL", "00000C79", version)}</partIdentification></standardSVK></ecu></standardSVT></svt>'


def tal(addr='29', version='002_000_000'):
    return f'<tal status="Executable"><generationContext><connectionInfo><mcdProjectName>{PROJECT}</mcdProjectName><vehicleInfo>F025</vehicleInfo></connectionInfo></generationContext><talLine baseVariant="DSC" diagAddress="{addr}"><swDeploy><sgbmid>{part_xml("SWFL", "00000C79", version)}</sgbmid></swDeploy></talLine></tal>'


def setup(root):
    root = Path(root)
    values = {'fa.xml': fa(), 'ist.xml': svt(), 'soll.xml': svt('002_000_000'), 'tal.xml': tal(), 'filter.xml': '<talFilter/>'}
    for name, value in values.items():
        (root / name).write_text(value)
    profile = {'schema_version': 1, 'expected_vin': VIN, 'series': 'F025', 'project': PROJECT, 'host': '127.0.0.1', 'port': 6801}
    (root / 'profile.json').write_text(json.dumps(profile))
    data = root / 'data'
    (data / 'psdzdata/mainseries/F025' / PROJECT).mkdir(parents=True)
    payload = data / 'psdzdata/swe/swfl/swfl_00000c79.bin.002_000_000'
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b'firmware fixture')
    payload.with_name('swfl_00000c79.xml.002_000_000').write_text('<BINARY-HEADER/>')
    payload.with_name('swfl_00000c79.bsw.002_000_000').write_bytes(b'')
    return profile, data


def fake_runner(root, data, vin=VIN, post_version='002_000_000', fail_write=False):
    from esys_cli.native import NativeRunner
    from esys_cli.core import CliError
    class Fake(NativeRunner):
        def assert_no_other_session(self):
            pass

        def run(self, job, arguments, mutating=False):
            command = arguments[0]
            stdout = job / f'{len(list(job.glob("*.fake.txt")))}.fake.txt'
            stdout.write_text('native fixture output')
            if command in ('-readfa', '-readsvt', '-readncd'):
                output = Path(arguments[arguments.index('-out') + 1])
                output.mkdir(parents=True, exist_ok=True)
                if command == '-readfa':
                    (output / 'FA.xml').write_text(fa(vin))
                elif command == '-readsvt':
                    version = post_version if (job / 'write-attempted.marker').exists() else '001_000_000'
                    (output / 'SVT.xml').write_text(svt(version))
                else:
                    (output / 'DSC.ncd').write_bytes(b'coding backup')
            elif command == '-talexecution':
                (job / 'write-attempted.marker').write_text('attempted')
                self.update(job, write_attempted=True)
                if fail_write:
                    raise CliError('external write failed', 4, {'job': str(job)})
                stdout.write_text('TAL-Execution finished with status: "Finished".\n')
            elif command == '-talcalculation':
                lines = Path(arguments[1]).read_text().splitlines()
                target = next(line.split('=', 1)[1] for line in lines if line.startswith('GENERATED_TAL='))
                Path(target.replace('\\:', ':').replace('\\=', '=')).write_text(tal())
            else:
                raise AssertionError(f'Unexpected native operation {command}')
            return {'exit_code': 0, 'stdout': str(stdout), 'stderr': str(stdout)}
    return Fake(root, data, Path(root) / 'jobs', lock_root=Path(root) / 'locks')
