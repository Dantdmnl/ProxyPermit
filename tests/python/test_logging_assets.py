"""Root helper umask must not make public service metadata inaccessible to the UI."""
import os
from pathlib import Path
import tempfile
import unittest

from scripts.deploy.logging_policy import MARKER, write_asset

CONTENT = MARKER + '[Service]\nLogNamespace=proxypermit\n'


@unittest.skipIf(os.name == 'nt', 'POSIX directory permissions')
class LoggingAssetTests(unittest.TestCase):
    def test_new_directories_are_publicly_traversable_under_private_umask(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'system' / 'keepalived.service.d' / '20-proxypermit-logging.conf'
            previous = os.umask(0o077)
            try:
                write_asset(path, CONTENT)
            finally:
                os.umask(previous)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o755)
            self.assertEqual(path.parent.parent.stat().st_mode & 0o777, 0o755)
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)

    def test_existing_private_folder_with_other_files_is_not_widened(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder) / 'keepalived.service.d'
            parent.mkdir(mode=0o700)
            path = parent / '20-proxypermit-logging.conf'
            path.write_text(CONTENT); path.chmod(0o644)
            secret = parent / 'administrator.conf'
            secret.write_text('isolated fixture'); secret.chmod(0o600)
            write_asset(path, CONTENT)
            self.assertEqual(parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(secret.read_text(), 'isolated fixture')
            self.assertEqual(secret.stat().st_mode & 0o777, 0o600)

    def test_symlink_parent_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'operator'
            target.mkdir(mode=0o700)
            parent = Path(folder) / 'keepalived.service.d'
            parent.symlink_to(target, target_is_directory=True)
            with self.assertRaises(ValueError):
                write_asset(parent / '20-proxypermit-logging.conf', CONTENT)
            self.assertEqual(list(target.iterdir()), [])

    @unittest.skipUnless(getattr(os, 'geteuid', lambda: -1)() == 0, 'Root-owned migration fixture')
    def test_exact_old_owned_public_directory_is_repaired(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder) / 'keepalived.service.d'
            parent.mkdir(mode=0o700)
            path = parent / '20-proxypermit-logging.conf'
            path.write_text(CONTENT); path.chmod(0o644)
            write_asset(path, CONTENT)
            self.assertEqual(parent.stat().st_mode & 0o777, 0o755)

    @unittest.skipUnless(getattr(os, 'geteuid', lambda: -1)() == 0, 'Root-owned migration fixture')
    def test_unrecognized_old_content_is_not_used_to_widen_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder) / 'keepalived.service.d'
            parent.mkdir(mode=0o700)
            path = parent / '20-proxypermit-logging.conf'
            path.write_text('operator-private fixture'); path.chmod(0o644)
            write_asset(path, CONTENT)
            self.assertEqual(parent.stat().st_mode & 0o777, 0o700)
