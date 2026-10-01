import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from esys_cli.cli import main


class DatasetTests(unittest.TestCase):
    def invoke(self, root, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(['--data-root', str(root), 'dataset', *map(str, args)])
        return code, json.loads(output.getvalue())

    def fixture(self, directory):
        root = Path(directory) / 'data'
        dataset = root / 'psdzdata'
        (dataset / 'swe/example').mkdir(parents=True)
        (dataset / 'swe/example/one.txt').write_bytes(b'abc')
        (dataset / 'swe/example/two.txt').write_bytes(b'')
        return root, dataset, Path(directory) / 'manifest.json'

    def test_portable_manifest_and_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root, dataset, manifest = self.fixture(directory)
            code, result = self.invoke(root, 'inspect')
            self.assertEqual(code, 0, result)
            self.assertEqual(result['data']['files'], 2)
            code, result = self.invoke(root, 'manifest', '--output', manifest)
            self.assertEqual(code, 0, result)
            saved = manifest.read_text(encoding='utf-8')
            self.assertNotIn(str(root), saved)
            first = json.loads(saved)['files'][0]
            self.assertEqual(first, {'path': 'swe/example/one.txt', 'size': 3,
                'sha256': 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'})
            relocated = root.with_name('relocated')
            root.rename(relocated)
            code, result = self.invoke(relocated, 'verify', '--manifest', manifest)
            self.assertEqual(code, 0, result)
            self.assertTrue(result['data']['verified'])

    def test_changes_missing_and_extra_files_fail_verification(self):
        for change in ['content', 'missing', 'extra']:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root, dataset, manifest = self.fixture(directory)
                code, result = self.invoke(root, 'manifest', '--output', manifest)
                self.assertEqual(code, 0, result)
                if change == 'content':
                    (dataset / 'swe/example/one.txt').write_bytes(b'xyz')
                elif change == 'missing':
                    (dataset / 'swe/example/one.txt').unlink()
                else:
                    (dataset / 'extra.txt').write_bytes(b'extra')
                code, result = self.invoke(root, 'verify', '--manifest', manifest)
                self.assertEqual(code, 2)
                self.assertFalse(result['details']['verified'])

    def test_existing_evidence_and_dataset_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root, dataset, manifest = self.fixture(directory)
            manifest.write_text('previous evidence')
            code, result = self.invoke(root, 'manifest', '--output', manifest)
            self.assertEqual(code, 2)
            self.assertIn('exists', result['error'])
            self.assertEqual(manifest.read_text(), 'previous evidence')
            code, result = self.invoke(root, 'manifest', '--output', dataset / 'new.json')
            self.assertEqual(code, 2)
            self.assertFalse((dataset / 'new.json').exists())

    def test_untrusted_manifest_paths_duplicates_and_types_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root, dataset, manifest = self.fixture(directory)
            code, result = self.invoke(root, 'manifest', '--output', manifest)
            self.assertEqual(code, 0, result)
            original = json.loads(manifest.read_text())
            for invalid in ['../escape', '/absolute', 'C:/escape', 'swe\\escape', 'swe/../escape', 'swe/file:stream']:
                document = json.loads(json.dumps(original))
                document['files'][0]['path'] = invalid
                manifest.write_text(json.dumps(document))
                code, result = self.invoke(root, 'verify', '--manifest', manifest)
                self.assertEqual(code, 2, invalid)
                self.assertIn('manifest', result['error'].lower())
            original['files'].append(original['files'][0])
            manifest.write_text(json.dumps(original))
            self.assertEqual(self.invoke(root, 'verify', '--manifest', manifest)[0], 2)
            original['files'] = [{'path': 'x', 'size': True, 'sha256': '0' * 64}]
            manifest.write_text(json.dumps(original))
            self.assertEqual(self.invoke(root, 'verify', '--manifest', manifest)[0], 2)

    def test_empty_dataset_and_link_escape_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'data'
            dataset = root / 'psdzdata'
            dataset.mkdir(parents=True)
            self.assertEqual(self.invoke(root, 'manifest', '--output', root / 'manifest.json')[0], 2)
            target = Path(directory) / 'outside.txt'
            target.write_text('private content')
            try:
                (dataset / 'link.txt').symlink_to(target)
            except OSError:
                self.skipTest('Creating symlinks is unavailable on this host')
            code, result = self.invoke(root, 'manifest', '--output', root / 'manifest.json')
            self.assertEqual(code, 2)
            self.assertIn('link', result['error'].lower())

    def test_change_during_hashing_does_not_create_completed_evidence(self):
        from esys_cli.core import sha256
        with tempfile.TemporaryDirectory() as directory:
            root, dataset, manifest = self.fixture(directory)
            def changing_hash(path):
                digest = sha256(path)
                (dataset / 'extra.txt').write_bytes(b'concurrent addition')
                return digest
            with patch('esys_cli.datasets.sha256', side_effect=changing_hash):
                code, result = self.invoke(root, 'manifest', '--output', manifest)
            self.assertEqual(code, 2)
            self.assertIn('changed', result['error'])
            self.assertFalse(manifest.exists())


if __name__ == '__main__':
    unittest.main()
