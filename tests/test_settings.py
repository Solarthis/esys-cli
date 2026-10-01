import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from esys_cli.cli import parser
from esys_cli.native import NativeRunner


class SettingsTests(unittest.TestCase):
    def test_environment_and_explicit_roots(self):
        with patch.dict(os.environ, {'ESYS_DATA_ROOT': 'external-data', 'ESYS_ROOT': 'external-esys',
                                     'ESYS_JOBS_ROOT': 'private-jobs'}):
            args = parser().parse_args(['capabilities'])
            self.assertEqual(args.data_root, 'external-data')
            self.assertEqual(args.esys_root, 'external-esys')
            self.assertEqual(args.jobs_root, 'private-jobs')
            self.assertEqual(parser().parse_args(['--data-root', 'explicit', 'capabilities']).data_root, 'explicit')

    def test_defaults_use_user_state_and_share_locks_across_installs(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'LOCALAPPDATA': directory, 'XDG_STATE_HOME': directory}):
            env = {key: value for key, value in os.environ.items() if key != 'ESYS_JOBS_ROOT'}
            with patch.dict(os.environ, env, clear=True):
                jobs = Path(parser().parse_args(['capabilities']).jobs_root)
                self.assertTrue(jobs.is_relative_to(Path(directory).resolve()))
                a = NativeRunner(Path(directory) / 'install-a', directory, jobs)
                b = NativeRunner(Path(directory) / 'install-b', directory, Path(directory) / 'other-jobs')
                self.assertEqual(a.lock_root, b.lock_root)
                self.assertTrue(a.lock_root.is_relative_to(Path(directory).resolve()))
