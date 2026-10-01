import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


class OfflineTests(unittest.TestCase):
    def test_dataset_profiles_duplicate_ecus_and_utf16_entities(self):
        from esys_cli.core import CliError, load_profile, parse_fa, parse_svt, projects
        from fixtures import setup, svt, PROJECT
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, data = setup(root)
            self.assertEqual(projects(data)[0]['project'], PROJECT)
            profile = json.loads((root/'profile.json').read_text())
            profile['host'] = '127.0.0.1\nINJECTED=value'
            (root/'profile.json').write_text(json.dumps(profile))
            with self.assertRaises(CliError):
                load_profile(root/'profile.json')
            import xml.etree.ElementTree as ET
            tree = ET.fromstring(svt())
            standard = list(tree)[0]
            standard.append(ET.fromstring(ET.tostring(list(standard)[0])))
            (root/'svt.xml').write_bytes(ET.tostring(tree))
            with self.assertRaises(CliError):
                parse_svt(root/'svt.xml')
            (root/'fa.xml').write_bytes('<!DOCTYPE fa [<!ENTITY x "hidden">]><fa/>'.encode('utf-16'))
            with self.assertRaises(CliError):
                parse_fa(root/'fa.xml')

    def test_native_fa_list_accepts_one_vehicle_and_rejects_ambiguity(self):
        from esys_cli.core import CliError, parse_fa
        from fixtures import fa, VIN
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fa.xml'
            entry = fa()
            path.write_text('<faList><id>' + entry + '</id></faList>')
            self.assertEqual(parse_fa(path)['vin'], VIN)
            path.write_text('<faList><id>' + entry + entry + '</id></faList>')
            with self.assertRaises(CliError):
                parse_fa(path)

    def test_package_provides_agent_interface(self):
        self.assertIsNotNone(importlib.util.find_spec('esys_cli'), 'agent CLI package is missing')

    def test_xml_and_json_contracts(self):
        try:
            from esys_cli.core import CliError, parse_fa, parse_svt
            from esys_cli.cli import main
        except ImportError:
            self.fail('offline CLI contracts are not implemented')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fa = root / 'vehicle.xml'
            fa.write_text('<fa><header vinLong="WBA00000000000001"/><standardFA series="F025" typeKey="WX92"/></fa>')
            self.assertEqual(parse_fa(fa)['vin'], 'WBA00000000000001')
            svt = root / 'svt.xml'
            svt.write_text('<svt xmlns="x"><standardSVT><ecu baseVariant="DSC"><diagnosticAddresses><diagnosticAddress physicalOffset="41"/></diagnosticAddresses><standardSVK><partIdentification><processClass>HWEL</processClass><id>00000628</id><mainVersion>050</mainVersion><subVersion>001</subVersion><patchVersion>001</patchVersion></partIdentification></standardSVK></ecu></standardSVT></svt>')
            self.assertEqual(parse_svt(svt)['ecus'][0]['address'], '0x29')
            fa.write_text('<!DOCTYPE fa [<!ENTITY secret "value">]><fa/>')
            with self.assertRaises(CliError):
                parse_fa(fa)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(['inspect', 'fa', str(fa)])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(output.getvalue())['status'], 'error')

    def test_bad_arguments_are_json(self):
        try:
            from esys_cli.cli import main
        except ImportError:
            self.fail('JSON argument errors are not implemented')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(['not-a-command'])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())['status'], 'error')

    def test_profile_creation_preserves_unrelated_job_files(self):
        from fixtures import setup, PROJECT
        from esys_cli.cli import main
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, data = setup(root)
            jobs = root / 'jobs'
            jobs.mkdir()
            sentinel = jobs / 'profile-validation.json'
            sentinel.write_text('existing caller data')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(['--data-root', str(data), '--jobs-root', str(jobs), 'profile', 'init',
                             '--fa', str(root/'fa.xml'), '--series', 'F025', '--project', PROJECT,
                             '--host', '127.0.0.1', '--output', str(root/'new-profile.json')])
            self.assertEqual(code, 0)
            self.assertTrue(sentinel.exists(), 'profile init removed unrelated caller data')
            self.assertEqual(sentinel.read_text(), 'existing caller data')

    def test_plan_is_available_from_json_cli(self):
        from fixtures import setup
        from esys_cli.cli import main
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, data = setup(root)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(['--data-root', str(data), 'plan', '--profile', str(root/'profile.json'),
                             '--fa', str(root/'fa.xml'), '--svt-ist', str(root/'ist.xml'),
                             '--svt-soll', str(root/'soll.xml'), '--tal', str(root/'tal.xml'),
                             '--allow-ecu', '0x29', '--output', str(root/'plan.json')])
            self.assertEqual(code, 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result['data']['allowed_ecus'], ['0x29'])
            self.assertTrue((root/'plan.json').exists())


if __name__ == '__main__':
    unittest.main()
