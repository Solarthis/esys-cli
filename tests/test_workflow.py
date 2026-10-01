from pathlib import Path
import tempfile
import unittest
from fixtures import VIN, fake_runner, setup


class WorkflowTests(unittest.TestCase):
    def test_version_read_returns_typed_fresh_svt_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile, data = setup(root)
            runner = fake_runner(root, data)
            original = runner.run
            def banner(job, arguments, mutating=False):
                if arguments[0] == '-readSoftwareVersion':
                    output = job/'banner.txt'
                    output.write_text('E-Sys startup completed')
                    return {'stdout': str(output), 'exit_code': 0}
                return original(job, arguments, mutating)
            runner.run = banner
            result = self.workflow(runner).read(profile, 'software-version', ecu='0x29')
            self.assertEqual(result['version_source'], 'fresh_svt')
            self.assertEqual(result['firmware_versions'][0]['version'], '001_000_000')

    def test_vin_read_returns_verified_live_fa_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile, data = setup(root)
            runner = fake_runner(root, data)
            original = runner.run
            def misleading_vin(job, arguments, mutating=False):
                if arguments[0] == '-readVinFromMaster':
                    output = job/'banner.txt'
                    output.write_text('Expected VIN ' + VIN + '; actual VIN unavailable')
                    return {'stdout': str(output), 'exit_code': 0}
                return original(job, arguments, mutating)
            runner.run = misleading_vin
            result = self.workflow(runner).read(profile, 'vin')
            self.assertEqual(result['vin'], VIN)
            self.assertEqual(result['vin_source'], 'fresh_fa')

    def test_missing_native_svt_is_not_success(self):
        from esys_cli.core import CliError
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile, data = setup(root)
            runner = fake_runner(root, data)
            original = runner.run
            def without_svt(job, arguments, mutating=False):
                result = original(job, arguments, mutating)
                if arguments[0] == '-readsvt':
                    for path in (job / 'SVT').glob('*.xml'):
                        path.unlink()
                return result
            runner.run = without_svt
            with self.assertRaises(CliError) as failed:
                self.workflow(runner).capture(profile)
            self.assertEqual(failed.exception.code, 3)

    def workflow(self, runner):
        try:
            from esys_cli.workflow import Workflow
        except ImportError:
            self.fail('vehicle workflow contracts are not implemented')
        return Workflow(runner)

    def test_capture_has_live_identity_and_coding_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile, data = setup(root)
            result = self.workflow(fake_runner(root, data)).capture(profile, True)
            self.assertEqual(result['fa']['vin'], VIN)
            self.assertEqual(result['identity_source'], 'live')
            self.assertEqual(result['svt']['ecus'][0]['address'], '0x29')
            self.assertTrue(any(item['path'].endswith('.ncd') for item in result['files']))

    def test_wrong_fresh_vin_is_rejected(self):
        from esys_cli.core import CliError
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile, data = setup(root)
            with self.assertRaises(CliError):
                self.workflow(fake_runner(root, data, vin='WBA00000000000002')).capture(profile)
            self.assertFalse(list((root / 'jobs').glob('*/SVT/*.xml')))

    def test_calculation_stages_artifacts_with_verified_live_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile, data = setup(root)
            result = self.workflow(fake_runner(root, data)).calculate(profile, root/'fa.xml', root/'ist.xml', root/'soll.xml', root/'filter.xml')
            self.assertTrue(Path(result['tal']).is_file())
            config = Path(result['config']).read_text()
            self.assertIn('URL=tcp\\://127.0.0.1\\:6801', config)
            self.assertIn('CONNECTION=icom_ethernet', config)
            self.assertTrue((Path(result['job']) / 'inputs' / 'fa.xml').is_file())
            self.assertTrue((Path(result['job']) / 'calculation-FA' / 'FA.xml').is_file())
            self.assertFalse(list((root/'locks').glob('*.lock')))

    def test_calculation_rejects_wrong_connected_vin(self):
        from esys_cli.core import CliError
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile, data = setup(root)
            with self.assertRaises(CliError):
                self.workflow(fake_runner(root, data, vin='WBA00000000000002')).calculate(
                    profile, root/'fa.xml', root/'ist.xml', root/'soll.xml', root/'filter.xml')
            self.assertFalse(list((root/'jobs').glob('*/generated.tal.xml')))


if __name__ == '__main__':
    unittest.main()
