"""Atomic policy updates and installer recovery retain narrow native-read ACLs."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app.core.files import access_acl
from app.core.tinyproxy import atomic_write, TinyproxyConfigStore, revision
from app.core.filters import WhitelistManager


@unittest.skipUnless(os.name != 'nt' and shutil.which('setfacl'), 'POSIX ACL tools required.')
class FilePermissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'policy'
        self.path.write_text('Port 3128\nAllow 127.0.0.1\n')
        self.path.chmod(0o640)
        subprocess.run(['setfacl', '--no-mask', '-m', 'u:42424:r--', str(self.path)], check=True)
        self.acl = access_acl(self.path)
        self.assertIsNotNone(self.acl)

    def test_config_activation_and_rollback_keep_mode_and_acl(self):
        store = TinyproxyConfigStore(self.path)
        original = self.path.read_text()
        with self.assertRaises(ValueError):
            store.save(original.replace('3128', '3129'), revision(original), activate=lambda: (False, 'example failure'))
        self.assertEqual(self.path.read_text(), original)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(access_acl(self.path), self.acl)

    def test_domain_editor_preserves_native_reader_acl(self):
        self.path.write_text('# test policy\n')
        manager = WhitelistManager(str(self.path))
        self.assertTrue(manager.add_entry('example.invalid')[0])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(access_acl(self.path), self.acl)

    def test_acl_copy_failure_does_not_replace_live_content(self):
        original = self.path.read_bytes()
        with patch('app.core.tinyproxy.restore_access_acl', side_effect=OSError('example failure')):
            with self.assertRaises(OSError):
                atomic_write(self.path, 'replacement', 0o640)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(access_acl(self.path), self.acl)

    def test_explicitly_tighter_mode_does_not_regrant_old_named_readers(self):
        atomic_write(self.path, 'private value', 0o600)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertIsNone(access_acl(self.path))
