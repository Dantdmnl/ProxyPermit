#!/usr/bin/env python3
"""Version, packaging and CI-gated GitHub release tools. Never moves Git refs."""
import argparse
import hashlib
import io
import json
import re
import sys
import tarfile
import os
import subprocess
import tempfile
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
                                     'install.sh', 'wsgi.py', '.env.example', 'pyproject.toml', 'AGENTS.md', 'zensical.toml')]
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


def release_notes(version, root=ROOT):
    validate_version(version)
    text = (Path(root) / 'CHANGELOG.md').read_text(encoding='utf-8')
    section = re.search(r'^## \[' + re.escape(version) + r'\] - (\d{4}-\d{2}-\d{2})$', text, re.M)
    if not section:
        raise ValueError('A release requires a dated changelog section.')
    datetime.strptime(section[1], '%Y-%m-%d')
    following = re.search(r'^## ', text[section.end():], re.M)
    end = section.end() + following.start() if following else len(text)
    notes = text[section.end():end].strip()
    if not re.search(r'^[-*] \S', notes, re.M):
        raise ValueError('The release notes are empty.')
    return notes + '\n'


def validate_release_run(run, tag, commit, repository, jobs=()):
    expected = dict(event='push', head_branch=tag,
                    head_sha=commit, path='.github/workflows/ci.yml', name='ProxyPermit CI')
    completed = run.get('status') == 'completed' and run.get('conclusion') == 'success'
    # During automatic publication this workflow is still running. The archive
    # job depends on every verification job, so its success is the publication gate.
    verified = (run.get('status') == 'in_progress' and run.get('conclusion') is None
                and any(job.get('name') == 'Release archive & tag validation'
                        and job.get('status') == 'completed' and job.get('conclusion') == 'success' for job in jobs))
    if (not (completed or verified) or any(run.get(key) != value for key, value in expected.items())
            or (run.get('head_repository') or {}).get('full_name') != repository):
        raise ValueError('Publication requires a successful ProxyPermit CI tag run on the exact release commit.')


def verify_release_artifact(directory, version, changelog):
    directory = Path(directory)
    filename = f'proxypermit-{version}.tar.gz'
    bundle = directory / filename
    checksum = directory / (filename + '.sha256')
    if {path.name for path in directory.iterdir()} != {bundle.name, checksum.name}:
        raise ValueError('The CI artifact must contain exactly the source archive and checksum.')
    expected = checksum.read_text(encoding='ascii').strip()
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    if expected != f'{digest}  {filename}':
        raise ValueError('The CI archive checksum does not match.')
    prefix = f'proxypermit-{version}/'
    with tarfile.open(bundle, 'r:gz') as package:
        members = package.getmembers()
        if (any(not member.isfile() or member.size > 8 * 1024 * 1024 for member in members)
                or sum(member.size for member in members) > 64 * 1024 * 1024
                or len({member.name for member in members}) != len(members)):
            raise ValueError('The CI archive contains unsafe or oversized members.')
        manifest = json.load(package.extractfile(prefix + 'release-manifest.json'))
        records = manifest.get('sha256', {})
        if (manifest.get('version') != version or not isinstance(records, dict)
                or {member.name for member in members} != {prefix + name for name in records} | {prefix + 'release-manifest.json'}):
            raise ValueError('The CI archive manifest does not match the release.')
        for name, value in records.items():
            if (name.startswith('/') or '..' in Path(name).parts
                    or hashlib.sha256(package.extractfile(prefix + name).read()).hexdigest() != value):
                raise ValueError('The CI archive file hashes do not match.')
        if (package.extractfile(prefix + 'VERSION').read().replace(b'\r\n', b'\n') != (version + '\n').encode()
                or package.extractfile(prefix + 'CHANGELOG.md').read() != changelog):
            raise ValueError('The CI archive metadata differs from the release tag.')
    return bundle, checksum


def publish(tag, run_id, repository, dry_run=False):
    if (not re.fullmatch(r'v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)', tag)
            or not re.fullmatch(r'[1-9]\d*', run_id)
            or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository)):
        raise ValueError('Choose a stable vMAJOR.MINOR.PATCH tag, a CI run ID and an owner/repository.')
    version = tag[1:]
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=ROOT)
    def github(*args):
        return json.loads(subprocess.check_output(['gh', 'api', *args], cwd=ROOT))
    commit = git('rev-parse', f'refs/tags/{tag}^{{commit}}').decode().strip()
    tag_object = git('rev-parse', f'refs/tags/{tag}').decode().strip()
    if git('show', f'{tag}:VERSION').decode().strip() != version:
        raise ValueError('The tag does not match VERSION.')
    changelog = git('show', f'{tag}:CHANGELOG.md')
    run = github(f'repos/{repository}/actions/runs/{run_id}')
    jobs = github(f'repos/{repository}/actions/runs/{run_id}/jobs?per_page=100').get('jobs', []) if run.get('status') == 'in_progress' else []
    validate_release_run(run, tag, commit, repository, jobs)
    with tempfile.TemporaryDirectory(prefix='proxypermit-release-') as temporary:
        work = Path(temporary)
        (work / 'CHANGELOG.md').write_bytes(changelog)
        notes = release_notes(version, work)
        notes += '\n[English documentation](https://dantdmnl.github.io/ProxyPermit/en/) · [Nederlandse documentatie](https://dantdmnl.github.io/ProxyPermit/nl/)\n'
        notes += '\nFor an existing installation, review the upgrade plan first. Pass your existing `--install-dir` if it differs from the default.\n'
        notes += '\n```sh\nsudo bash install.sh --upgrade --plan\nsudo bash install.sh --upgrade --apply\n```\n'
        output = work / 'assets'
        subprocess.run(['gh', 'run', 'download', run_id, '--repo', repository, '--name',
                        'proxypermit-source-release', '--dir', str(output)], check=True, cwd=ROOT)
        bundle, checksum = verify_release_artifact(output, version, changelog)
        remote = github(f'repos/{repository}/git/ref/tags/{tag}')
        if remote.get('object', {}).get('sha') != tag_object:
            raise ValueError('The remote tag changed; refusing publication.')
        if dry_run:
            print(f'Validated {tag}: tested commit {commit}, CI run {run_id}, archive and notes.')
            return
        existing = subprocess.run(['gh', 'release', 'view', tag, '--repo', repository],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if existing.returncode == 0:
            print(f'Release {tag} already exists; leaving its notes and assets unchanged.')
            return
        note_file = work / 'notes.md'
        note_file.write_text(notes, encoding='utf-8')
        subprocess.run(['gh', 'release', 'create', tag, str(bundle), str(checksum), '--repo', repository,
                        '--verify-tag', '--title', f'ProxyPermit {tag}', '--notes-file', str(note_file)], check=True, cwd=ROOT)


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
    publishing = commands.add_parser('publish', help='Publish verified assets from a successful tag CI run.')
    publishing.add_argument('--tag', required=True)
    publishing.add_argument('--run-id', required=True)
    publishing.add_argument('--repo', default=os.getenv('GITHUB_REPOSITORY', ''))
    publishing.add_argument('--dry-run', action='store_true')
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
        elif args.command == 'publish':
            publish(args.tag, args.run_id, args.repo, args.dry_run)
        else:
            print(archive(output=args.output))
        return 0
    except (OSError, ValueError, KeyError, tarfile.TarError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
