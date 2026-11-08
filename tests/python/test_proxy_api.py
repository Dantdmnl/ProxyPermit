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

    def test_service_restart_cannot_activate_staged_config(self):
        headers, revision = self.token_and_revision()
        result = self.client.put('/api/proxy/config', headers=headers,
                                 json={'content': 'Port 4000\nAllow 127.0.0.1\n', 'revision': revision})
        self.assertEqual(result.status_code, 200)
        with patch.object(main, 'restart_tinyproxy_async') as restart:
            self.assertEqual(self.client.post('/api/service/restart').status_code, 409)
            restart.assert_not_called()

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
