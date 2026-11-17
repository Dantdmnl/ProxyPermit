"""Management TLS trust, renewal and interrupted activation use isolated root fixtures."""
import hashlib
import json
import os
from pathlib import Path
import ssl
import tempfile
import time
import unittest
from unittest.mock import patch

from app.core.tinyproxy import ConfigError
from app.core import management_tls as public_tls
from scripts.deploy import management_tls as tls


class ManagementNamesTests(unittest.TestCase):
    def test_names_are_explicit_deduplicated_and_loopback_remains_verifiable(self):
        self.assertEqual(tls.names(['Example.COM', 'localhost'], ['192.0.2.10', '127.0.0.1']),
                         ['DNS:example.com', 'DNS:localhost', 'IP:127.0.0.1', 'IP:192.0.2.10', 'IP:::1'])
        for dns, ips in [(['*.example.com'], []), (['bad\nDNS:attacker.invalid'], []),
                         (['-invalid.example'], []), ([], ['0.0.0.0']), ([], ['224.0.0.1'])]:
            with self.subTest(dns=dns, ips=ips), self.assertRaises(ValueError):
                tls.names(dns, ips)

    def test_environment_preserves_secrets_and_removes_duplicate_owned_settings(self):
        before = '# isolated\nSECRET=private-fixture\nexport MANAGER_SCHEME=http\nMANAGER_SCHEME=http\n'
        after = tls.rewrite_environment(before, dict(MANAGER_SCHEME='https', SESSION_COOKIE_SECURE='true'))
        self.assertIn('SECRET=private-fixture\n', after)
        self.assertEqual(after.count('MANAGER_SCHEME='), 1)
        self.assertNotIn('MANAGER_SCHEME=http\n', after)
        self.assertIn('SESSION_COOKIE_SECURE=true', after)

    def test_no_listener_changes_or_unix_tls(self):
        self.assertEqual(tls.endpoint({'GUNICORN_BIND': '127.0.0.1:8080'}), ('127.0.0.1', 8080))
        self.assertEqual(tls.endpoint({'APP_BIND': '[::]:8443'}), ('::1', 8443))
        for bind in ('unix:/run/example.sock', '127.0.0.1:1,127.0.0.1:2', 'localhost:0'):
            with self.assertRaises(ConfigError):
                tls.endpoint({'APP_BIND': bind})


@unittest.skipUnless(os.name == 'posix' and getattr(os, 'geteuid', lambda: -1)() == 0, 'Root-owned OpenSSL fixtures')
class ManagementTlsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.folder = self.root / 'management-tls'
        self.folder.mkdir(mode=0o750)
        self.env = self.root / '.env'
        self.env.write_text('MANAGER_SCHEME=http\nGUNICORN_BIND=127.0.0.1:8080\nSECRET=isolated-secret\n')
        self.env.chmod(0o640)
        for item in (patch.object(tls, 'FOLDER', self.folder), patch.object(tls, 'ENVIRONMENT', self.env)):
            item.start(); self.addCleanup(item.stop)

    def test_real_leaf_renewal_preserves_ca_and_rotates_key_and_fingerprint(self):
        sans = tls.names(['console.example.invalid'], ['192.0.2.10'])
        first = tls.generate(sans, 0)
        root = (self.folder / 'ca.pem').read_bytes()
        second = tls.generate(sans, 0)
        self.assertEqual(root, (self.folder / 'ca.pem').read_bytes())
        self.assertEqual(first['caFingerprint'], second['caFingerprint'])
        self.assertNotEqual(first['fingerprint'], second['fingerprint'])
        self.assertNotEqual((self.folder / first['generation'] / 'key.pem').read_bytes(),
                            (self.folder / second['generation'] / 'key.pem').read_bytes())
        self.assertEqual((self.folder / 'ca-key.pem').stat().st_mode & 0o777, 0o600)
        key = self.folder / second['generation'] / 'key.pem'
        self.assertIn('BEGIN PRIVATE KEY', key.read_text())
        self.assertEqual(key.stat().st_mode & 0o777, 0o640)
        cert = self.folder / second['generation'] / 'cert.pem'
        tls.run('openssl', 'verify', '-CAfile', str(self.folder / 'ca.pem'), '-verify_hostname', 'console.example.invalid', str(cert))
        (self.folder / 'ca-key.pem').chmod(0o644)
        with self.assertRaises(ConfigError):
            tls.generate(sans, 0)

    def test_too_old_ca_is_not_replaced_silently(self):
        first = tls.generate(tls.names(), 0)
        key = (self.folder / 'ca-key.pem').read_bytes()
        with patch.object(tls, 'certificate', return_value=dict(expires=time.time() + 86400)):
            with self.assertRaises(ConfigError):
                tls.generate(tls.names(), 0)
        self.assertEqual(key, (self.folder / 'ca-key.pem').read_bytes())
        self.assertTrue((self.folder / first['generation'] / 'cert.pem').is_file())

    def test_public_metadata_warns_before_expiry_and_rejects_untrusted_or_inactive_status(self):
        profile = tls.generate(tls.names(), 0)
        values = dict(MANAGER_SCHEME='https', MANAGER_CERT=str(self.folder / profile['generation'] / 'cert.pem'))
        with patch.object(public_tls, 'FOLDER', self.folder):
            self.assertEqual(public_tls.status(values)['state'], 'valid')
            self.assertEqual(public_tls.status(values, now=profile['expires'] - 7 * 86400)['state'], 'due')
            self.assertEqual(public_tls.status(values, now=profile['expires'])['state'], 'expired')
            self.assertEqual(public_tls.status(dict(values, GUNICORN_USE_TLS='false'))['state'], 'unknown')
            info = self.folder / profile['generation'] / 'info.json'
            info.chmod(0o666)
            self.assertEqual(public_tls.status(values)['state'], 'unknown')
            self.assertIsNone(public_tls.status({}))

    def checkpoint(self, phase='prepared'):
        before = self.env.read_text()
        after = tls.rewrite_environment(before, {'MANAGER_SCHEME': 'https'})
        tls.write(self.folder / 'before-env', before)
        operation = dict(id='a' * 32, actor=0, phase=phase, profile={'isolated': True},
                         beforeHash=hashlib.sha256(before.encode()).hexdigest(),
                         afterHash=hashlib.sha256(after.encode()).hexdigest())
        tls.write(self.folder / 'operation.json', json.dumps(operation))
        self.env.write_text(after)
        return before, after

    def test_prepared_boot_recovery_restores_environment_without_service_commands(self):
        before, _ = self.checkpoint()
        with patch.object(tls, 'run') as command, patch.object(tls, 'audit') as audit:
            tls.recover(self.root, files_only=True)
        command.assert_not_called()
        audit.assert_called_once()
        self.assertEqual(self.env.read_text(), before)
        self.assertFalse((self.folder / 'operation.json').exists())
        tls.recover(self.root, files_only=True)

    def test_verified_recovery_completes_forward_and_external_edits_block_recovery(self):
        _, after = self.checkpoint('verified')
        with patch.object(tls, 'run'), patch.object(tls, 'audit'):
            tls.recover(self.root, files_only=True)
        self.assertEqual(self.env.read_text(), after)
        self.assertEqual(json.loads((self.folder / 'profile.json').read_text()), {'isolated': True})
        self.checkpoint()
        self.env.write_text('MANAGER_SCHEME=https\nUNRELATED=administrator-change\n')
        with patch.object(tls, 'run') as command, self.assertRaises(ConfigError):
            tls.recover(self.root)
        command.assert_not_called()
        self.assertTrue((self.folder / 'operation.json').exists())

    def test_symlinks_and_world_writable_configuration_are_rejected(self):
        link = self.root / 'link'
        link.symlink_to(self.env)
        with self.assertRaises(OSError):
            tls.protected(link)
        self.env.chmod(0o666)
        with self.assertRaises(ConfigError):
            tls.protected(self.env)

    def test_installation_rollback_preserves_tls_binding_and_rejects_pre_tls_software(self):
        profile = tls.generate(tls.names(), 0)
        tls.write(self.folder / 'profile.json', json.dumps(profile))
        updates = dict(MANAGER_SCHEME='https', GUNICORN_USE_TLS='true', SESSION_COOKIE_SECURE='true',
                       MANAGER_CERT=str(self.folder / profile['generation'] / 'cert.pem'),
                       MANAGER_KEY=str(self.folder / profile['generation'] / 'key.pem'))
        tls.write(self.env, tls.rewrite_environment(self.env.read_text(), updates), 0o640)
        previous = self.root / 'previous-release'
        entries = [{'path': '/opt/proxypermit/current', 'symlink': str(previous)}]
        with self.assertRaises(ConfigError):
            tls.rollback_values(entries)
        module = previous / 'scripts/deploy/management_tls.py'
        module.parent.mkdir(parents=True); module.write_text('# isolated supported release')
        restored = tls.rollback_values(entries)
        for name, value in updates.items():
            self.assertEqual(restored[name], value)

    def test_activation_failure_restores_only_management_and_success_is_verified_before_commit(self):
        original_run = tls.run
        before = self.env.read_text()
        commands = []
        def command(*args, **kwargs):
            if args[0] == 'systemctl':
                commands.append(args)
                if args == ('systemctl', 'start', tls.RECOVER):
                    self.assertFalse((self.folder / 'operation.json').exists())
                return None
            return original_run(*args, **kwargs)
        with patch.object(tls, 'run', side_effect=command), patch.object(tls, 'assets', return_value={}), \
                patch.object(tls, 'audit'), patch.object(tls, 'probe', side_effect=OSError('isolated readiness failure')):
            with self.assertRaises(OSError):
                tls.activate(tls.names(), self.root, 0)
        self.assertEqual(self.env.read_text(), before)
        self.assertIsNone(tls.load_profile())
        self.assertFalse((self.folder / 'operation.json').exists())
        self.assertFalse(any('tinyproxy.service' in item or 'keepalived.service' in item for item in commands))
        with patch.object(tls, 'run', side_effect=command), patch.object(tls, 'assets', return_value={}), \
                patch.object(tls, 'audit'), patch.object(tls, 'probe', return_value=True):
            result = tls.activate(tls.names(), self.root, 0)
        self.assertEqual(tls.load_profile()['generation'], result['generation'])
        self.assertIn('MANAGER_SCHEME=https\n', self.env.read_text())
        self.assertIn('ENABLE_HSTS=false\n', self.env.read_text())


if __name__ == '__main__':
    unittest.main()
