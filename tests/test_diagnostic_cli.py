import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class DiagnosticCliTests(unittest.TestCase):
    def invoke(self, root, *arguments):
        result = subprocess.run([sys.executable, '-m', 'esys_cli', '--esys-root', str(root / 'missing-esys'),
                                 '--data-root', str(root / 'missing-data'), '--jobs-root', str(root / 'jobs'),
                                 *map(str, arguments)], capture_output=True, text=True, timeout=20)
        return result.returncode, json.loads(result.stdout)

    def test_full_cli_demo_works_without_vendor_software_or_data(self):
        for protocol in ['hsfz', 'doip']:
            with self.subTest(protocol=protocol), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                code, result = self.invoke(root, 'diag', 'demo', '--transport', protocol)
                self.assertEqual(code, 0, result)
                self.assertTrue(result['data']['simulation'])
                self.assertTrue(result['data']['clear']['all_faults_absent'])
                self.assertEqual(result['data']['network_scope'], '127.0.0.1')
                self.assertFalse((root / 'missing-esys').exists())
                self.assertFalse((root / 'missing-data').exists())

    def test_profile_creation_has_no_dataset_dependency_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            arguments = ['diag', 'profile', 'init', '--transport', 'hsfz', '--host', '192.0.2.10',
                         '--identity-ecu', '10', '--ecu', '29', '--vin', 'WBA00000000000001',
                         '--output', root / 'profile.json']
            code, result = self.invoke(root, *arguments)
            self.assertEqual(code, 0, result)
            value = json.loads((root / 'profile.json').read_text())
            self.assertEqual(value['ecus'], [41])
            self.assertEqual(value['port'], 6801)
            self.assertEqual(self.invoke(root, *arguments)[0], 2)

    def test_agent_capabilities_separate_independent_and_native(self):
        with tempfile.TemporaryDirectory() as directory:
            code, result = self.invoke(Path(directory), 'capabilities')
            self.assertEqual(code, 0)
            self.assertTrue(result['data']['fault_memory']['supported'])
            self.assertFalse(result['data']['fault_memory']['requires_esys'])
            self.assertFalse(result['data']['fault_memory']['requires_datasets'])
            self.assertFalse(result['data']['fault_memory']['live_vehicle_validated'])

    def test_separate_cli_commands_against_loopback_ecu(self):
        from esys_cli.simulator import Simulator
        with tempfile.TemporaryDirectory() as directory, Simulator() as simulator:
            root = Path(directory)
            profile = root / 'profile.json'
            profile.write_text(json.dumps(simulator.profile))
            code, result = self.invoke(root, 'diag', 'faults', '--profile', profile)
            self.assertEqual(code, 0, result)
            self.assertEqual(result['data']['faults']['0x29']['dtcs'][0]['code'], '123456')
            code, result = self.invoke(root, 'diag', 'read-did', '--profile', profile, '--ecu', '29', '--did', 'F189')
            self.assertEqual(code, 0, result)
            self.assertEqual(result['data']['hex'], '53494D2D312E30')
            code, plan = self.invoke(root, 'diag', 'clear-plan', '--profile', profile, '--output', root / 'clear.json')
            self.assertEqual(code, 0, plan)
            code, result = self.invoke(root, 'diag', 'clear', '--plan', root / 'clear.json',
                                       '--accept-plan', plan['data']['plan_hash'], '--vehicle-stationary')
            self.assertEqual(code, 0, result)
            self.assertTrue(result['data']['all_faults_absent'])
