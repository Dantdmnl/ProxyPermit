import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.web import manager as main


class ProxyApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Path(self.temp.name) / 'tinyproxy.conf'
        self.filter = Path(self.temp.name) / 'filter.txt'
        self.filter.write_text('*.example.com\n')
        self.content = f'Port 3128\nAllow 127.0.0.1\nFilter "{self.filter.as_posix()}"\nFilterType fnmatch\n'
        # Native Tinyproxy paths are Linux absolute paths; Windows tests use an
        # ERE config without a filter for config writes and patch summary paths.
        self.config.write_text('Port 3128\nAllow 127.0.0.1\n')
        self.environment = patch.dict(os.environ, TINYPROXY_CONFIG_PATH=str(self.config), DEPLOYMENT_MODE='standalone', DEMO_MODE='true')
        self.environment.start()
        self.auth = patch.object(main, 'ENABLE_AUTH', False)
        self.auth.start()
        self.client = main.app.test_client()

    def tearDown(self):
        self.auth.stop()
        self.environment.stop()
        self.temp.cleanup()

    def token_and_revision(self):
        data = self.client.get('/api/proxy/config').get_json()
        return {'X-CSRF-Token': data['csrfToken']}, data['revision']

    def test_editor_renders_and_assets_are_separate(self):
        result = self.client.get('/proxy')
        self.assertEqual(result.status_code, 200)
        self.assertIn(b'/static/js/proxy.js', result.data)
        with self.client.get('/static/css/proxy.css') as response:
            self.assertEqual(response.status_code, 200)

    def test_config_requires_login(self):
        with patch.object(main, 'ENABLE_AUTH', True):
            self.assertEqual(self.client.get('/api/proxy/config').status_code, 401)

    def test_write_requires_csrf(self):
        self.assertEqual(self.client.put('/api/proxy/config', json={}).status_code, 403)

    def test_save_and_conflict(self):
        headers, revision = self.token_and_revision()
        body = {'content': 'Port 4000\nAllow 127.0.0.1\n', 'revision': revision}
        self.assertEqual(self.client.put('/api/proxy/config', headers=headers, json=body).status_code, 200)
        self.assertEqual(self.config.read_text(), 'Port 3128\nAllow 127.0.0.1\n')
        self.assertEqual(self.client.put('/api/proxy/config', headers=headers, json=body).status_code, 409)

    def test_discard_keeps_external_live_edit(self):
        headers, revision = self.token_and_revision()
        draft = self.client.put('/api/proxy/config', headers=headers, json={
            'content': 'Port 4000\nAllow 127.0.0.1\n', 'revision': revision,
        }).get_json()
        live = 'Port 5000\nAllow 127.0.0.1\n'
        self.config.write_text(live)
        self.assertEqual(self.client.put('/api/proxy/config', headers=headers, json={
            'content': 'Port 6000\nAllow 127.0.0.1\n', 'revision': draft['revision'],
        }).status_code, 409)
        self.assertEqual(self.client.delete('/api/proxy/config/draft', headers=headers,
                                            json={'revision': draft['revision']}).status_code, 200)
        self.assertEqual(self.client.get('/api/proxy/config').get_json()['content'], live)

    def test_demo_does_not_claim_activation(self):
        headers, revision = self.token_and_revision()
        result = self.client.put('/api/proxy/config', headers=headers,
                                 json={'content': 'Port 4000\nAllow 127.0.0.1\n', 'revision': revision, 'activate': True})
        self.assertEqual(result.status_code, 400)

    def test_new_ha_supports_local_publish_with_legacy_sync_disabled(self):
        with patch.dict(os.environ, DEPLOYMENT_MODE='ha', PROXYPERMIT_MANAGED_HA='true'), \
                patch('app.web.proxy.activation_supported', return_value=True), \
                patch.object(main.whitelist_manager, 'restart_tinyproxy', return_value=(True, None)):
            loaded = self.client.get('/api/proxy/config').get_json()
            self.assertTrue(loaded['canActivate'])
            result = self.client.put('/api/proxy/config', headers={'X-CSRF-Token': loaded['csrfToken']},
                                     json={'content': 'Port 4000\nAllow 127.0.0.1\n',
                                           'revision': loaded['revision'], 'activate': True})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(self.client.get('/api/sync/info').status_code, 409)

    def test_ha_policy_transfers_active_settings_and_rules_once(self):
        self.config.write_text(self.content)
        with self.client.session_transaction() as session:
            session['user'] = 'test-admin'
        with patch.dict(os.environ, DEPLOYMENT_MODE='ha', PROXYPERMIT_MANAGED_HA='true', PROXYPERMIT_HA_NODE='b',
                        NODE0_ADDRESS='192.0.2.11', NODE1_ADDRESS='192.0.2.12', VIP_ADDRESS='192.0.2.10',
                        SESSION_STATE_FILE=str(self.config.with_name('session.json'))), \
                patch('app.web.proxy.activation_supported', return_value=True), \
                patch.object(main.whitelist_manager, 'whitelist_path', self.filter), \
                patch.object(main.whitelist_manager, 'restart_tinyproxy', return_value=(True, None)) as restart:
            headers, _ = self.token_and_revision()

            def post(action, **body):
                return self.client.post('/api/proxy/ha-policy/' + action, headers=headers, json=body)

            invite = post('invite').get_json()['code']
            self.config.write_text(self.content.replace('Port 3128', 'Port 4000') + 'Timeout 750\n')
            self.filter.write_text('*.new.example\n')
            with patch.dict(os.environ, PROXYPERMIT_HA_NODE='a'):
                code = post('export', code=invite).get_json()['code']
            self.config.write_text(self.content)
            self.filter.write_text('*.example.com\n')
            self.assertEqual(post('preview', code=code.replace('750', '751')).status_code, 400)
            preview = post('preview', code=code).get_json()
            self.assertTrue(preview['configChanged'])
            self.assertTrue(preview['filtersChanged'])
            restart.assert_not_called()
            self.assertEqual(post('apply', code=code, confirmed=True).status_code, 400)
            body = dict(code=code, confirmed=True, revision=preview['revision'])
            self.assertEqual(post('apply', **body).status_code, 200)
            self.assertIn('Port 3128', self.config.read_text())
            self.assertIn('Timeout 750', self.config.read_text())
            self.assertEqual(self.filter.read_text(), '*.new.example\n')
            restart.assert_called_once()
            self.assertEqual(post('apply', **body).status_code, 400)

            target_config = self.config.read_text()
            invite = post('invite').get_json()['code']
            self.config.write_text(target_config.replace('Timeout 750', 'Timeout 900'))
            self.filter.write_text('*.rollback.example\n')
            with patch.dict(os.environ, PROXYPERMIT_HA_NODE='a'):
                code = post('export', code=invite).get_json()['code']
            self.config.write_text(target_config)
            self.filter.write_text('*.new.example\n')
            preview = post('preview', code=code).get_json()
            restart.side_effect = [(False, 'test failure'), (True, None)]
            self.assertEqual(post('apply', code=code, confirmed=True, revision=preview['revision']).status_code, 400)
            self.assertEqual(self.config.read_text(), target_config)
            self.assertEqual(self.filter.read_text(), '*.new.example\n')

    def test_service_restart_cannot_activate_staged_config(self):
        headers, revision = self.token_and_revision()
        result = self.client.put('/api/proxy/config', headers=headers,
                                 json={'content': 'Port 4000\nAllow 127.0.0.1\n', 'revision': revision})
        self.assertEqual(result.status_code, 200)
        with patch.object(main, 'restart_tinyproxy_async') as restart:
            self.assertEqual(self.client.post('/api/service/restart').status_code, 409)
            restart.assert_not_called()

    def test_ha_replication_redeems_code_once_and_uses_a_separate_peer_key(self):
        self.config.write_text(self.content)
        with self.client.session_transaction() as session:
            session['user'] = 'test-admin'
        with patch.dict(os.environ, DEPLOYMENT_MODE='ha', PROXYPERMIT_MANAGED_HA='true', PROXYPERMIT_HA_NODE='a',
                        NODE0_ADDRESS='192.0.2.11', NODE1_ADDRESS='192.0.2.12', VIP_ADDRESS='192.0.2.10',
                        HA_POLICY_TLS_PIN='a' * 64, SESSION_STATE_FILE=str(self.config.with_name('session.json'))), \
                patch('app.web.proxy.activation_supported', return_value=True), \
                patch.object(main.whitelist_manager, 'whitelist_path', self.filter):
            headers, _ = self.token_and_revision()
            issued = self.client.post('/api/proxy/ha-replication/invite', headers=headers, json={})
            self.assertEqual(issued.status_code, 200)
            import json
            invitation = json.loads(issued.get_json()['code'])
            token = {'Authorization': 'Bearer ' + invitation['key']}
            self.assertEqual(self.client.get('/internal/ha/policy', headers=token).status_code, 403)
            from concurrent.futures import ThreadPoolExecutor
            from threading import Barrier
            barrier = Barrier(2)
            def redeem(_):
                with main.app.test_client() as client:
                    barrier.wait(timeout=5)
                    return client.post('/internal/ha/enroll', headers=token)
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(redeem, range(2)))
            self.assertEqual(sorted(response.status_code for response in responses), [200, 403])
            enrolled = next(response for response in responses if response.status_code == 200)
            peer = {'Authorization': 'Bearer ' + enrolled.get_json()['key']}
            self.assertEqual(self.client.post('/internal/ha/enroll', headers=token).status_code, 403)
            self.assertEqual(self.client.get('/internal/ha/policy', headers=peer).get_json()['filters'], '*.example.com\n')
            self.assertEqual(self.client.post('/api/proxy/ha-replication/disconnect', headers=headers, json={}).status_code, 200)
            self.assertEqual(self.client.get('/internal/ha/policy', headers=peer).status_code, 403)

    def test_standalone_status_does_not_inspect_peers(self):
        result = self.client.get('/api/status').get_json()
        self.assertEqual(result['configured_role'], 'STANDALONE')
        self.assertIsNone(result['peer_address'])

    def test_invalid_json_and_non_string_are_rejected(self):
        headers, _ = self.token_and_revision()
        self.assertEqual(self.client.post('/api/proxy/config/validate', headers=headers, json={'content': []}).status_code, 400)

    def test_native_filter_uses_revision_and_preserves_patterns(self):
        headers, _ = self.token_and_revision()
        native = dict(filterMode='blocklist', filterType='fnmatch', filterURLs=False, filterPath=str(self.filter))
        with patch.object(main.whitelist_manager, 'whitelist_path', self.filter), patch('app.web.proxy.summary', return_value=native):
            loaded = self.client.get('/api/proxy/filter').get_json()
            result = self.client.put('/api/proxy/filter', headers=headers,
                                     json={'content': '*tracker*\n', 'revision': loaded['revision']})
            self.assertEqual(result.status_code, 200, result.get_json())
            self.assertEqual(self.filter.read_text(), '*.example.com\n')
            self.assertEqual(self.client.get('/api/proxy/filter').get_json()['content'], '*tracker*\n')
            with patch('app.web.proxy.activation_supported', return_value=True), patch.object(main.whitelist_manager, 'restart_tinyproxy', return_value=(True, None)):
                revision = self.client.get('/api/proxy/config').get_json()['revision']
                result = self.client.put('/api/proxy/config', headers=headers,
                                         json={'content': self.config.read_text(), 'revision': revision, 'activate': True})
            self.assertEqual(result.status_code, 200, result.get_json())
            self.assertEqual(self.filter.read_text(), '*tracker*\n')

    def test_incompatible_filter_blocks_domain_conversion(self):
        result = self.client.post('/api/entries', json={'content': 'example.com'})
        self.assertEqual(result.status_code, 409)

    def test_activation_failure_restores_pending_filter_too(self):
        headers, _ = self.token_and_revision()
        native = dict(filterMode='blocklist', filterType='fnmatch', filterURLs=False, filterPath=str(self.filter))
        with patch.object(main.whitelist_manager, 'whitelist_path', self.filter), patch('app.web.proxy.summary', return_value=native):
            loaded = self.client.get('/api/proxy/filter').get_json()
            self.client.put('/api/proxy/filter', headers=headers,
                            json={'content': '*tracker*\n', 'revision': loaded['revision']})
            revision = self.client.get('/api/proxy/config').get_json()['revision']
            with patch('app.web.proxy.activation_supported', return_value=True), patch.object(main.whitelist_manager, 'restart_tinyproxy', side_effect=[(False, 'failed'), (True, None)]):
                result = self.client.put('/api/proxy/config', headers=headers,
                                         json={'content': 'Port 4000\nAllow 127.0.0.1\n', 'revision': revision, 'activate': True})
            self.assertEqual(result.status_code, 400)
            self.assertEqual(self.filter.read_text(), '*.example.com\n')
            self.assertEqual(self.config.read_text(), 'Port 3128\nAllow 127.0.0.1\n')


if __name__ == '__main__':
    unittest.main()
