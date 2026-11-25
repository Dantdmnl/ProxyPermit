"""Maintenance survives lost replies, concurrent owners and interrupted upgrades."""
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from app.core import cluster_lease
from app.core.tinyproxy import ConfigError
from scripts.deploy import cluster_maintenance as maintenance

CIB = '<cib><configuration><nodes><node id="1" uname="node-a"/><node id="2" uname="node-b"/></nodes></configuration><status/></cib>'


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.disk_load, self.disk_save = maintenance.load, maintenance.save
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.cib = ET.fromstring(CIB)
        self.saved = None
        self.commit_reply = True
        self.stack.enter_context(patch.object(maintenance, 'require_root'))
        self.stack.enter_context(patch.object(maintenance, 'load', side_effect=lambda: deepcopy(self.saved)))
        self.stack.enter_context(patch.object(maintenance, 'save', side_effect=self.save))
        self.stack.enter_context(patch.object(maintenance, 'forget', side_effect=self.forget))
        self.stack.enter_context(patch.object(maintenance, 'query', side_effect=lambda: deepcopy(self.cib)))
        self.stack.enter_context(patch.object(maintenance, 'replace', side_effect=self.commit))
        self.stack.enter_context(patch.object(maintenance, 'local_name', return_value='node-a'))
        self.stack.enter_context(patch.object(maintenance, 'peer_ready', return_value=True))
        self.stack.enter_context(patch.object(maintenance, 'drained', return_value=True))
        self.stack.enter_context(patch.object(maintenance.cluster_status, 'evidence', return_value={'state': 'protected'}))
        self.audit = self.stack.enter_context(patch.object(maintenance, 'record'))

    def save(self, value):
        self.saved = deepcopy(value)

    def forget(self):
        self.saved = None

    def commit(self, root):
        self.cib = deepcopy(root)
        return self.commit_reply

    def test_lost_acquire_and_release_replies_are_reconciled(self):
        self.commit_reply = False
        saved = maintenance.acquire()
        self.assertEqual(self.saved['phase'], 'entered')
        maintenance.complete(saved)
        self.assertIsNone(self.saved)
        self.assertIsNone(cluster_lease.marker(self.cib))
        self.assertFalse(self.cib.findall('.//nvpair'))

    def test_status_is_read_only_and_reports_checkpoint_mismatch_without_token(self):
        self.assertEqual(maintenance.status(), {'state': 'no-reservation'})
        saved = maintenance.acquire()
        cluster_before = ET.tostring(self.cib)
        self.assertEqual(maintenance.status(), {'state': 'local-reservation', 'phase': 'entered',
                                                'peerServingProtectedVIP': True})
        self.assertEqual(ET.tostring(self.cib), cluster_before)
        self.assertEqual(self.saved, saved)
        maintenance.drained.return_value = False
        self.assertFalse(maintenance.status()['peerServingProtectedVIP'])
        self.saved['token'] = '0' * 32 if saved['token'] != '0' * 32 else '1' * 32
        self.assertEqual(maintenance.status(), {'state': 'reservation-mismatch', 'phase': 'entered'})
        self.audit.assert_not_called()

    def test_upgrade_failure_preserves_standby_and_reservation(self):
        with self.assertRaisesRegex(RuntimeError, 'update failed'):
            with maintenance.rolling(lambda: True, '/opt/proxypermit'):
                raise RuntimeError('update failed')
        self.assertEqual(self.saved['phase'], 'entered')
        self.assertIsNotNone(cluster_lease.marker(self.cib))
        self.assertEqual(self.cib.find(".//nvpair[@name='standby']").get('value'), 'on')
        with self.assertRaises(ConfigError):
            maintenance.acquire()

    def test_failed_health_cannot_release_reservation(self):
        with self.assertRaisesRegex(ConfigError, 'not healthy'):
            with maintenance.rolling(lambda: False, '/opt/proxypermit'):
                pass
        self.assertIsNotNone(cluster_lease.marker(self.cib))

    def test_other_owner_wins_compare_and_swap_without_being_overwritten(self):
        def concurrent_commit(_root):
            cluster_lease.claim(self.cib, 'node-b', 'b' * 32, 1000)
            return False
        with patch.object(maintenance, 'replace', side_effect=concurrent_commit):
            with self.assertRaisesRegex(ConfigError, 'Another node acquired'):
                maintenance.acquire()
        self.assertIsNone(self.saved)
        self.assertEqual(cluster_lease.marker(self.cib)['node'], 'node-b')
        self.assertIsNone(self.cib.find("configuration/nodes/node[@uname='node-a']//nvpair[@name='standby']"))

    def test_release_checkpoint_survives_process_death_after_cib_commit(self):
        saved = maintenance.acquire()
        cluster_lease.release(self.cib, saved)
        saved['phase'] = 'releasing'
        self.save(saved)
        maintenance.complete(saved)
        self.assertIsNone(self.saved)

    def test_changed_standby_is_preserved_during_recovery(self):
        saved = maintenance.acquire()
        self.cib.find(".//nvpair[@name='standby']").set('value', 'off')
        with self.assertRaises(ConfigError):
            maintenance.complete(saved)
        self.assertIsNotNone(self.saved)
        self.assertIsNotNone(cluster_lease.marker(self.cib))

    def test_release_waits_for_temporary_loss_of_peer_protection(self):
        saved = maintenance.acquire()
        maintenance.drained.side_effect = [False, False, True]
        with patch.object(maintenance.time, 'sleep') as wait:
            maintenance.complete(saved)
        self.assertEqual(wait.call_count, 2)
        self.assertIsNone(self.saved)
        self.assertIsNone(cluster_lease.marker(self.cib))

    def test_release_timeout_leaves_the_checkpoint_and_never_writes(self):
        saved = maintenance.acquire()
        maintenance.drained.return_value = False
        with patch.object(maintenance, 'replace') as replace, \
                patch.object(maintenance.time, 'monotonic', side_effect=[0, maintenance.PEER_RELEASE_WAIT]):
            with self.assertRaisesRegex(ConfigError, 'protected VIP'):
                maintenance.complete(saved)
        replace.assert_not_called()
        self.assertEqual(self.saved['phase'], 'entered')
        self.assertIsNotNone(cluster_lease.marker(self.cib))

    def test_unprotected_peer_prevents_any_cib_write(self):
        with patch.object(maintenance.cluster_status, 'evidence', return_value={'state': 'degraded'}), \
                patch.object(maintenance, 'replace') as replace:
            with self.assertRaises(ConfigError):
                maintenance.acquire()
        replace.assert_not_called()
        self.assertIsNone(self.saved)

    def test_cib_transport_never_passes_xml_credentials_as_arguments(self):
        with patch.object(maintenance.subprocess, 'run') as run:
            run.return_value.stdout = b''
            maintenance.command('cibadmin', '--replace', '--xml-pipe', '--sync-call', content=b'<private/>')
        self.assertNotIn(b'<private/>', run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs['input'], b'<private/>')
        self.assertNotIn('AWS_SECRET_ACCESS_KEY', run.call_args.kwargs['env'])

    @unittest.skipUnless(hasattr(__import__('os'), 'geteuid'), 'Root file ownership requires POSIX')
    def test_private_checkpoint_is_durable_and_untrusted_permissions_are_rejected(self):
        import os
        if getattr(os, 'geteuid', lambda: -1)() != 0:
            self.skipTest('Root ownership check requires disposable Linux root tests')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'checkpoint.json'
            with patch.object(maintenance, 'CHECKPOINT', path):
                saved = dict(version=1, phase='entered', node='node-a', token='a' * 32,
                    standbyId='standby', standbySet=None, before='off', created=1000)
                self.disk_save(saved)
                self.assertEqual(self.disk_load(), saved)
                path.chmod(0o644)
                with self.assertRaises(ConfigError):
                    self.disk_load()
