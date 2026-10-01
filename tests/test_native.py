from pathlib import Path
import sys
import tempfile
import unittest


class NativeTests(unittest.TestCase):
    def test_vendor_classpath_and_literal_arguments(self):
        NativeRunner, _ = self.contracts()
        from esys_cli.core import CliError
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root/'jre/bin').mkdir(parents=True)
            (root/'jre/bin/java.exe').touch()
            batch = root/'E-Sys.bat'
            batch.write_text('set CLASSPATH=%CLASSPATH%;%SP%/esysCore.jar\nset CLASSPATH=%CLASSPATH%;%SP%/environment\n')
            runner = NativeRunner(root, root/'data', root/'jobs')
            job = runner.new_job('probe')
            literal = 'a path with spaces & delimiters'
            argv = runner.argv(job, ['-out', literal])
            self.assertIn(literal, argv)
            self.assertEqual(argv[argv.index('-cp') + 1], str(root/'lib/esysCore.jar') + ';' + str(root/'lib/environment'))
            batch.write_text('set CLASSPATH=%CLASSPATH%;%SP%/../../outside.jar\n')
            with self.assertRaises(CliError):
                runner.argv(job, [])

    def test_runtime_preserves_valid_vendor_plant_identifier(self):
        NativeRunner, _ = self.contracts()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'config').mkdir()
            (root/'config/Esys.properties').write_text('PlantIDdec=1234\nTalExecution.doNotCheckSWE=true\n')
            runner = NativeRunner(root, root/'data', root/'jobs')
            runtime = (runner.new_job('probe') / 'runtime.properties').read_text()
            self.assertIn('PlantIDdec=1234\n', runtime)
            self.assertIn('TalExecution.doNotCheckSWE=false\n', runtime)

    def contracts(self):
        try:
            from esys_cli.native import NativeRunner, properties
            return NativeRunner, properties
        except ImportError:
            self.fail('native execution contracts are not implemented')

    def test_process_arguments_logs_and_runtime_directory(self):
        NativeRunner, properties = self.contracts()
        with tempfile.TemporaryDirectory() as directory:
            runner = NativeRunner(directory, directory, Path(directory) / 'jobs with spaces', 5)
            job = runner.new_job('probe')
            literal = 'value with spaces; & $() = "quotes"'
            result = runner.run_process(job, [sys.executable, '-c', 'import sys; print(sys.argv[1])', literal])
            self.assertEqual(Path(result['stdout']).read_text().strip(), literal)
            self.assertTrue((job / 'runtime' / 'data').is_dir())
            self.assertEqual(result['exit_code'], 0)
            self.assertEqual(properties({'key': 'a=b\nnext'}), 'key=a\\=b\\nnext\n')

    def test_timeout_and_session_collision(self):
        NativeRunner, _ = self.contracts()
        from esys_cli.core import CliError
        with tempfile.TemporaryDirectory() as directory:
            runner = NativeRunner(directory, directory, Path(directory) / 'jobs', 0.05, lock_root=Path(directory) / 'locks')
            job = runner.new_job('probe')
            with self.assertRaises(CliError) as failed:
                runner.run_process(job, [sys.executable, '-c', 'import time; time.sleep(10)'])
            self.assertEqual(failed.exception.code, 3)
            profile = {'expected_vin': 'WBA00000000000001'}
            with runner.session(profile, job):
                with self.assertRaises(CliError) as locked:
                    with runner.session(profile, job):
                        pass
                self.assertEqual(locked.exception.code, 5)
            with runner.session(profile, job):
                pass

    def test_timed_out_write_remains_running_and_retains_lock(self):
        NativeRunner, _ = self.contracts()
        from esys_cli.core import CliError
        import os
        import signal
        with tempfile.TemporaryDirectory() as directory:
            runner = NativeRunner(directory, directory, Path(directory) / 'jobs', 0.05, lock_root=Path(directory) / 'locks')
            job = runner.new_job('write')
            try:
                with runner.session({'expected_vin': 'WBA00000000000001'}, job) as session:
                    with self.assertRaises(CliError) as failed:
                        runner.run_process(job, [sys.executable, '-c', 'import time; time.sleep(10)'], mutating=True)
                    self.assertEqual(failed.exception.code, 4)
                    pid = failed.exception.details['pid']
                    self.assertIsNone(runner.processes[pid].poll())
                    session.retain()
                self.assertEqual(len(list(runner.lock_root.glob('*.lock'))), 1)
            finally:
                if 'pid' in locals():
                    os.kill(pid, signal.SIGTERM)
                    runner.processes[pid].wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
