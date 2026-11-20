"""HTTPS controls require admin, fresh credentials, CSRF and bounded root jobs."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
from threading import Barrier
import time
import unittest
from unittest.mock import patch

import pyotp
from werkzeug.security import generate_password_hash
from app.core import management_https as control
from app.web import manager
from scripts.deploy import management_tls_web as helper, management_tls as tls

RUN_PROCESS = subprocess.run


class HttpsWebTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.accounts = self.root / 'accounts.json'
        hashed = generate_password_hash('isolated HTTPS password')
        self.data = {role: dict(password_hash=hashed, revision=role + '-v1', role=role) for role in ('admin', 'manager', 'monitor')}
        self.accounts.write_text(json.dumps(self.data))
        self.state = dict(revision='a' * 64, names=['DNS:localhost', 'IP:127.0.0.1'], managed=True,
                          canEnable=False, canRenew=True, node='isolated-node', role='standalone', certificate=None)
        for item in (patch.multiple(manager, ENABLE_AUTH=True, AUTH_ACCOUNTS_FILE=str(self.accounts),
                        SESSION_STATE_FILE=str(self.root / 'session.json'), AUTH_LOGIN_RATE_LIMIT_ENABLED=False),
                     patch.object(control, 'REQUEST', self.root / 'request.json'),
                     patch.object(control, 'available', return_value=True),
                     patch.object(control, 'snapshot', side_effect=lambda: dict(self.state)),
                     patch.object(control, 'result', return_value=None),
                     patch.object(control, 'unit_busy', return_value=False),
                     patch('app.web.https_settings.subprocess.run')):
            item.start(); self.addCleanup(item.stop)

    def login(self, role='admin', **fields):
        client = manager.app.test_client()
        self.assertEqual(client.post('/api/auth/login', json=dict(username=role, password='isolated HTTPS password', **fields)).status_code, 200)
        with client.session_transaction() as session:session['settings_csrf'] = 'isolated-https-csrf'
        return client

    def post(self, client, **fields):
        return client.post('/api/settings/https/jobs', json=dict(action='renew', revision=self.state['revision'],
            confirmed=True, password='isolated HTTPS password', **fields), headers={'X-CSRF-Token': 'isolated-https-csrf'})

    def test_proxy_managers_and_monitors_cannot_read_or_change_https(self):
        for role in ('manager', 'monitor'):
            client = self.login(role)
            self.assertEqual(client.get('/api/settings/https').status_code, 403)
            self.assertEqual(client.get('/api/settings/https/ca').status_code, 403)
            self.assertEqual(self.post(client).status_code, 403)
        self.assertFalse(control.REQUEST.exists())

    def test_csrf_confirmation_password_and_revision_are_required_without_persisting_secrets(self):
        client = self.login()
        url = '/api/settings/https/jobs'
        body = dict(action='renew', revision=self.state['revision'], confirmed=True, password='isolated HTTPS password')
        self.assertEqual(client.post(url, json=body).status_code, 403)
        for changed, expected in ((dict(confirmed=False), 400), (dict(password='wrong'), 401), (dict(revision='b' * 64), 409)):
            response = client.post(url, json={**body, **changed}, headers={'X-CSRF-Token': 'isolated-https-csrf'})
            self.assertEqual(response.status_code, expected)
        self.assertFalse(control.REQUEST.exists())
        self.assertEqual(self.post(client).status_code, 202)
        stored = control.REQUEST.read_text()
        self.assertNotIn('password', stored); self.assertNotIn('otp', stored)
        self.assertEqual(json.loads(stored)['username'], 'admin')
        self.assertEqual(self.post(client).status_code, 409)

    def test_fresh_optional_totp_and_trust_acknowledgement(self):
        secret = pyotp.random_base32(); self.data['admin']['totp_secret'] = secret
        self.accounts.write_text(json.dumps(self.data))
        client = self.login(otp=pyotp.TOTP(secret).now())
        self.assertEqual(self.post(client).status_code, 401)
        otp = pyotp.TOTP(secret).at(time.time() + 30)
        self.assertEqual(self.post(client, otp=otp).status_code, 202)
        control.REQUEST.unlink()
        self.assertEqual(self.post(client, otp=otp).status_code, 401)
        response = client.post('/api/settings/https/jobs', json=dict(action='activate', confirmed=True,
            password='isolated HTTPS password', revision='a' * 64, plan='b' * 32), headers={'X-CSRF-Token': 'isolated-https-csrf'})
        self.assertEqual(response.status_code, 400)

    def test_role_revocation_and_insecure_remote_access_fail_closed(self):
        client = self.login()
        self.assertEqual(client.get('/api/settings/https', environ_overrides={'REMOTE_ADDR': '192.0.2.30'}).status_code, 403)
        self.data['admin']['role'] = 'manager'; self.data['admin']['revision'] = 'downgraded'
        self.accounts.write_text(json.dumps(self.data))
        self.assertIn(self.post(client).status_code, (401, 403))
        self.assertFalse(control.REQUEST.exists())

    def test_concurrent_requests_queue_only_one_job(self):
        client = self.login(); cookie = client.get_cookie(manager.app.config['SESSION_COOKIE_NAME'])
        barrier = Barrier(2)
        def queue(_):
            with manager.app.test_client() as other:
                other.set_cookie(manager.app.config['SESSION_COOKIE_NAME'], cookie.value)
                barrier.wait(timeout=5)
                return self.post(other).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(queue, range(2))), [202, 409])

    def test_completed_result_cannot_queue_while_root_unit_is_still_exiting(self):
        client = self.login()
        with patch.object(control, 'result', return_value={'phase': 'complete'}), \
                patch.object(control, 'unit_busy', return_value=True):
            self.assertEqual(self.post(client).status_code, 409)
        self.assertFalse(control.REQUEST.exists())

    def test_installer_assets_import_without_site_dependencies(self):
        code = 'from pathlib import Path; from scripts.deploy.management_tls_web import assets; assert len(assets(Path("/opt/proxypermit"))) == 3'
        result = RUN_PROCESS([sys.executable, '-S', '-c', code], cwd=Path(__file__).resolve().parents[2],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_activation_rejects_a_management_address_not_covered_by_the_plan(self):
        client = self.login()
        self.state.update(canEnable=True, canRenew=False, managed=False, names=['IP:127.0.0.1'])
        body = dict(action='activate', confirmed=True, trusted=True, password='isolated HTTPS password',
                    revision=self.state['revision'], plan='b' * 32)
        headers = {'X-CSRF-Token': 'isolated-https-csrf'}
        self.assertEqual(client.post('/api/settings/https/jobs', json=body, headers=headers).status_code, 400)
        self.assertFalse(control.REQUEST.exists())
        self.state['names'].append('DNS:localhost')
        self.assertEqual(client.post('/api/settings/https/jobs', json=body, headers=headers).status_code, 202)


@unittest.skipUnless(os.name == 'posix' and getattr(os, 'geteuid', lambda: -1)() == 0, 'Root-owned helper fixtures')
class HttpsRootBoundaryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.request = self.root / 'request.json'
        self.body = dict(id='a' * 32, action='renew', revision='b' * 64, plan='', username='admin', accountRevision='v1', created=int(time.time()))
        self.request.write_text(json.dumps(self.body)); self.request.chmod(0o600)
        item = patch.object(control, 'REQUEST', self.request); item.start(); self.addCleanup(item.stop)

    def test_helper_rejects_paths_unknown_fields_links_stale_files_and_wrong_owners(self):
        self.assertEqual(helper.read_request(0)['action'], 'renew')
        for altered in ({**self.body, 'action': 'shell'}, {**self.body, 'path': '/etc/shadow'},
                        {**self.body, 'created': int(time.time()) - 180}, {**self.body, 'plan': '../../etc'}):
            self.request.write_text(json.dumps(altered))
            with self.assertRaises(ValueError):helper.read_request(0)
        self.request.write_text(json.dumps(self.body))
        with self.assertRaises(ValueError):helper.read_request(12345)
        self.request.chmod(0o644)
        with self.assertRaises(ValueError):helper.read_request(0)
        self.request.unlink();self.request.symlink_to('/etc/passwd')
        with self.assertRaises(OSError):helper.read_request(0)

    def test_root_rechecks_admin_revision_and_rejects_other_roles(self):
        accounts = self.root / 'accounts.json'
        accounts.write_text(json.dumps({'admin': {'role': 'manager', 'revision': 'v1'}}));accounts.chmod(0o600)
        env = self.root / '.env';env.write_text('AUTH_ACCOUNTS_FILE=' + str(accounts));env.chmod(0o600)
        with patch.object(tls, 'ENVIRONMENT', env):
            with self.assertRaises(ValueError):helper.verify_actor(self.body, 0)
            accounts.write_text(json.dumps({'admin': {'role': 'admin', 'revision': 'newer'}}))
            with self.assertRaises(ValueError):helper.verify_actor(self.body, 0)
            accounts.write_text(json.dumps({'admin': {'role': 'admin', 'revision': 'v1'}}))
            helper.verify_actor(self.body, 0)

    def test_preparation_is_non_disruptive_and_activation_requires_the_same_fresh_plan(self):
        certificate_folder = self.root / 'certificates'
        env = self.root / '.env';env.write_text('MANAGER_SCHEME=http\n');env.chmod(0o600)
        state = dict(revision='b' * 64, names=['DNS:localhost', 'IP:127.0.0.1', 'IP:::1'], canEnable=True, canRenew=False)
        request = {**self.body, 'action': 'prepare'}
        with patch.object(tls, 'FOLDER', certificate_folder), patch.object(tls, 'ENVIRONMENT', env), \
                patch.object(tls.deploy, 'maintenance_lock', side_effect=lambda: nullcontext()), \
                patch.object(control, 'snapshot', side_effect=lambda: dict(state)), \
                patch.object(helper, 'verify_actor'), patch.object(tls, 'audit'), patch.object(tls, 'activate') as activate:
            outcome = helper.execute(request, self.root, 0, 0)
            self.assertEqual(outcome['phase'], 'prepared')
            self.assertEqual(env.read_text(), 'MANAGER_SCHEME=http\n')
            activate.assert_not_called()
            path = certificate_folder / 'web-plan.json'
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            plan = json.loads(path.read_text())
            request.update(action='activate', plan=plan['id'])
            state['revision'] = 'c' * 64
            with self.assertRaises(ValueError):helper.execute(request, self.root, 0, 0)
            activate.assert_not_called()
            state['revision'] = request['revision']
            old_expiry = plan['expires'];plan['expires'] = int(time.time()) - 1
            tls.write(path, json.dumps(plan))
            with self.assertRaises(ValueError):helper.execute(request, self.root, 0, 0)
            activate.assert_not_called()
            plan['expires'] = old_expiry;tls.write(path, json.dumps(plan))
            activate.return_value = plan['profile']
            self.assertEqual(helper.execute(request, self.root, 0, 0)['phase'], 'complete')
            self.assertEqual(activate.call_args.kwargs['candidate'], plan['profile'])
            self.assertEqual(activate.call_args.kwargs['requester']['username'], 'admin')
            self.assertFalse(path.exists())


if __name__ == '__main__':unittest.main()
