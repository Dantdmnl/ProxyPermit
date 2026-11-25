import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('release_tool', ROOT / 'scripts/release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'VERSION').write_text('1.0.0\n')
        (self.root / 'CHANGELOG.md').write_text('# Changelog\n\n## [Unreleased]\n\n### Fixed\n\n- Preserve configured ports.\n')
        release.sync(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_prepare_updates_all_versions_and_moves_notes(self):
        release.prepare('2.0.0', self.root, date='2026-11-06')
        self.assertEqual(release.sync(self.root, check=True), '2.0.0')
        self.assertIn('## [2.0.0] - 2026-11-06', (self.root / 'CHANGELOG.md').read_text())

    def test_empty_notes_and_downgrade_change_nothing(self):
        with self.assertRaises(ValueError):
            release.prepare('0.9.0', self.root)
        (self.root / 'CHANGELOG.md').write_text('# Changelog\n\n## [Unreleased]\n')
        with self.assertRaises(ValueError):
            release.prepare('2.0.0', self.root)
        self.assertEqual(release.current_version(self.root), '1.0.0')

    def test_dry_run_and_stale_detection(self):
        self.assertIn('VERSION', release.prepare('2.0.0', self.root, dry_run=True))
        self.assertEqual(release.current_version(self.root), '1.0.0')
        (self.root / 'app/_version.py').write_text('stale')
        with self.assertRaises(ValueError):
            release.sync(self.root, check=True)

    def test_stable_release_from_same_development_base(self):
        (self.root / 'VERSION').write_text('2.0.0-dev.3\n')
        release.prepare('2.0.0', self.root)
        self.assertEqual(release.current_version(self.root), '2.0.0')

    def test_prerelease_preparation_uses_numeric_order_and_cannot_downgrade_stable(self):
        release.prepare('1.1.0-dev.2', self.root, date='2026-11-17')
        self.assertEqual(release.sync(self.root, check=True), '1.1.0-dev.2')
        self.assertIn('## [1.1.0-dev.2] - 2026-11-17', (self.root/'CHANGELOG.md').read_text())
        self.assertIn('Preserve configured ports.', release.release_notes('1.1.0-dev.2', self.root))
        self.assertLess(release.version_order('1.1.0-dev.2'), release.version_order('1.1.0-dev.10'))
        for target in ('1.1.0-dev.1', '1.1.0-dev.2', '1.0.1-dev.1'):
            with self.subTest(target=target), self.assertRaises(ValueError):release.prepare(target, self.root)
        (self.root/'VERSION').write_text('1.1.0\n')
        with self.assertRaises(ValueError):release.prepare('1.1.0-dev.3', self.root)

    def test_prerelease_publication_uses_verified_artifact_without_replacing_latest(self):
        version='1.1.0-dev.1';tag='v'+version;repository='example/ProxyPermit'
        release.prepare(version, self.root, date='2026-11-17')
        bundle=release.archive(self.root)
        created=[]
        def output(args, **_kwargs):
            if args[:2]==['git','rev-parse']:return (('a' if args[-1].endswith('^{commit}') else 'b')*40).encode()
            if args[:2]==['git','show']:
                return (self.root/args[2].split(':')[1]).read_bytes()
            if args[:2]==['gh','api']:
                value=(dict(event='push',status='completed',conclusion='success',head_branch=tag,head_sha='a'*40,
                            path='.github/workflows/ci.yml',name='ProxyPermit CI',head_repository=dict(full_name=repository))
                       if '/actions/runs/' in args[2] else dict(object=dict(sha='b'*40)))
                return json.dumps(value).encode()
            raise AssertionError(args)
        def run(args, **_kwargs):
            if args[:3]==['gh','run','download']:
                target=Path(args[args.index('--dir')+1]);target.mkdir()
                for path in bundle.parent.iterdir():shutil.copy2(path,target/path.name)
            elif args[:3]==['gh','release','view']:return subprocess.CompletedProcess(args,1)
            elif args[:3]==['gh','release','create']:
                created.append((args,Path(args[args.index('--notes-file')+1]).read_text()))
            else:raise AssertionError(args)
            return subprocess.CompletedProcess(args,0)
        with patch.object(release,'ROOT',self.root),patch.object(release.subprocess,'check_output',side_effect=output), \
             patch.object(release.subprocess,'run',side_effect=run):
            release.publish(tag,'123',repository)
        self.assertEqual(len(created),1)
        command,notes=created[0]
        self.assertIn('--prerelease',command);self.assertIn('--latest=false',command)
        self.assertIn('--verify-tag',command)
        self.assertIn('Pre-release for testing',notes)
        self.assertEqual(release.publication_flags('1.1.0'),[])
        for invalid in ('1.1.0-dev.01','1.1.0-rc.1','1.1.0-dev.-1'):
            with self.assertRaises(ValueError):release.publication_flags(invalid)

    def test_notes_select_only_the_requested_dated_section(self):
        release.prepare('1.0.1', self.root, date='2026-11-13')
        self.assertEqual(release.release_notes('1.0.1', self.root), '### Fixed\n\n- Preserve configured ports.\n')
        with self.assertRaises(ValueError):
            release.release_notes('1.0.2', self.root)

    def test_publication_requires_the_successful_tag_push_in_this_repository(self):
        run = dict(event='push', status='completed', conclusion='success', head_branch='v1.0.0',
                   head_sha='a' * 40, path='.github/workflows/ci.yml', name='ProxyPermit CI',
                   head_repository={'full_name': 'example/ProxyPermit'})
        release.validate_release_run(run, 'v1.0.0', 'a' * 40, 'example/ProxyPermit')
        running = {**run, 'status': 'in_progress', 'conclusion': None}
        with self.assertRaises(ValueError):
            release.validate_release_run(running, 'v1.0.0', 'a' * 40, 'example/ProxyPermit')
        release.validate_release_run(running, 'v1.0.0', 'a' * 40, 'example/ProxyPermit',
                                     [dict(name='Release archive & tag validation', status='completed', conclusion='success')])
        for key, value in [('event', 'pull_request'), ('head_branch', 'main'), ('head_sha', 'b' * 40),
                           ('conclusion', 'failure'), ('path', '.github/workflows/other.yml'),
                           ('head_repository', {'full_name': 'fork/ProxyPermit'})]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                release.validate_release_run({**run, key: value}, 'v1.0.0', 'a' * 40, 'example/ProxyPermit')

    def test_publication_checks_archive_checksum_and_tag_metadata(self):
        release.prepare('1.0.1', self.root, date='2026-11-13')
        archive = release.archive(self.root)
        changelog = (self.root / 'CHANGELOG.md').read_bytes()
        release.verify_release_artifact(archive.parent, '1.0.1', changelog)
        with self.assertRaises(ValueError):
            release.verify_release_artifact(archive.parent, '1.0.1', b'unrelated changelog')
        archive.write_bytes(archive.read_bytes() + b'changed')
        with self.assertRaises(ValueError):
            release.verify_release_artifact(archive.parent, '1.0.1', changelog)

    def test_archive_excludes_secrets_and_matches_manifest(self):
        (self.root / '.env').write_text('SECRET=private')
        (self.root / 'app/private.key').write_text('secret')
        (self.root / 'app/audit.log.1').write_text('private audit fixture')
        (self.root / 'app/ha-peer.json').write_text('{"key":"fixture"}')
        (self.root / 'app/.test-data').mkdir()
        (self.root / 'app/.test-data/private.json').write_text('{"fixture":true}')
        (self.root / 'ha-tls/maintenance').mkdir(parents=True)
        (self.root / 'ha-tls/maintenance/plan.json').write_text('{"runtime":"fixture"}')
        (self.root / 'cluster-protection.json').write_text('{"runtime":"fixture"}')
        (self.root / 'app/cluster-maintenance.json').write_text('{"runtime":"fixture"}')
        for name in ('cluster-plan.json','cluster-bootstrap.json','cluster-plan-web-request.json','proxmox-fencing.json','authkey','cluster.p12','cluster.pfx'):
            (self.root/'app'/name).write_text('private fixture')
        preflight = ('qdevice-ready.json', 'fencing-tested.json', 'fencing-off-observed.json', 'preflight-current.json')
        for name in preflight:
            (self.root/'app'/name).write_text('private fixture')
        for name in ('nssdb','proxypermit-cluster-bootstrap'):
            (self.root/'app'/name).mkdir();(self.root/'app'/name/'private.json').write_text('private fixture')
        archive = release.archive(self.root)
        with tarfile.open(archive) as bundle:
            names = bundle.getnames()
            self.assertFalse(any(name.endswith(('.env', '.key')) for name in names))
            self.assertFalse(any('.log.' in name or 'ha-peer.json' in name or '/.test-data/' in name for name in names))
            self.assertFalse(any('/ha-tls/' in name or name.endswith('/cluster-protection.json') for name in names))
            self.assertFalse(any(name.endswith('/cluster-maintenance.json') for name in names))
            self.assertFalse(any(name.endswith(('/cluster-plan.json','/cluster-bootstrap.json','/cluster-plan-web-request.json','/proxmox-fencing.json','/authkey','.p12','.pfx'))
                                 or '/nssdb/' in name or '/proxypermit-cluster-bootstrap/' in name for name in names))
            self.assertFalse(any(name.rsplit('/', 1)[-1] in preflight for name in names))
            manifest = json.load(bundle.extractfile('proxypermit-1.0.0/release-manifest.json'))
            for name, digest in manifest['sha256'].items():
                self.assertEqual(hashlib.sha256(bundle.extractfile('proxypermit-1.0.0/' + name).read()).hexdigest(), digest)

    def test_archive_bytes_do_not_depend_on_checkout_timestamps(self):
        source = self.root / 'app' / 'packaging-fixture.py'
        source.write_bytes(b'example = True\n')
        archive = release.archive(self.root, self.root / 'first')
        os.utime(source, (1234567890, 1234567890))
        os.utime(self.root / 'VERSION', (1234567890, 1234567890))
        rebuilt = release.archive(self.root, self.root / 'second')
        self.assertEqual(archive.read_bytes(), rebuilt.read_bytes())
        with tarfile.open(archive) as bundle:
            entry = bundle.getmember('proxypermit-1.0.0/app/packaging-fixture.py')
            self.assertEqual((entry.mtime, entry.uid, entry.gid, entry.uname, entry.gname),
                             (0, 0, 0, '', ''))


if __name__ == '__main__':
    unittest.main()
