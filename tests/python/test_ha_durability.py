"""Never acknowledge credential changes before their directory entries persist."""
import errno
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.core.files import DurabilityError
from app.core.tinyproxy import atomic_write, ConfigError
from app.web import ha_replication as replication
import test_ha_key_api as key_api


class AtomicDurabilityTests(unittest.TestCase):
    def test_file_sync_failure_preserves_original_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            path.write_text('original')
            with patch('app.core.tinyproxy.os.fsync', side_effect=OSError(errno.ENOSPC, 'test disk full')):
                with self.assertRaises(OSError):
                    atomic_write(path, 'replacement')
            self.assertEqual(path.read_text(), 'original')
            self.assertEqual(sorted(item.name for item in path.parent.iterdir()), ['state.json'])

    def test_directory_sync_failure_reports_visible_but_uncertain_replacement(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            path.write_text('original')
            with patch('app.core.tinyproxy.sync_directory', side_effect=OSError('test sync failure')):
                with self.assertRaises(DurabilityError):
                    atomic_write(path, 'replacement')
            self.assertEqual(path.read_text(), 'replacement')

    def test_state_removal_is_synced_even_on_retry(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, SESSION_STATE_FILE=str(Path(folder) / 'session.json')):
            replication.write_state('peer', {'key': 'a' * 64})
            with patch.object(replication, 'sync_directory', side_effect=OSError('test sync failure')):
                with self.assertRaises(OSError):
                    replication.delete_state('peer')
            self.assertFalse(replication.state('peer').exists())
            with patch.object(replication, 'sync_directory') as sync:
                replication.delete_state('peer')
                sync.assert_called_once_with(Path(folder))

    def test_non_object_state_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, SESSION_STATE_FILE=str(Path(folder) / 'session.json')):
            replication.state('peer').write_text('[]')
            with self.assertRaises(ConfigError):
                replication.read_state('peer')


class CredentialDurabilityTests(unittest.TestCase):
    old = 'a' * 64
    # Reuse fixture helpers without inheriting and rerunning its tests.
    def setUp(self):
        self.client = None
        key_api.PeerKeyApiTests.setUp(self)

    policy = key_api.PeerKeyApiTests.policy
    rotate = key_api.PeerKeyApiTests.rotate
    commit = key_api.PeerKeyApiTests.commit

    def test_standby_does_not_commit_when_staged_key_cannot_be_persisted(self):
        staged = {'key': self.old, 'pin': 'b' * 64,
                  'rotation': {'id': 'c' * 32, 'key': 'd' * 64, 'expires': 9999999999}}
        with patch.object(replication, 'write_state', side_effect=OSError(errno.ENOSPC, 'test disk full')), \
                patch.object(replication, 'fetch_primary') as fetch:
            with self.assertRaises(OSError):
                replication.finish_key_rotation(staged)
            fetch.assert_not_called()

    def test_primary_retries_durable_commit_after_post_rename_sync_failure(self):
        self.policy(self.old)
        self.assertEqual(self.rotate().status_code, 200)
        offer = self.policy(self.old).get_json()['credentialRotation']
        with patch('app.core.tinyproxy.sync_directory', side_effect=OSError('test sync failure')):
            self.assertEqual(self.commit(offer).status_code, 500)
        self.assertEqual(replication.read_state('peer')['key'], offer['key'])
        with patch('app.core.tinyproxy.sync_directory') as sync:
            self.assertEqual(self.commit(offer).status_code, 200)
            sync.assert_called()
        self.assertEqual(self.policy(self.old).status_code, 403)

    def test_consumed_invitation_cannot_replace_peer_after_interrupted_cleanup(self):
        invite = {'key': 'f' * 64, 'expires': 9999999999, 'cluster': replication.cluster_id()}
        replication.write_state('enrollment', invite)
        result = self.client.post('/internal/ha/enroll', headers={'Authorization': 'Bearer ' + invite['key']})
        self.assertEqual(result.status_code, 403)
        self.assertEqual(replication.read_state('peer'), {'key': self.old})

    def test_standby_retries_commit_after_failure_to_persist_new_active_key(self):
        staged = {'key': self.old, 'pin': 'b' * 64,
                  'rotation': {'id': 'c' * 32, 'key': 'd' * 64, 'expires': 9999999999}}
        replication.write_state('peer', staged)
        commit = {'committed': True, 'id': 'c' * 32, 'cluster': replication.cluster_id()}
        original = replication.write_state
        def store(name, value):
            if 'rotation' not in value:
                raise OSError(errno.ENOSPC, 'test disk full')
            original(name, value)
        with patch.object(replication, 'write_state', side_effect=store), \
                patch.object(replication, 'fetch_primary', return_value=commit):
            with self.assertRaises(OSError):
                replication.finish_key_rotation(staged)
        self.assertEqual(json.loads(replication.state('peer').read_text()), staged)
        with patch.object(replication, 'fetch_primary', return_value=commit):
            self.assertEqual(replication.finish_key_rotation(replication.read_state('peer'))['key'], 'd' * 64)
