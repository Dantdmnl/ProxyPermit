"""Certificate commit is durable before replacement and cannot be rolled back silently."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.core.tinyproxy import ConfigError
from scripts.deploy import ha_certificates as maintenance


class CertificateMaintenanceTests(unittest.TestCase):
    def setUp(self):
        audit = patch.object(maintenance, 'audit')
        self.audit = audit.start()
        self.addCleanup(audit.stop)
        actor = patch.object(maintenance, 'actor_uid', return_value=1000)
        actor.start()
        self.addCleanup(actor.stop)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.private = Path(self.folder.name)
        self.peer = dict(key='a' * 64, certificatePrepared='b' * 32)
        self.plan = dict(id='b' * 32, oldPin='c' * 64, newPin='d' * 64, created=1000, expires=1600,
                         phase='offered', binding=hashlib.sha256(('a' * 64).encode()).hexdigest(),
                         service='proxypermit-ha-tls.service')

    def test_candidate_without_standby_ack_does_not_commit(self):
        with patch.object(maintenance, 'load_plan', return_value=self.plan), \
                patch.object(maintenance, 'peer_state', return_value={'key': 'a' * 64}), \
                patch.object(maintenance.time, 'time', return_value=1100), \
                patch.object(maintenance, 'root_write') as write, patch.object(maintenance, 'finish') as finish:
            with self.assertRaises(ConfigError):
                maintenance.activate(self.private, 0)
            write.assert_not_called()
            finish.assert_not_called()

    def test_changed_peer_or_expiring_transition_does_not_commit(self):
        for peer, now in (({**self.peer, 'key': 'f' * 64}, 1100), (self.peer, 1540)):
            with patch.object(maintenance, 'load_plan', return_value=self.plan), \
                    patch.object(maintenance, 'peer_state', return_value=peer), \
                    patch.object(maintenance.time, 'time', return_value=now), \
                    patch.object(maintenance, 'root_write') as write:
                with self.assertRaises(ConfigError):
                    maintenance.activate(self.private, 0)
                write.assert_not_called()

    def test_failed_replacement_leaves_irreversible_commit_record(self):
        committed = []
        def write(_path, data):
            committed.append(json.loads(data))
        def fail(_private, plan, _gid):
            self.assertEqual(committed[-1]['phase'], 'committed')
            self.assertEqual(plan, committed[-1])
            raise OSError('simulated storage failure')
        with patch.object(maintenance, 'load_plan', return_value=self.plan), \
                patch.object(maintenance, 'peer_state', return_value=self.peer), \
                patch.object(maintenance.time, 'time', return_value=1100), \
                patch.object(maintenance, 'root_write', side_effect=write), \
                patch.object(maintenance, 'finish', side_effect=fail):
            with self.assertRaises(OSError):
                maintenance.activate(self.private, 0)
        self.assertEqual(len(committed), 1)
        self.assertEqual(committed[0]['phase'], 'committed')

    def test_failed_commit_storage_does_not_replace_certificates(self):
        with patch.object(maintenance, 'load_plan', return_value=self.plan), \
                patch.object(maintenance, 'peer_state', return_value=self.peer), \
                patch.object(maintenance.time, 'time', return_value=1100), \
                patch.object(maintenance, 'root_write', side_effect=OSError('test disk full')), \
                patch.object(maintenance, 'finish') as finish:
            with self.assertRaises(OSError):
                maintenance.activate(self.private, 0)
            finish.assert_not_called()

    def test_committed_renewal_cannot_be_cancelled(self):
        with patch.object(maintenance, 'load_plan', return_value={**self.plan, 'phase': 'committed'}):
            with self.assertRaises(ConfigError):
                maintenance.cancel(self.private)

    def test_completion_after_lost_reply_does_not_rewrite_active_files(self):
        peer = {**self.peer, 'certificateVersion': self.plan['id']}
        with patch.object(maintenance, 'load_plan', return_value={**self.plan, 'phase': 'committed'}), \
                patch.object(maintenance, 'peer_state', return_value=peer), \
                patch.object(maintenance, 'pin', return_value=self.plan['newPin']), \
                patch.object(maintenance.deploy, 'env_values', return_value={'HA_POLICY_TLS_PIN': self.plan['newPin']}), \
                patch.object(maintenance, 'root_write') as write, patch.object(maintenance, 'finish') as finish:
            maintenance.activate(self.private, 0)
            finish.assert_not_called()
            self.assertEqual(json.loads(write.call_args.args[1])['phase'], 'complete')
