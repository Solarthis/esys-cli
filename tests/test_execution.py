from pathlib import Path
import tempfile
import unittest
from fixtures import fake_runner, setup, tal


class ExecutionTests(unittest.TestCase):
    def test_edited_plan_hash_is_rejected(self):
        from esys_cli.core import CliError
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, data = setup(root)
            plan = self.make_plan(root, data)
            plan['allowed_ecus'] = ['0x30']
            _, verify_plan, _ = self.api()
            with self.assertRaises(CliError):
                verify_plan(plan)

    def test_staged_tal_change_during_preflight_never_attempts_write(self):
        from esys_cli.core import CliError, save_json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, data = setup(root)
            plan = self.make_plan(root, data)
            save_json(root/'plan.json', plan)
            runner = fake_runner(root, data)
            original = runner.run
            def changing_stage(job, arguments, mutating=False):
                result = original(job, arguments, mutating)
                if arguments[0] == '-readsvt' and not (job/'write-attempted.marker').exists():
                    (job/'inputs/tal.xml').write_text(tal('30'))
                return result
            runner.run = changing_stage
            _, _, Workflow = self.api()
            with self.assertRaises(CliError):
                Workflow(runner).execute(root/'plan.json', plan['plan_hash'], True)
            self.assertFalse(list((root/'jobs').glob('*/write-attempted.marker')))

    def test_keyboard_interrupt_during_write_retains_lock_and_unknown_outcome(self):
        from esys_cli.core import CliError, save_json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, data = setup(root)
            plan = self.make_plan(root, data)
            save_json(root/'plan.json', plan)
            runner = fake_runner(root, data)
            original = runner.run
            def interrupted(job, arguments, mutating=False):
                if mutating:
                    raise KeyboardInterrupt('operator interrupt')
                return original(job, arguments, mutating)
            runner.run = interrupted
            _, _, Workflow = self.api()
            try:
                Workflow(runner).execute(root/'plan.json', plan['plan_hash'], True)
            except CliError as failed:
                self.assertEqual(failed.code, 4)
            except KeyboardInterrupt:
                pass
            else:
                self.fail('Interrupted write was reported successful')
            self.assertEqual(len(list((root/'locks').glob('*.lock'))), 1)

    def test_failed_native_write_retains_lock(self):
        from esys_cli.core import CliError, save_json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, data = setup(root)
            plan = self.make_plan(root, data)
            save_json(root/'plan.json', plan)
            _, _, Workflow = self.api()
            with self.assertRaises(CliError) as failed:
                Workflow(fake_runner(root, data, fail_write=True)).execute(root/'plan.json', plan['plan_hash'], True)
            self.assertEqual(failed.exception.code, 4)
            self.assertEqual(len(list((root/'locks').glob('*.lock'))), 1)

    def test_wrong_live_pre_state_never_attempts_write(self):
        from esys_cli.core import CliError, save_json
        from fixtures import svt
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, data = setup(root)
            (root/'ist.xml').write_text(svt('003_000_000'))
            plan = self.make_plan(root, data)
            save_json(root/'plan.json', plan)
            _, _, Workflow = self.api()
            with self.assertRaises(CliError):
                Workflow(fake_runner(root, data)).execute(root/'plan.json', plan['plan_hash'], True)
            self.assertFalse(list((root/'jobs').glob('*/write-attempted.marker')))
            self.assertFalse(list((root/'locks').glob('*.lock')))

    def api(self):
        try:
            from esys_cli.core import create_plan, verify_plan
            from esys_cli.workflow import Workflow
        except ImportError:
            self.fail('hashed programming contracts are not implemented')
        return create_plan, verify_plan, Workflow

    def make_plan(self, root, data):
        create_plan, _, _ = self.api()
        return create_plan(root/'profile.json', root/'fa.xml', root/'ist.xml', root/'soll.xml', root/'tal.xml', ['0x29'], data)

    def test_foreign_scope_missing_payload_and_target_mismatch_are_rejected(self):
        from esys_cli.core import CliError
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, data = setup(root)
            (root/'tal.xml').write_text(tal('30'))
            with self.assertRaises(CliError):
                self.make_plan(root, data)
            (root/'tal.xml').write_text(tal(version='003_000_000'))
            with self.assertRaises(CliError):
                self.make_plan(root, data)
            (root/'tal.xml').write_text(tal())
            (data/'psdzdata/swe/swfl/swfl_00000c79.bin.002_000_000').unlink()
            with self.assertRaises(CliError):
                self.make_plan(root, data)

    def test_artifact_tampering_is_rejected(self):
        from esys_cli.core import CliError
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, data = setup(root)
            plan = self.make_plan(root, data)
            _, verify_plan, _ = self.api()
            (root/'tal.xml').write_text(tal('30'))
            with self.assertRaises(CliError):
                verify_plan(plan)

    def test_execution_requires_acknowledgement_then_verifies_post_state(self):
        from esys_cli.core import CliError, save_json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile, data = setup(root)
            plan = self.make_plan(root, data)
            save_json(root/'plan.json', plan)
            _, _, Workflow = self.api()
            workflow = Workflow(fake_runner(root, data))
            with self.assertRaises(CliError):
                workflow.execute(root/'plan.json', 'wrong', True)
            self.assertFalse(list((root/'jobs').glob('*/write-attempted.marker')))
            result = workflow.execute(root/'plan.json', plan['plan_hash'], True)
            self.assertEqual(result['verification'], 'scoped_svt_and_finished_tal')
            self.assertEqual(result['post']['ecus'][0]['parts'][1]['version'], '002_000_000')
            self.assertFalse(list((root/'locks').glob('*.lock')))

    def test_wrong_post_state_retains_unverified_write_lock(self):
        from esys_cli.core import CliError, save_json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, data = setup(root)
            plan = self.make_plan(root, data)
            save_json(root/'plan.json', plan)
            _, _, Workflow = self.api()
            with self.assertRaises(CliError) as failed:
                Workflow(fake_runner(root, data, post_version='001_000_000')).execute(root/'plan.json', plan['plan_hash'], True)
            self.assertEqual(failed.exception.code, 4)
            self.assertEqual(len(list((root/'locks').glob('*.lock'))), 1)

    def test_wrong_live_identity_never_attempts_write(self):
        from esys_cli.core import CliError, save_json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, data = setup(root)
            plan = self.make_plan(root, data)
            save_json(root/'plan.json', plan)
            _, _, Workflow = self.api()
            with self.assertRaises(CliError):
                Workflow(fake_runner(root, data, vin='WBA00000000000002')).execute(root/'plan.json', plan['plan_hash'], True)
            self.assertFalse(list((root/'jobs').glob('*/write-attempted.marker')))
            self.assertFalse(list((root/'locks').glob('*.lock')))


if __name__ == '__main__':
    unittest.main()
