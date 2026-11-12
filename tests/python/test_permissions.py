"""Browser role enforcement, privilege boundaries and revocation."""
import ast
import json
import os
import tempfile
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash
import pyotp

from app.core.accounts import account_role, admin_count
from app.web import manager
from app.web.permissions import READ, PROXY, ADMIN, SELF


class PermissionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.accounts = self.root / 'accounts.json'
        hashed = generate_password_hash('role test passphrase')
        self.data = {role: dict(password_hash=hashed, revision=role + '-v1', role=role)
                     for role in ('admin', 'manager', 'monitor')}
        self.accounts.write_text(json.dumps(self.data))
        self.config = self.root / 'tinyproxy.conf'
        self.config.write_text('Port 3128\nAllow 127.0.0.1\n')
        self.filter = self.root / 'filter.txt'
        self.filter.write_text('# isolated role fixture\n')
        env = patch.dict(os.environ, TINYPROXY_CONFIG_PATH=str(self.config),
                         PROXYPERMIT_ACCESS_SAFETY_FILE=str(self.root / 'safety.json'),
                         DEMO_MODE='true', DEPLOYMENT_MODE='standalone')
        env.start()
        self.addCleanup(env.stop)
        state = patch.multiple(manager, ENABLE_AUTH=True, AUTH_ACCOUNTS_FILE=str(self.accounts),
                               SESSION_STATE_FILE=str(self.root / 'session.json'), AUTH_LOGIN_RATE_LIMIT_ENABLED=False,
                               WHITELIST_PATH=str(self.filter))
        state.start()
        self.addCleanup(state.stop)
        filters = patch.object(manager.whitelist_manager, 'whitelist_path', self.filter)
        filters.start()
        self.addCleanup(filters.stop)

    def login(self, role):
        client = manager.app.test_client()
        self.assertEqual(client.post('/api/auth/login', json=dict(username=role, password='role test passphrase')).status_code, 200)
        client.get('/settings?lang=en')
        with client.session_transaction() as session:
            session['proxy_csrf'] = session['settings_csrf'] = 'isolated-csrf'
        return client

    def account_change(self, client, action, **data):
        return client.post('/api/settings/accounts', headers={'X-CSRF-Token': 'isolated-csrf'},
                           json=dict(action=action, password='role test passphrase', **data))

    def test_monitor_reads_status_and_logs_but_cannot_access_raw_config_or_mutate_policy(self):
        client = self.login('monitor')
        self.assertEqual(client.get('/api/entries').status_code, 200)
        self.assertFalse(client.get('/api/entries').json['canEditRules'])
        self.assertEqual(client.get('/api/logs').status_code, 200)
        self.assertEqual(client.get('/api/auth/me').json['role'], 'monitor')
        for path in ('/api/proxy/config', '/api/proxy/filter', '/api/settings/ha/network', '/api/export', '/api/export?format=raw'):
            self.assertEqual(client.get(path).status_code, 403, path)
        requests = [('POST', '/api/entries'), ('PUT', '/api/entries/0'), ('DELETE', '/api/entries/0'),
                    ('POST', '/api/entries/reorder'), ('POST', '/api/import'), ('POST', '/api/service/restart'),
                    ('PUT', '/api/proxy/config'), ('DELETE', '/api/proxy/config/draft'),
                    ('PUT', '/api/proxy/filter'), ('POST', '/api/sync/run'), ('POST', '/api/sync/merge-apply')]
        before = self.filter.read_bytes(), self.config.read_bytes()
        for method, path in requests:
            response = client.open(path, method=method, json={}, headers={'X-CSRF-Token': 'isolated-csrf'})
            self.assertEqual(response.status_code, 403, path)
            self.assertEqual(response.json['error'], 'Your role does not permit this action.', path)
        self.assertEqual(before, (self.filter.read_bytes(), self.config.read_bytes()))
        settings = client.get('/settings').get_data(as_text=True)
        self.assertNotIn('id="accounts-title"', settings)
        self.assertNotIn('id="haPreviewForm"', settings)
        self.assertNotIn('href="/proxy"', settings)

    def test_manager_saves_config_but_cannot_manage_ha_or_other_accounts(self):
        client = self.login('manager')
        self.assertEqual(client.get('/api/export').status_code, 200)
        config = client.get('/api/proxy/config').json
        response = client.put('/api/proxy/config', headers={'X-CSRF-Token': 'isolated-csrf'},
                              json=dict(content=config['content'] + 'Timeout 500\n', revision=config['revision']))
        self.assertEqual(response.status_code, 200)
        for action in ('create', 'remove', 'role'):
            response = self.account_change(client, action, username='monitor', newPassword='another passphrase', role='admin')
            self.assertEqual(response.status_code, 403, action)
        for path in ('/api/settings/ha/preview', '/api/settings/ha/setup', '/api/settings/ha/retire',
                     '/api/proxy/ha-replication/invite', '/api/proxy/ha-replication/disconnect',
                     '/api/proxy/ha-policy/apply', '/api/auth/force-logout'):
            self.assertEqual(client.post(path, headers={'X-CSRF-Token': 'isolated-csrf'}, json={}).status_code, 403, path)
        self.assertEqual(json.loads(self.accounts.read_text()), self.data)

    def test_low_roles_can_change_only_their_own_password_without_escalating(self):
        for role in ('manager', 'monitor'):
            client = self.login(role)
            self.assertEqual(self.account_change(client, 'password', username='admin', role='admin',
                                                newPassword='new personal passphrase').status_code, 200)
            accounts = json.loads(self.accounts.read_text())
            self.assertEqual(accounts['admin'], self.data['admin'])
            self.assertEqual(accounts[role]['role'], role)

    def test_manager_cannot_change_host_settings_or_activate_an_admin_host_draft(self):
        client = self.login('manager')
        before = self.config.read_text()
        for change in ('User root\n', 'Group root\n', 'LogFile /tmp/forbidden-log\n',
                       'PidFile /tmp/forbidden-pid\n', 'Listen 127.0.0.2\n'):
            config = client.get('/api/proxy/config').json
            response = client.put('/api/proxy/config', headers={'X-CSRF-Token': 'isolated-csrf'},
                                  json=dict(content=before + change, revision=config['revision']))
            self.assertEqual(response.status_code, 403)
            self.assertEqual(self.config.read_text(), before)
            self.assertFalse(client.get('/api/proxy/config').json['staged'])
        admin = self.login('admin')
        config = admin.get('/api/proxy/config').json
        self.assertEqual(admin.put('/api/proxy/config', headers={'X-CSRF-Token': 'isolated-csrf'},
                                  json=dict(content=before + 'User tinyproxy\n', revision=config['revision'])).status_code, 200)
        pending = client.get('/api/proxy/config').json
        with patch('app.web.proxy.activation_supported', return_value=True), \
                patch.object(manager.whitelist_manager, 'restart_tinyproxy') as restart:
            response = client.put('/api/proxy/config', headers={'X-CSRF-Token': 'isolated-csrf'},
                                  json=dict(content=pending['content'], revision=pending['revision'], activate=True))
            self.assertEqual(response.status_code, 403)
            restart.assert_not_called()
        self.assertEqual(client.delete('/api/proxy/config/draft', headers={'X-CSRF-Token': 'isolated-csrf'},
                                       json=dict(revision=pending['revision'])).status_code, 403)
        self.assertEqual(self.config.read_text(), before)
        self.assertTrue(client.get('/api/proxy/config').json['staged'])

    def test_admin_role_change_revokes_target_sessions_and_preserves_account_secrets(self):
        admin, other = self.login('admin'), self.login('manager')
        self.assertEqual(self.account_change(admin, 'role', username='manager', role='monitor').status_code, 200)
        updated = json.loads(self.accounts.read_text())['manager']
        self.assertEqual(updated['password_hash'], self.data['manager']['password_hash'])
        self.assertEqual(updated['role'], 'monitor')
        self.assertNotEqual(updated['revision'], self.data['manager']['revision'])
        self.assertEqual(other.get('/api/config').status_code, 401)
        self.assertEqual(self.account_change(admin, 'role', username='admin', role='monitor').status_code, 409)
        self.assertEqual(self.account_change(admin, 'remove', username='admin').status_code, 409)
        self.assertEqual(admin_count(json.loads(self.accounts.read_text())), 1)
        self.assertEqual(self.account_change(admin, 'create', username='new-monitor', newPassword='new monitor passphrase').status_code, 200)
        self.assertEqual(json.loads(self.accounts.read_text())['new-monitor']['role'], 'monitor')

    def test_missing_roles_preserve_existing_admins_and_invalid_roles_cannot_sign_in(self):
        self.assertEqual(account_role({'revision': 'old'}), 'admin')
        for role in (None, '', 'ADMIN', [], {}, True):
            self.data['monitor']['role'] = role
            self.accounts.write_text(json.dumps(self.data))
            client = manager.app.test_client()
            self.assertEqual(client.post('/api/auth/login', json=dict(username='monitor', password='role test passphrase')).status_code, 401)

    def test_monitor_can_enable_optional_2fa_for_own_account(self):
        client = self.login('monitor')
        setup = self.account_change(client, 'totp-start')
        self.assertEqual(setup.status_code, 200)
        secret = setup.json['secret']
        code = pyotp.TOTP(secret).now()
        self.assertEqual(self.account_change(client, 'totp-confirm', code=code).status_code, 200)
        current = json.loads(self.accounts.read_text())
        self.assertEqual(current['monitor']['role'], 'monitor')
        self.assertEqual(current['monitor']['totp_secret'], secret)
        self.assertEqual(current['admin'], self.data['admin'])
        fresh = pyotp.TOTP(secret).at(time.time() + 30)
        self.assertEqual(self.account_change(client, 'totp-disable', otp=fresh).status_code, 200)

    def test_cli_preserves_last_valid_admin_and_role_changes_revoke_sessions(self):
        command = [sys.executable, str(Path(__file__).resolve().parents[2] / 'scripts/tools/accounts.py'),
                   '--file', str(self.accounts)]
        before = self.accounts.read_bytes()
        for args in (['role', 'admin', 'monitor'], ['remove', 'admin']):
            result = subprocess.run(command + args, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(self.accounts.read_bytes(), before)
        result = subprocess.run(command + ['role', 'monitor', 'manager'], capture_output=True, check=False)
        self.assertEqual(result.returncode, 0)
        updated = json.loads(self.accounts.read_text())['monitor']
        self.assertEqual(updated['role'], 'manager')
        self.assertNotEqual(updated['revision'], self.data['monitor']['revision'])
        self.assertEqual(updated['password_hash'], self.data['monitor']['password_hash'])
        self.assertEqual(admin_count({'broken': {}, 'admin': self.data['admin']}), 1)

    def test_every_authenticated_browser_endpoint_has_an_explicit_permission(self):
        root = Path(__file__).resolve().parents[2]
        configured = READ | PROXY | ADMIN | SELF | {'proxy.manage_replication'}
        for name, prefix in (('manager', ''), ('proxy', 'proxy.'), ('settings', 'settings.')):
            tree = ast.parse((root / 'app/web' / (name + '.py')).read_text(encoding='utf-8'))
            for function in ast.walk(tree):
                if isinstance(function, ast.FunctionDef) and any(isinstance(item, ast.Name) and item.id == 'requires_auth' for item in function.decorator_list):
                    self.assertIn(prefix + function.name, configured)
