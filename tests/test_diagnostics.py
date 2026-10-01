import json
from pathlib import Path
import tempfile
import time
import unittest

from esys_cli.core import CliError


class DiagnosticsTests(unittest.TestCase):
    def api(self, directory, **options):
        from esys_cli.diagnostics import Diagnostics
        from esys_cli.simulator import Simulator
        return Diagnostics(Path(directory) / 'jobs', timeout=0.5, lock_root=Path(directory) / 'locks'), Simulator(**options)

    def test_both_transports_plan_clear_and_preserve_original_faults(self):
        for protocol in ['hsfz', 'doip']:
            with self.subTest(protocol=protocol), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                workflow, simulator = self.api(root, protocol=protocol)
                with simulator:
                    planned = workflow.clear_plan(simulator.profile, root / 'plan.json')
                    self.assertEqual(planned['before']['0x29' if protocol == 'hsfz' else '0x1029']['dtcs'][0]['code'], '123456')
                    result = workflow.clear(root / 'plan.json', planned['plan_hash'], True)
                    self.assertTrue(result['clear_acknowledged'])
                    self.assertTrue(result['all_faults_absent'])
                    self.assertEqual(sum(payload == b'\x14\xff\xff\xff' for _, payload in simulator.requests), 1)
                    saved = json.loads((Path(result['job']) / 'job.json').read_text())
                    self.assertEqual(saved['state'], 'verified')
                    self.assertTrue((Path(result['job']) / 'before.json').exists())
                    self.assertIn('123456', (root / 'plan.json').read_text())
                    self.assertFalse(list((root / 'locks').glob('*.lock')))

    def test_changed_faults_or_vin_never_send_clear(self):
        for change in ['faults', 'vin']:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                workflow, simulator = self.api(root)
                with simulator:
                    planned = workflow.clear_plan(simulator.profile, root / 'plan.json')
                    if change == 'faults':
                        simulator.faults[0x29] = bytes.fromhex('65432109')
                    else:
                        simulator.vin = 'WBA00000000000002'
                    with self.assertRaises(CliError):
                        workflow.clear(root / 'plan.json', planned['plan_hash'], True)
                    self.assertFalse(any(payload[0] == 0x14 for _, payload in simulator.requests))

    def test_clear_requires_exact_hash_stationary_fresh_untampered_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow, simulator = self.api(root)
            with simulator:
                planned = workflow.clear_plan(simulator.profile, root / 'plan.json')
                for accepted, stationary in [('wrong', True), (planned['plan_hash'], False)]:
                    with self.assertRaises(CliError):
                        workflow.clear(root / 'plan.json', accepted, stationary)
                document = json.loads((root / 'plan.json').read_text())
                document['expires_at'] = time.time() - 1
                from esys_cli.core import plan_digest
                document['plan_hash'] = plan_digest(document)
                (root / 'plan.json').write_text(json.dumps(document))
                with self.assertRaises(CliError):
                    workflow.clear(root / 'plan.json', document['plan_hash'], True)
                self.assertFalse(any(payload[0] == 0x14 for _, payload in simulator.requests))

    def test_tampered_plan_and_invalid_request_timeout_never_connect(self):
        from esys_cli.diagnostics import Diagnostics
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow, simulator = self.api(root)
            with simulator:
                planned = workflow.clear_plan(simulator.profile, root / 'plan.json')
                count = len(simulator.requests)
                document = json.loads((root / 'plan.json').read_text())
                document['profile']['ecus'] = [0x30]
                (root / 'plan.json').write_text(json.dumps(document))
                with self.assertRaises(CliError):
                    workflow.clear(root / 'plan.json', planned['plan_hash'], True)
                self.assertEqual(len(simulator.requests), count)
            for timeout in [0, -1, float('nan'), float('inf'), 121]:
                with self.subTest(timeout=timeout), self.assertRaises(CliError):
                    Diagnostics(root / 'jobs', timeout)

    def test_interruption_at_write_boundary_retains_lock(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow, simulator = self.api(root)
            with simulator:
                planned = workflow.clear_plan(simulator.profile, root / 'plan.json')
                with patch('esys_cli.diagnostics.uds.clear_faults', side_effect=KeyboardInterrupt):
                    with self.assertRaises(CliError) as interrupted:
                        workflow.clear(root / 'plan.json', planned['plan_hash'], True)
                self.assertEqual(interrupted.exception.code, 4)
                self.assertTrue(list((root / 'locks').glob('*.lock')))

    def test_post_clear_transport_and_evidence_failures_preserve_unknown_outcome(self):
        from unittest.mock import patch
        from esys_cli.diagnostic_transport import Transport
        from esys_cli.diagnostics import Job
        for failure in ['after_send', 'save_after']:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                workflow, simulator = self.api(root, ecus=[0x29, 0x30])
                with simulator:
                    plan = workflow.clear_plan(simulator.profile, root / 'plan.json')
                    if failure == 'after_send':
                        original = Transport.send_frame
                        def injected(instance, kind, body, deadline):
                            original(instance, kind, body, deadline)
                            if body.endswith(bytes.fromhex('14ffffff')):
                                raise OSError('simulated disconnect after send')
                        target, method = Transport, 'send_frame'
                    else:
                        original = Job.update
                        def injected(instance, **values):
                            if 'after' in values:
                                raise OSError('simulated evidence write failure')
                            return original(instance, **values)
                        target, method = Job, 'update'
                    with patch.object(target, method, injected):
                        with self.assertRaises(CliError) as failed:
                            workflow.clear(root / 'plan.json', plan['plan_hash'], True)
                    self.assertEqual(failed.exception.code, 4)
                    evidence = Path(failed.exception.details['job'])
                    self.assertTrue((evidence / 'before.json').exists())
                    self.assertTrue(list((root / 'locks').glob('*.lock')))
                    self.assertFalse(any(ecu == 0x30 and payload[0] == 0x14 for ecu, payload in simulator.requests))

    def test_uncertain_write_retains_lock_and_stops_later_ecus(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow, simulator = self.api(root, drop_after_clear=True, ecus=[0x29, 0x30])
            with simulator:
                planned = workflow.clear_plan(simulator.profile, root / 'plan.json')
                with self.assertRaises(CliError) as raised:
                    workflow.clear(root / 'plan.json', planned['plan_hash'], True)
                self.assertEqual(raised.exception.code, 4)
                self.assertEqual(sum(payload[0] == 0x14 for _, payload in simulator.requests), 1)
                self.assertEqual(len(list((root / 'locks').glob('*.lock'))), 1)
                with self.assertRaises(CliError) as locked:
                    workflow.faults(simulator.profile)
                self.assertEqual(locked.exception.code, 5)

    def test_persistent_faults_are_reported_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow, simulator = self.api(root, persistent=True)
            with simulator:
                planned = workflow.clear_plan(simulator.profile, root / 'plan.json')
                result = workflow.clear(root / 'plan.json', planned['plan_hash'], True)
                self.assertFalse(result['all_faults_absent'])
                self.assertEqual(result['remaining_fault_count'], 1)
                self.assertEqual(sum(payload[0] == 0x14 for _, payload in simulator.requests), 1)

    def test_incomplete_tests_status_does_not_mean_fault_remains(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow, simulator = self.api(root, post_clear_records=bytes.fromhex('12345650'))
            with simulator:
                plan = workflow.clear_plan(simulator.profile, root / 'plan.json')
                result = workflow.clear(root / 'plan.json', plan['plan_hash'], True)
                self.assertTrue(result['all_faults_absent'])
                self.assertEqual(result['after']['0x29']['dtcs'][0]['status'], 0x50)

    def test_independent_and_native_sessions_share_vehicle_lock(self):
        from esys_cli.native import NativeRunner
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow, simulator = self.api(root)
            runner = NativeRunner(root / 'no-esys', root / 'no-data', root / 'native-jobs', lock_root=root / 'locks')
            native_job = runner.new_job('synthetic-read')
            with simulator, runner.session({'expected_vin': simulator.profile['expected_vin']}, native_job):
                with self.assertRaises(CliError) as locked:
                    workflow.faults(simulator.profile)
                self.assertEqual(locked.exception.code, 5)
                self.assertFalse(simulator.requests)

    def test_profiles_reject_broadcast_and_foreign_ecu_scope(self):
        from esys_cli.diagnostics import validate_profile
        with tempfile.TemporaryDirectory() as directory:
            workflow, simulator = self.api(directory)
            self.addCleanup(simulator.listener.close)
            for field, value in [('host', '255.255.255.255'), ('host', '0.0.0.0'),
                                 ('ecus', [0xDF]), ('ecus', [0x29, 0x29]),
                                 ('source', 0x29), ('port', True)]:
                candidate = {**simulator.profile, field: value}
                with self.subTest(field=field), self.assertRaises(CliError):
                    validate_profile(candidate)
            with self.assertRaises(CliError):
                workflow.read_did(simulator.profile, 0x30, 0xF190)


if __name__ == '__main__':
    unittest.main()
