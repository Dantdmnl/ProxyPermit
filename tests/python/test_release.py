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

    def test_archive_excludes_secrets_and_matches_manifest(self):
        (self.root / '.env').write_text('SECRET=private')
        (self.root / 'app/private.key').write_text('secret')
        archive = release.archive(self.root)
        with tarfile.open(archive) as bundle:
            names = bundle.getnames()
            self.assertFalse(any(name.endswith(('.env', '.key')) for name in names))
            manifest = json.load(bundle.extractfile('proxypermit-1.0.0/release-manifest.json'))
            for name, digest in manifest['sha256'].items():
                self.assertEqual(hashlib.sha256(bundle.extractfile('proxypermit-1.0.0/' + name).read()).hexdigest(), digest)


if __name__ == '__main__':
    unittest.main()
