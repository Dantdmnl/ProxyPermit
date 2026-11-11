import unittest
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app.web import manager as main


class RemotePreviewSecurityTests(unittest.TestCase):
    def test_hsts_requires_https_and_defaults_to_the_current_host(self):
        with patch.object(main, 'ENABLE_HSTS', True), patch.object(main, 'HSTS_INCLUDE_SUBDOMAINS', False), \
                patch.object(main, 'HSTS_PRELOAD', False):
            with main.app.test_client() as client:
                http = client.get('/', base_url='http://localhost', headers={'X-Forwarded-Proto': 'https'})
                https = client.get('/', base_url='https://localhost')
        self.assertNotIn('Strict-Transport-Security', http.headers)
        self.assertEqual(https.headers['Strict-Transport-Security'], f'max-age={main.HSTS_MAX_AGE}')

    def setUp(self):
        self.client = main.app.test_client()
        self.original_enable_auth = main.ENABLE_AUTH
        self.original_username = main.AUTH_USERNAME
        main.ENABLE_AUTH = True
        main.AUTH_USERNAME = 'test-admin'
        with self.client.session_transaction() as session:
            session['user'] = main.AUTH_USERNAME
            session['session_version'] = main._get_session_version()

    def tearDown(self):
        main.ENABLE_AUTH = self.original_enable_auth
        main.AUTH_USERNAME = self.original_username

    @patch.object(main.requests, 'get')
    def test_remote_preview_rejects_unconfigured_host(self, request_get):
        response = self.client.post(
            '/api/merge/remote-preview',
            json={'remote_url': 'http://169.254.169.254:8080'},
        )

        self.assertEqual(response.status_code, 403)
        request_get.assert_not_called()

    def test_browser_origin_cannot_force_logout(self):
        result = self.client.post('/api/auth/force-logout', headers={'Origin': 'https://untrusted.example'})
        self.assertEqual(result.status_code, 403)
        with self.client.session_transaction() as session:
            self.assertEqual(session['user'], main.AUTH_USERNAME)

    def test_malformed_login_and_unicode_credentials(self):
        result = self.client.post('/api/auth/login', json={'username': ['admin'], 'password': 123})
        self.assertEqual(result.status_code, 400)
        self.assertFalse(main.check_auth('géén-admin', 'wachtwoord'))

    def test_login_lockout_survives_spoofed_forwarding_headers(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(main, 'SESSION_STATE_FILE', str(Path(directory) / 'session.json')), \
                patch.object(main, 'AUTH_LOGIN_RATE_LIMIT_ENABLED', True), \
                patch.object(main, 'AUTH_LOGIN_MAX_ATTEMPTS', 2), \
                patch.object(main, 'check_auth', return_value=False):
            for index, expected in enumerate((401, 429), 1):
                result = main.app.test_client().post('/api/auth/login', json={'username': 'test-admin', 'password': 'wrong'},
                                                     headers={'X-Forwarded-For': f'192.0.2.{index}'})
                self.assertEqual(result.status_code, expected)
            self.assertIn('127.0.0.1', (Path(directory) / 'login-attempts.json').read_text())

    def test_login_lockout_expires_at_original_time_after_locale_changes(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(main, 'SESSION_STATE_FILE', str(Path(directory) / 'session.json')), \
                patch.object(main, 'AUTH_ACCOUNTS_FILE', ''), \
                patch.object(main, 'AUTH_LOGIN_RATE_LIMIT_ENABLED', True), \
                patch.object(main, 'AUTH_LOGIN_MAX_ATTEMPTS', 2), \
                patch.object(main, 'AUTH_LOGIN_LOCKOUT_SECONDS', 120), \
                patch.object(main, 'AUTH_LOGIN_DYNAMIC_LOCKOUT_ENABLED', False), \
                patch.object(main.time, 'time', return_value=10_000) as clock, \
                patch.object(main, 'check_auth', return_value=False) as check:
            client = main.app.test_client()
            credentials = {'username': 'test-admin', 'password': 'wrong'}
            client.get('/?lang=nl')
            self.assertEqual(client.post('/api/auth/login', json=credentials).status_code, 401)
            client.get('/?lang=en')
            locked = client.post('/api/auth/login', json=credentials)
            self.assertEqual(locked.status_code, 429)
            self.assertEqual(locked.headers['Retry-After'], '120')

            check.return_value = True
            for elapsed, locale in [(10, 'nl'), (40, 'en'), (119, 'nl')]:
                with self.subTest(locale=locale, elapsed=elapsed):
                    clock.return_value = 10_000 + elapsed
                    client.get('/?lang=' + locale)
                    result = client.post('/api/auth/login', json=credentials)
                    self.assertEqual(result.status_code, 429)
                    self.assertEqual(result.headers['Content-Language'], locale)
                    self.assertEqual(result.headers['Retry-After'], str(120 - elapsed))
                    self.assertEqual(result.json['retry_after'], 120 - elapsed)
            self.assertEqual(check.call_count, 2)
            clock.return_value = 10_120
            self.assertEqual(client.post('/api/auth/login', json=credentials).status_code, 200)

    def test_transfer_can_only_be_consumed_once_on_target(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(main, 'NONCE_STORE_FILE', str(Path(directory) / 'nonce.json')):
            token = self.client.post('/api/auth/transfer').get_json()['token']
            target = main.app.test_client()
            self.assertEqual(target.post('/api/auth/consume', json={'token': token}).status_code, 200)
            self.assertEqual(target.post('/api/auth/consume', json={'token': token}).status_code, 401)


class SyncSecurityTests(unittest.TestCase):
    def test_signed_upload_is_applied_once_by_the_https_api(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict('os.environ', DEPLOYMENT_MODE='ha', PROXYPERMIT_MANAGED_HA='false',
                           NODE0_ADDRESS='192.0.2.11', NODE1_ADDRESS='192.0.2.12'), \
                patch.object(main, 'NONCE_STORE_FILE', str(Path(directory) / 'nonce.json')), \
                patch.object(main, 'get_sync_secret', return_value='test-secret'), \
                patch.object(main, 'apply_synced_filter') as apply_filter:
            path, nonce, timestamp, body = '/api/sync/upload', 'cd' * 16, str(int(time.time())), '^example[.]com$'
            signature = main.generate_hmac_signature('test-secret', 'POST', path, timestamp, nonce, body)
            headers = {'X-Sync-Nonce': nonce, 'X-Sync-Timestamp': timestamp, 'X-Sync-Signature': signature}
            client = main.app.test_client()
            for expected in (200, 401):
                result = client.post(path, base_url='https://node.example', data=body, headers=headers,
                                     environ_overrides={'REMOTE_ADDR': '192.0.2.11'})
                self.assertEqual(result.status_code, expected)
            apply_filter.assert_called_once_with(body)

    def test_peer_header_cannot_impersonate_configured_ip(self):
        with patch.dict('os.environ', DEPLOYMENT_MODE='ha', PROXYPERMIT_MANAGED_HA='false',
                        NODE0_ADDRESS='192.0.2.11', NODE1_ADDRESS='192.0.2.12', ALLOW_LOCALHOST_SYNC='false'):
            client = main.app.test_client()
            response = client.get('/api/sync/info', base_url='https://node.example',
                                  headers={'X-Forwarded-For': '192.0.2.11'},
                                  environ_overrides={'REMOTE_ADDR': '192.0.2.99'})
            self.assertEqual(response.status_code, 403)
            response = client.get('/api/sync/info', base_url='http://node.example',
                                  environ_overrides={'REMOTE_ADDR': '192.0.2.11'})
            self.assertEqual(response.status_code, 403)

    def test_signed_nonce_is_accepted_once_even_concurrently(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(main, 'NONCE_STORE_FILE', str(Path(directory) / 'nonce.json')):
            timestamp = str(int(time.time()))
            args = ('test-secret', 'POST', '/api/sync/upload', timestamp, 'ab' * 16)
            signature = main.generate_hmac_signature(*args, '^example[.]com$')
            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(executor.map(
                    lambda _: main.verify_hmac_signature(*args, signature, '^example[.]com$'), range(4)
                ))
            self.assertEqual(results.count(True), 1)

    def test_sync_activation_failure_restores_previous_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'filter'
            path.write_bytes(b'^previous[.]example$\r\n')
            with patch.object(main, 'WHITELIST_PATH', str(path)), patch.object(main, 'has_pending_changes', return_value=False), \
                    patch.object(main, 'validate_filter', return_value={'valid': True}), \
                    patch.object(main.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'systemctl')), \
                    patch.object(main.whitelist_manager, 'restart_tinyproxy', side_effect=[(False, None), (True, None)]):
                with self.assertRaises(RuntimeError):
                    main.apply_synced_filter('^new[.]example$\n')
            self.assertEqual(path.read_bytes(), b'^previous[.]example$\r\n')


if __name__ == '__main__':
    unittest.main()
