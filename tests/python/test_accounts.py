import json
import tempfile
import subprocess
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pyotp
from werkzeug.security import generate_password_hash

from app.web import manager
from app.web import settings


class LocalAccountTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.accounts = root / 'accounts.json'
        self.secret = pyotp.random_base32()
        password_hash = generate_password_hash('test passphrase')
        self.data = {'alice': {'password_hash': password_hash, 'revision': 'alice-v1', 'totp_secret': self.secret},
                     'bob': {'password_hash': password_hash, 'revision': 'bob-v1'}}
        self.accounts.write_text(json.dumps(self.data))
        config = root / 'tinyproxy.conf'
        config.write_text('Port 3128\nAllow 127.0.0.1\n')
        environment = patch.dict('os.environ', TINYPROXY_CONFIG_PATH=str(config))
        environment.start()
        self.addCleanup(environment.stop)
        settings = patch.multiple(manager, ENABLE_AUTH=True, AUTH_ACCOUNTS_FILE=str(self.accounts),
                                  SESSION_STATE_FILE=str(root / 'session.json'), AUTH_LOGIN_RATE_LIMIT_ENABLED=False)
        settings.start()
        self.addCleanup(settings.stop)
        self.client = manager.app.test_client()

    def login(self, name, **fields):
        return self.client.post('/api/auth/login', json=dict(username=name, password='test passphrase', **fields))

    def test_privacy_notice_is_public_but_log_records_require_authentication(self):
        self.assertEqual(self.client.get('/api/logs').status_code, 401)
        for language in ('nl', 'en'):
            notice = self.client.get('/privacy?lang=' + language)
            self.assertEqual(notice.status_code, 200)
            self.assertNotIn(b'alice', notice.data)
            self.assertNotIn(b'bob', notice.data)
            self.assertEqual(notice.headers['Cache-Control'], 'no-store')

    def test_audit_excludes_secrets_and_does_not_duplicate_to_operational_logs(self):
        self.login('bob')
        with self.client.application.test_request_context('/api/fixture'):
            with patch.object(manager.audit_logger, 'info') as record, patch.object(manager.logger, 'info') as operational:
                manager._audit_event('fixture_change', password='sensitive-password', token='sensitive-token',
                                     description='a private note')
            value = json.loads(record.call_args[0][0])
            self.assertNotIn('password', value)
            self.assertNotIn('token', value)
            self.assertNotIn('description', value)
            operational.assert_not_called()

    def test_ha_network_inventory_requires_an_authenticated_account(self):
        with patch.object(settings, 'discover_network', return_value=[dict(interface='ens18')]) as discover:
            self.assertEqual(self.client.get('/api/settings/ha/network').status_code, 401)
            discover.assert_not_called()
            self.login('bob')
            response = self.client.get('/api/settings/ha/network')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()['networks'], [dict(interface='ens18')])
            self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_ha_setup_requires_review_reauthentication_and_unchanged_plan(self):
        self.login('bob')
        plan = dict(node='a', interface='ens18', nodeA='192.0.2.11', nodeB='192.0.2.12',
                    vip='192.0.2.10/24', clients='192.0.2.0/24', vrid='51')
        self.client.get('/settings')
        with self.client.session_transaction() as session:
            csrf = session['settings_csrf']
        headers = {'X-CSRF-Token': csrf}
        folder = self.accounts.parent
        with patch.object(settings, 'ha_setup_available', return_value=True), \
                patch.object(settings, 'has_pending_changes', return_value=False), \
                patch.object(settings, 'setup_revision', return_value='fixture-revision'), \
                patch.object(settings, 'HA_SETUP_REQUEST', folder / 'ha-request.json'), \
                patch.object(settings, 'HA_SETUP_RESULT', folder / 'ha-result.json'), \
                patch.object(settings.subprocess, 'run') as command:
            self.assertEqual(self.client.post('/api/settings/ha/setup', json={}).status_code, 403)
            preview = self.client.post('/api/settings/ha/preview', headers=headers, json=plan)
            self.assertEqual(preview.status_code, 200)
            body = dict(plan=plan, revision='fixture-revision', confirmed=True, password='incorrect')
            self.assertNotEqual(self.client.post('/api/settings/ha/setup', headers=headers, json=body).status_code, 202)
            command.assert_not_called()
            body['password'] = 'test passphrase'
            body['plan'] = {**plan, 'interface': 'ens19'}
            self.assertEqual(self.client.post('/api/settings/ha/setup', headers=headers, json=body).status_code, 409)
            command.assert_not_called()
            body['plan'] = plan
            command.side_effect = subprocess.TimeoutExpired(settings.HA_SETUP_COMMAND, 10)
            self.assertEqual(self.client.post('/api/settings/ha/setup', headers=headers, json=body).status_code, 202)
            command.assert_called_once_with(settings.HA_SETUP_COMMAND, check=True, capture_output=True, timeout=10)
            request = json.loads((folder / 'ha-request.json').read_text())
            self.assertEqual(request['topology']['node'], 'a')
            self.assertNotIn('password', request)
            self.assertNotIn('otp', request)
            self.assertEqual(self.client.post('/api/settings/ha/setup', headers=headers, json=body).status_code, 409)
            # A second approved worker must not overwrite a still-unconsumed request.
            with self.client.session_transaction() as session:
                session['ha_setup_plan'] = dict(revision='fixture-revision',
                    fingerprint=settings.setup_fingerprint(plan), created=int(time.time()))
            (folder / 'ha-result.json').write_text(json.dumps(dict(phase='failed')))
            self.assertEqual(self.client.post('/api/settings/ha/setup', headers=headers, json=body).status_code, 409)
            self.assertEqual(command.call_count, 1)
            self.assertEqual(json.loads((folder / 'ha-request.json').read_text())['id'], request['id'])

    def test_optional_factor_challenge_and_replay(self):
        response = self.login('alice')
        self.assertEqual(response.status_code, 401)
        self.assertTrue(response.get_json()['otpRequired'])
        self.assertEqual(self.client.get('/api/config').status_code, 401)
        now = 1_800_000_000
        code = pyotp.TOTP(self.secret).at(now)
        window = {pyotp.TOTP(self.secret).at(now + offset) for offset in (-30, 0, 30)}
        invalid = next(f'{number:06d}' for number in range(4) if f'{number:06d}' not in window)
        with patch('app.core.accounts.time.time', return_value=now):
            self.assertEqual(self.login('alice', otp=invalid).status_code, 401)
            self.assertEqual(self.login('alice', otp=code).status_code, 200)
            self.assertEqual(self.client.get('/api/auth/me').get_json()['username'], 'alice')
            self.assertEqual(self.client.post('/api/auth/transfer').status_code, 409)
            self.client.post('/api/auth/logout')
            self.assertEqual(self.login('alice', otp=code).status_code, 401)

    def test_password_only_and_revocation_by_account_revision(self):
        self.assertEqual(self.login('bob').status_code, 200)
        self.assertEqual(self.client.get('/api/auth/me').get_json()['username'], 'bob')
        self.data['bob']['revision'] = 'bob-v2'
        self.accounts.write_text(json.dumps(self.data))
        self.assertEqual(self.client.get('/api/config').status_code, 401)

    def test_audit_records_the_authenticated_actor(self):
        self.assertEqual(self.login('bob').status_code, 200)
        config = self.client.get('/api/proxy/config').get_json()
        with patch.object(manager.audit_logger, 'info') as audit:
            response = self.client.put('/api/proxy/config', headers={'X-CSRF-Token': config['csrfToken']},
                                       json={'content': 'Port 3128\nAllow 127.0.0.1\nTimeout 60\n', 'revision': config['revision']})
            self.assertEqual(response.status_code, 200)
            record = json.loads(audit.call_args.args[0])
            self.assertEqual(record['username'], 'bob')
            self.assertEqual(record['event'], 'tinyproxy_config_saved')

    def settings_change(self, action, **fields):
        self.client.get('/settings')
        with self.client.session_transaction() as current:
            csrf = current['settings_csrf']
        return self.client.post('/api/settings/accounts', headers={'X-CSRF-Token': csrf},
                                json=dict(action=action, **fields))

    def test_ha_preview_requires_login_and_csrf_and_validates_topology(self):
        route = '/api/settings/ha/preview'
        values = dict(node='b', interface='ens18', nodeA='192.0.2.11', nodeB='192.0.2.12',
                      vip='192.0.2.10/24', clients='192.0.2.0/24', vrid='51')
        self.assertEqual(self.client.post(route, json=values).status_code, 401)
        self.assertEqual(self.login('bob').status_code, 200)
        self.client.get('/settings')
        self.assertEqual(self.client.post(route, json=values).status_code, 403)
        with self.client.session_transaction() as current:
            headers = {'X-CSRF-Token': current['settings_csrf']}
        response = self.client.post(route, json=values, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['topology']['peer'], '192.0.2.11')
        self.assertEqual(self.client.post(route, json={**values, 'vip': '192.0.2.12/24'}, headers=headers).status_code, 400)

    def test_ha_retirement_requires_confirmation_and_reauthentication(self):
        route = '/api/settings/ha/retire'
        self.assertEqual(self.login('bob').status_code, 200)
        self.client.get('/settings')
        with self.client.session_transaction() as current:
            headers = {'X-CSRF-Token': current['settings_csrf']}
        with patch.object(settings, 'ha_retirement_available', return_value=True), patch.object(settings.subprocess, 'run') as command:
            self.assertEqual(self.client.post(route, json={'confirmed': True}).status_code, 403)
            self.assertEqual(self.client.post(route, json={'confirmed': False}, headers=headers).status_code, 400)
            self.assertEqual(self.client.post(route, json={'confirmed': True, 'password': 'wrong'}, headers=headers).status_code, 401)
            command.assert_not_called()
            valid = {'confirmed': True, 'password': 'test passphrase'}
            self.assertEqual(self.client.post(route, json=valid, headers=headers).status_code, 200)
            command.assert_called_once_with(settings.HA_RETIRE_COMMAND, check=True, capture_output=True, timeout=90)

    def test_settings_requires_authentication_csrf_and_password(self):
        self.assertEqual(self.client.get('/settings').status_code, 403)
        self.assertEqual(self.client.post('/api/settings/accounts', json={}).status_code, 401)
        self.assertEqual(self.login('bob').status_code, 200)
        self.assertEqual(self.client.post('/api/settings/accounts', json={'action': 'remove'}).status_code, 403)
        self.assertEqual(self.client.post('/api/settings/accounts', json={'action': 'remove'},
                                          environ_overrides={'REMOTE_ADDR': '192.0.2.20'}).status_code, 403)
        self.assertEqual(self.settings_change('remove', username='alice', password='wrong').status_code, 401)
        self.assertIn('alice', json.loads(self.accounts.read_text()))

    def test_settings_can_create_remove_and_revoke_another_account(self):
        self.assertEqual(self.login('bob').status_code, 200)
        self.assertEqual(self.settings_change('create', username='charlie', password='test passphrase',
                                              newPassword='another passphrase').status_code, 200)
        other = manager.app.test_client()
        self.assertEqual(other.post('/api/auth/login', json={'username': 'charlie',
                                                            'password': 'another passphrase'}).status_code, 200)
        self.assertEqual(self.settings_change('remove', username='bob', password='test passphrase').status_code, 409)
        self.assertEqual(self.settings_change('remove', username='charlie', password='test passphrase').status_code, 200)
        self.assertEqual(other.get('/api/config').status_code, 401)

    def test_settings_enrolls_and_disables_only_with_valid_totp(self):
        self.assertEqual(self.login('bob').status_code, 200)
        now = int(time.time())
        with patch('app.core.accounts.time.time', return_value=now) as clock:
            setup = self.settings_change('totp-start', password='test passphrase')
            self.assertEqual(setup.status_code, 200)
            factor = pyotp.TOTP(setup.get_json()['secret'])
            self.assertNotIn('totp_secret', json.loads(self.accounts.read_text())['bob'])
            self.assertEqual(self.settings_change('totp-confirm', code='invalid').status_code, 400)
            self.assertEqual(self.settings_change('totp-confirm', code=factor.at(now)).status_code, 200)
            self.assertNotIn(setup.get_json()['secret'], self.client.get('/settings').get_data(as_text=True))
            self.assertEqual(self.settings_change('totp-disable', password='test passphrase',
                                                  otp=factor.at(now)).status_code, 401)
            clock.return_value += 30
            self.assertEqual(self.settings_change('totp-disable', password='test passphrase',
                                                  otp=factor.at(now + 30)).status_code, 200)
            self.assertNotIn('totp_secret', json.loads(self.accounts.read_text())['bob'])
