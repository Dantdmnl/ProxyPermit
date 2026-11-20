"""Exercise the machine protocol, overlapping requests and interrupted handover."""
from concurrent.futures import ThreadPoolExecutor
import json
import http.client
import os
from pathlib import Path
import tempfile
import time
from threading import Barrier
import unittest
from unittest.mock import patch, MagicMock

from app.web import manager as main, ha_replication as replication


class PeerKeyApiTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.old = 'a' * 64
        values = dict(DEPLOYMENT_MODE='ha', PROXYPERMIT_MANAGED_HA='true', PROXYPERMIT_HA_NODE='a',
                      NODE0_ADDRESS='192.0.2.11', NODE1_ADDRESS='192.0.2.12', VIP_ADDRESS='192.0.2.10',
                      SESSION_STATE_FILE=str(self.root / 'session.json'))
        for item in (patch.dict(os.environ, values), patch.object(main, 'ENABLE_AUTH', False),
                     patch.object(replication, 'CERTIFICATE_TRANSITION', self.root / 'certificate-transition.json'),
                     patch('app.web.proxy.activation_supported', return_value=True)):
            item.start()
            self.addCleanup(item.stop)
        self.client = main.app.test_client()
        with self.client.session_transaction() as session:
            session['user'] = 'isolated-test-admin'
            session['proxy_csrf'] = 'isolated-csrf'
        replication.write_state('peer', {'key': self.old})
        replication.snapshot('Port 3128\nAllow 127.0.0.1\n', '^example\\.invalid$\n')

    def policy(self, token, version='', protocol='1'):
        return self.client.get('/internal/ha/policy', headers={'Authorization': 'Bearer ' + token,
            'X-HA-Credential-Version': version, 'X-HA-Credential-Protocol': protocol})

    def rotate(self):
        with patch.dict(main.app.extensions, {'reauthenticate_admin_change': lambda _data: None}):
            return self.client.post('/api/proxy/ha-replication/rotate-key', json={'password': 'fixture'},
                                    headers={'X-CSRF-Token': 'isolated-csrf'})

    def commit(self, offer):
        return self.client.post('/internal/ha/credential-commit', json={'id': offer['id']},
                                headers={'Authorization': 'Bearer ' + offer['key']})

    def test_protocol_is_verified_before_rotation_and_keys_are_not_in_browser_status(self):
        self.assertEqual(self.rotate().status_code, 409)
        self.assertEqual(replication.read_state('peer'), {'key': self.old})
        self.assertEqual(self.policy(self.old).status_code, 200)
        self.assertEqual(self.rotate().status_code, 200)
        offered = self.policy(self.old).get_json()['credentialRotation']
        self.assertEqual(self.policy(offered['key']).status_code, 403)
        status = self.client.post('/api/proxy/ha-replication/status', json={},
                                  headers={'X-CSRF-Token': 'isolated-csrf'}).get_data(as_text=True)
        self.assertNotIn(self.old, status)
        self.assertNotIn(offered['key'], status)
        self.assertEqual(self.commit(offered).status_code, 200)
        self.assertEqual(self.policy(self.old).status_code, 403)
        self.assertEqual(self.rotate().status_code, 409)
        self.assertEqual(self.policy(offered['key'], offered['id']).status_code, 200)
        self.assertEqual(replication.read_state('peer')['rotation']['state'], 'complete')

    def test_rotation_requires_real_reauthentication_by_default(self):
        self.policy(self.old)
        result = self.client.post('/api/proxy/ha-replication/rotate-key', json={'password': 'fixture'},
                                  headers={'X-CSRF-Token': 'isolated-csrf'})
        self.assertEqual(result.status_code, 403)
        self.assertNotIn('rotation', replication.read_state('peer'))

    def test_certificate_transition_blocks_key_rotation_until_acknowledged(self):
        self.policy(self.old)
        certificate = dict(id='c' * 32, phase='offered', expires=int(time.time()) + 600)
        with patch.object(replication, 'certificate_transition', return_value=certificate):
            self.assertEqual(self.rotate().status_code, 409)
            self.assertNotIn('rotation', replication.read_state('peer'))
            certificate['phase'] = 'committed'
            self.assertEqual(self.rotate().status_code, 409)
            peer = replication.read_state('peer')
            replication.write_state('peer', {**peer, 'certificateVersion': certificate['id']})
            self.assertEqual(self.rotate().status_code, 200)

    def test_two_starts_create_one_transition(self):
        self.policy(self.old)
        barrier = Barrier(2)
        def start(_):
            with main.app.test_client() as client:
                with client.session_transaction() as session:
                    session['user'] = 'isolated-test-admin'
                    session['proxy_csrf'] = 'isolated-csrf'
                barrier.wait(timeout=5)
                return client.post('/api/proxy/ha-replication/rotate-key', json={'password': 'fixture'},
                                   headers={'X-CSRF-Token': 'isolated-csrf'}).status_code
        with patch.dict(main.app.extensions, {'reauthenticate_admin_change': lambda _data: None}), ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(start, range(2))), [200, 409])

    def test_lost_commit_reply_leaves_staged_key_recoverable(self):
        self.policy(self.old)
        self.assertEqual(self.rotate().status_code, 200)
        offer = self.policy(self.old).get_json()['credentialRotation']
        self.assertEqual(self.commit(offer).status_code, 200)
        primary_state = str(self.root / 'session.json')
        standby = self.root / 'standby'
        standby.mkdir()
        def commit_on_primary(_pin, token, _action, payload):
            with patch.dict(os.environ, SESSION_STATE_FILE=primary_state):
                result = self.client.post('/internal/ha/credential-commit', json=payload,
                                          headers={'Authorization': 'Bearer ' + token})
                self.assertEqual(result.status_code, 200)
                return result.get_json()
        with patch.dict(os.environ, SESSION_STATE_FILE=str(standby / 'session.json')):
            staged = {'key': self.old, 'pin': 'b' * 64, 'rotation': offer}
            replication.write_state('peer', staged)
            with patch.object(replication, 'fetch_primary', side_effect=OSError('lost response')):
                with self.assertRaises(OSError):
                    replication.finish_key_rotation(staged)
            self.assertEqual(replication.read_state('peer'), staged)
            with patch.object(replication, 'fetch_primary', side_effect=commit_on_primary):
                recovered = replication.finish_key_rotation(staged)
            self.assertEqual(recovered['key'], offer['key'])
            self.assertNotIn('rotation', recovered)
        self.assertEqual(self.policy(self.old).status_code, 403)
        self.assertEqual(self.policy(offer['key'], offer['id']).status_code, 200)

    def test_payload_revision_rejects_truncation_or_changed_policy(self):
        policy = replication.read_state('published-policy')
        replication.validate_published(policy)
        for changed in ({**policy, 'filters': 'different'}, {**policy, 'revision': 'bad'}, [], {'cluster': policy['cluster']}):
            with self.assertRaises(ValueError):
                replication.validate_published(changed)

    def test_incomplete_http_body_is_redacted_and_connection_closed(self):
        connection = MagicMock()
        response = connection.getresponse.return_value
        response.status = 200
        response.read.side_effect = http.client.IncompleteRead(b'private rotation offer', 100)
        with patch.object(replication, 'pinned_connection', return_value=connection):
            with self.assertRaises(ValueError) as error:
                replication.fetch_primary('b' * 64, self.old, 'policy')
        self.assertNotIn('private rotation offer', str(error.exception))
        connection.close.assert_called_once()
