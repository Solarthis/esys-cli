"""Read-only access to externally supplied datasets. No vendor data is bundled."""
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

from .core import CliError, sha256


class Dataset:
    def __init__(self, data_root):
        self.root = (Path(data_root).resolve() / 'psdzdata').resolve()
        if not self.root.is_dir():
            raise CliError(f'External psdzdata directory is missing: {self.root}')

    def projects(self):
        base = self.root / 'mainseries'
        if not base.is_dir():
            raise CliError(f'PSdZ mainseries directory is missing: {base}')
        return [{'series': series.name, 'project': project.name, 'path': str(project)}
                for series in sorted(base.iterdir()) if series.is_dir()
                for project in sorted(series.iterdir()) if project.is_dir()]

    def has_project(self, series, project):
        return any(item['series'] == series and item['project'] == project for item in self.projects())

    def payload_paths(self, item):
        pc, ident, version = item['process_class'], item['id'], item['version']
        if pc not in ('BTLD', 'SWFL', 'CAFD') or not re.fullmatch(r'[0-9A-F]{8}', ident) or not re.fullmatch(r'\d{3}_\d{3}_\d{3}', version):
            raise CliError('Invalid dataset software reference')
        base = self.root / 'swe' / pc.lower()
        stem = pc.lower() + '_' + ident.lower()
        extensions = ['caf'] if pc == 'CAFD' else ['bin', 'xml', 'bsw']
        paths = [base / f'{stem}.{extension}.{version}' for extension in extensions]
        for index, path in enumerate(paths):
            if not path.is_file() or (index == 0 and path.stat().st_size == 0):
                raise CliError(f'Required dataset payload or metadata is missing: {path}')
        return paths

    def inventory(self):
        result, folded = {}, set()
        def fail(error):
            raise error
        for directory, dirs, files in os.walk(self.root, followlinks=False, onerror=fail):
            for name in sorted(dirs + files):
                path = Path(directory) / name
                info = path.lstat()
                # Windows WOF-compressed files can be reparse points; only directory
                # reparse points and actual symlinks redirect traversal outside the tree.
                directory_link = stat.S_ISDIR(info.st_mode) and getattr(info, 'st_file_attributes', 0) & 0x400
                if path.is_symlink() or directory_link:
                    raise CliError(f'Dataset links/junctions are unsupported: {path}')
                if name in dirs:
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise CliError(f'Dataset contains a non-regular file: {path}')
                relative = path.relative_to(self.root).as_posix()
                validate_path(relative)
                if relative.casefold() in folded:
                    raise CliError('Dataset paths collide on case-insensitive filesystems')
                folded.add(relative.casefold())
                result[relative] = (info.st_size, info.st_mtime_ns, info.st_ino, info.st_dev)
        return dict(sorted(result.items()))

    def records(self):
        before = self.inventory()
        if not before:
            raise CliError('Cannot fingerprint an empty dataset')
        records = [{'path': path, 'size': info[0], 'sha256': sha256(self.root / path)}
                   for path, info in before.items()]
        if self.inventory() != before:
            raise CliError('Dataset changed during hashing; no stable manifest was produced')
        return records

    def inspect(self):
        inventory = self.inventory()
        return {'provider': 'external-filesystem', 'root': str(self.root),
                'files': len(inventory), 'logical_bytes': sum(info[0] for info in inventory.values()),
                'projects': self.projects() if (self.root / 'mainseries').is_dir() else [],
                'hashes_verified': False}

    def manifest(self, output):
        output = Path(output).resolve()
        if output.exists():
            raise CliError('Manifest output already exists; choose a new filename')
        if output.is_relative_to(self.root):
            raise CliError('Manifest output must be outside the dataset')
        records = self.records()
        document = {'schema_version': 1, 'algorithm': 'sha256', 'files': records}
        output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation preserves any evidence created by a concurrent caller.
        with output.open('x', encoding='utf-8') as stream:
            json.dump(document, stream, indent=2)
            stream.write('\n')
        return {'manifest': str(output), 'files': len(records),
                'logical_bytes': sum(item['size'] for item in records), 'complete': True}

    def verify(self, manifest):
        document = json.loads(Path(manifest).read_text(encoding='utf-8'))
        if not isinstance(document, dict) or set(document) != {'schema_version', 'algorithm', 'files'} or type(document['schema_version']) is not int or document['schema_version'] != 1 or document['algorithm'] != 'sha256' or not isinstance(document['files'], list) or not document['files']:
            raise CliError('Invalid dataset manifest schema')
        expected, folded = {}, set()
        for item in document['files']:
            if not isinstance(item, dict) or set(item) != {'path', 'size', 'sha256'}:
                raise CliError('Invalid manifest record')
            path = item['path']
            validate_path(path)
            if path.casefold() in folded or type(item['size']) is not int or item['size'] < 0 or not isinstance(item['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', item['sha256']):
                raise CliError('Invalid or duplicate manifest record')
            folded.add(path.casefold())
            expected[path] = item
        actual = {item['path']: item for item in self.records()}
        missing, extra = sorted(expected.keys() - actual.keys()), sorted(actual.keys() - expected.keys())
        changed = sorted(path for path in expected.keys() & actual.keys() if expected[path] != actual[path])
        verified = not (missing or extra or changed)
        result = {'verified': verified, 'files': len(actual),
                  'missing_count': len(missing), 'extra_count': len(extra), 'changed_count': len(changed),
                  'missing': missing[:50], 'extra': extra[:50], 'changed': changed[:50]}
        if not verified:
            raise CliError('Dataset does not match the manifest', details=result)
        return result


def validate_path(value):
    if not isinstance(value, str) or not value or re.search(r'[\\:<>"|?*\x00-\x1f]', value):
        raise CliError('Invalid manifest relative path')
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(part in ('', '.', '..') or part.endswith((' ', '.')) for part in value.split('/')):
        raise CliError('Invalid manifest relative path')
