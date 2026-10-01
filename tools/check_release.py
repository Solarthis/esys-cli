"""Heuristic release checks; not a substitute for provenance/license review."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import zipfile


ROOT_FILES = {'.gitignore', 'AGENTS.md', 'CONTRIBUTING.md', 'LICENSE', 'README.md',
              'SECURITY.md', 'THIRD_PARTY.md', 'esys.ps1', 'pyproject.toml', '.github/workflows/ci.yml'}


def content_issues(name, content):
    problems = []
    patterns = [r'gh[pousr]_[A-Za-z0-9]{20,}', r'github_pat_[A-Za-z0-9_]{20,}',
                r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
                r'(?i)C:[/\\]Users[/\\][^\s]+']
    if any(re.search(pattern, content) for pattern in patterns):
        problems.append({'file': name, 'reason': 'possible credential, private key or personal path'})
    for identifier in re.findall(r'\b[A-HJ-NPR-Z0-9]{17}\b', content):
        if identifier not in ('WBA00000000000001', 'WBA00000000000002'):
            problems.append({'file': name, 'reason': 'possible real vehicle identifier'})
    return problems


def audit_sources(root, files):
    problems = []
    for name in files:
        allowed = name in ROOT_FILES or re.fullmatch(r'(esys_cli|tests|tools)/[a-z_][a-z0-9_]*\.py', name) or re.fullmatch(r'docs/[a-z0-9_-]+\.md', name)
        path = root / name
        if not allowed or path.is_symlink() or path.stat().st_size > 500_000:
            problems.append({'file': name, 'reason': 'not an allowed public source artifact'})
            continue
        try:
            problems.extend(content_issues(name, path.read_text(encoding='utf-8')))
        except UnicodeError:
            problems.append({'file': name, 'reason': 'binary or invalid UTF-8 source'})
    return problems


def audit_wheel(path):
    problems = []
    with zipfile.ZipFile(path) as archive:
        for item in archive.infolist():
            name = item.filename
            allowed = re.fullmatch(r'esys_cli/[a-z_][a-z0-9_]*\.py', name) or re.fullmatch(r'esys_agent_cli-[0-9.]+\.dist-info/(METADATA|WHEEL|RECORD|entry_points\.txt|top_level\.txt|licenses/(LICENSE|THIRD_PARTY\.md))', name)
            if not allowed or item.file_size > 500_000:
                problems.append({'file': name, 'reason': 'unexpected wheel member'})
                continue
            problems.extend(content_issues(name, archive.read(item).decode('utf-8')))
    return problems


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--wheel', type=Path)
    parser.add_argument('--wheel-dir', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    files = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z']).decode('utf-8').strip('\0').split('\0')
    issues = audit_sources(root, files)
    if args.wheel:
        issues.extend(audit_wheel(args.wheel))
    if args.wheel_dir:
        wheels = list(args.wheel_dir.glob('*.whl'))
        if not wheels:
            issues.append({'reason': 'no wheel found in requested directory'})
        for wheel in wheels:
            issues.extend(audit_wheel(wheel))
    print(json.dumps({'files': len(files), 'issues': issues, 'passed': not issues}))
    return bool(issues)


if __name__ == '__main__':
    raise SystemExit(main())
