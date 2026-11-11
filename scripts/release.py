#!/usr/bin/env python3
"""Local version/release tooling. Never commits, tags, pushes or deploys."""
import argparse
import hashlib
import io
import json
import re
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_RE = re.compile(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-dev\.(0|[1-9]\d*))?\Z')


def validate_version(version):
    if not VERSION_RE.fullmatch(version):
        raise ValueError('Use MAJOR.MINOR.PATCH or MAJOR.MINOR.PATCH-dev.N.')
    return version


def current_version(root=ROOT):
    return validate_version((Path(root) / 'VERSION').read_text(encoding='utf-8').strip())


def generated(version):
    validate_version(version)
    return {
        'app/_version.py': f'# Generated from VERSION by scripts/release.py sync.\n__version__ = {version!r}\n',
        'app/static/js/version.js': f'// Generated from VERSION by scripts/release.py sync.\nwindow.ProxyPermitVersion = {json.dumps(version)};\n',
    }


def sync(root=ROOT, check=False):
    root = Path(root)
    version = current_version(root)
    for name, content in generated(version).items():
        path = root / name
        if check:
            if not path.exists() or path.read_text(encoding='utf-8') != content:
                raise ValueError(f'{name} is stale. Run python scripts/release.py sync.')
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
    return version


def prepare(version, root=ROOT, date=None, dry_run=False):
    root = Path(root)
    validate_version(version)
    current = current_version(root)
    if '-dev.' in version:
        raise ValueError('A release requires MAJOR.MINOR.PATCH.')
    before = tuple(map(int, current.split('-')[0].split('.')))
    after = tuple(map(int, version.split('.')))
    if after < before or (after == before and '-dev.' not in current):
        raise ValueError('A release must be newer than the current version.')
    changelog = (root / 'CHANGELOG.md').read_text(encoding='utf-8')
    marker = '## [Unreleased]\n'
    if marker not in changelog:
        raise ValueError('CHANGELOG.md is missing ## [Unreleased].')
    start = changelog.index(marker) + len(marker)
    following = re.search(r'^## ', changelog[start:], re.M)
    end = start + following.start() if following else len(changelog)
    notes = changelog[start:end].strip()
    if not re.search(r'^[-*] \S', notes, re.M):
        raise ValueError('Add actual change notes under Unreleased before preparing a release. No files changed.')
    if re.search(r'^## \[' + re.escape(version) + r'\]', changelog, re.M):
        raise ValueError('This release already exists in the changelog.')
    date = date or datetime.now(timezone.utc).date().isoformat()
    datetime.strptime(date, '%Y-%m-%d')
    updates = {'VERSION': version + '\n', **generated(version),
               'CHANGELOG.md': changelog[:start] + f'\n## [{version}] - {date}\n\n{notes}\n\n' + changelog[end:]}
    if dry_run:
        return list(updates)
    # Validate everything before mutation; restore all files on an I/O failure.
    originals = {name: (root / name).read_bytes() if (root / name).exists() else None for name in updates}
    try:
        for name, content in updates.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
    except OSError:
        for name, content in originals.items():
            path = root / name
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
        raise
    return list(updates)


def archive(root=ROOT, output=None):
    root = Path(root)
    version = sync(root, check=True)
    output = Path(output) if output else root / 'dist'
    output.mkdir(parents=True, exist_ok=True)
    name = f'proxypermit-{version}'
    target = output / (name + '.tar.gz')
    roots = ('app', 'deploy', 'docs', 'legacy', 'scripts', 'tests', 'requirements')
    files = [root / item for item in ('VERSION', 'CHANGELOG.md', 'README.md', 'LICENSE', 'Makefile',
                                     'install.sh', 'wsgi.py', '.env.example', 'pyproject.toml', 'AGENTS.md')]
    for directory in roots:
        files.extend((root / directory).rglob('*'))
    records = {}
    with tarfile.open(target, 'w:gz') as bundle:
        for path in sorted(files):
            relative = path.relative_to(root)
            if not path.is_file() or path.is_symlink() or '__pycache__' in relative.parts or path.suffix in ('.pyc', '.log', '.key', '.pem', '.crt'):
                continue
            if any(part in ('.test-data', '.demo', '.venv', '.cache', 'node_modules', 'backups',
                            'manual_backups', '.backups', '.proxypermit-config-backups') for part in relative.parts):
                continue
            if '.log.' in path.name or path.suffix in ('.jsonl', '.journal') or path.name.endswith('.journal~'):
                continue
            if path.match('ha-*.json') or path.name in ('session-state.json', 'nonce-store.json', 'nonces.json',
                    'sync-state.json', 'manual-sync.json', 'whitelist_entry_log.json'):
                continue
            if path.name.startswith('.env') and path.name != '.env.example':
                continue
            if path.name in ('accounts.json', 'totp-steps.json', 'login-attempts.json') or path.match('enroll-*.json'):
                continue
            if 'generated' in path.name:
                continue
            records[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
            bundle.add(path, arcname=f'{name}/{relative.as_posix()}', recursive=False)
        content = (json.dumps({'version': version, 'sha256': records}, indent=2) + '\n').encode()
        info = tarfile.TarInfo(name + '/release-manifest.json')
        info.size, info.mode = len(content), 0o644
        bundle.addfile(info, io.BytesIO(content))
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(target.suffix + '.sha256').write_text(f'{checksum}  {target.name}\n', encoding='ascii')
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('show')
    commands.add_parser('sync')
    check = commands.add_parser('check')
    check.add_argument('--tag', help='Also require an exact vVERSION Git tag.')
    release = commands.add_parser('prepare')
    release.add_argument('version')
    release.add_argument('--dry-run', action='store_true')
    package = commands.add_parser('archive')
    package.add_argument('--output')
    args = parser.parse_args(argv)
    try:
        if args.command == 'show':
            print(current_version())
        elif args.command in ('sync', 'check'):
            version = sync(check=args.command == 'check')
            if args.command == 'check' and args.tag and args.tag != 'v' + version:
                raise ValueError('Git tag must equal v' + version)
            if args.command == 'check' and args.tag and '-dev.' not in version:
                changelog = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
                if not re.search(r'^## \[' + re.escape(version) + r'\] - \d{4}-\d{2}-\d{2}$', changelog, re.M):
                    raise ValueError('A stable release tag requires dated release notes. Run the release preparation first.')
            print('Version synchronized: ' + version)
        elif args.command == 'prepare':
            print(json.dumps({'version': args.version, 'files': prepare(args.version, dry_run=args.dry_run), 'dryRun': args.dry_run}))
        else:
            print(archive(output=args.output))
        return 0
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
