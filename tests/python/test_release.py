import hashlib
import importlib.util
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

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
        archive = release.archive(self.root)
        with tarfile.open(archive) as bundle:
            names = bundle.getnames()
            self.assertFalse(any(name.endswith(('.env', '.key')) for name in names))
            self.assertFalse(any('.log.' in name or 'ha-peer.json' in name or '/.test-data/' in name for name in names))
            manifest = json.load(bundle.extractfile('proxypermit-1.0.0/release-manifest.json'))
            for name, digest in manifest['sha256'].items():
                self.assertEqual(hashlib.sha256(bundle.extractfile('proxypermit-1.0.0/' + name).read()).hexdigest(), digest)


if __name__ == '__main__':
    unittest.main()
