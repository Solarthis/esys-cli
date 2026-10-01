import importlib.util
from pathlib import Path
import tempfile
import unittest


class ReleaseGuardTests(unittest.TestCase):
    def test_public_source_allowed_but_vendor_files_and_secrets_blocked(self):
        script = Path(__file__).resolve().parents[1] / 'tools/check_release.py'
        self.assertTrue(script.exists(), 'Release content guard is missing')
        spec = importlib.util.spec_from_file_location('release_guard', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'README.md').write_text('Original documentation under MIT.')
            self.assertEqual(module.audit_sources(root, ['README.md']), [])
            for name, content in [('payload.bin', 'synthetic'), ('capture.xml', '<private/>'),
                                  ('README.md', 'ghp_' + 'x' * 40),
                                  ('README.md', 'WBA' + '12345678901234')]:
                (root / name).write_text(content)
                self.assertTrue(module.audit_sources(root, [name]), name)
