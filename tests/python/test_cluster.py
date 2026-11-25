"""A stale or forgeable status cannot claim that failover protection is healthy."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.core import cluster
from app.core.tinyproxy import ConfigError


class ClusterBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.profile = self.root / 'profile.json'
        self.status = self.root / 'status.json'
        self.context = patch.multiple(cluster, PROFILE=self.profile, STATUS=self.status,
                                      SERVICE_RESERVATION=self.root / 'service-reservation.conf',
                                      SETUP_CHECKPOINT=self.root / 'cluster-bootstrap.json')
        self.context.start()
        self.addCleanup(self.context.stop)

    def test_regular_nodes_keep_independent_control(self):
        cluster.require_independent_control('upgrading')
        self.assertEqual(cluster.read_status(), {'state': 'not-configured'})

    def test_unreadable_reservation_fails_closed_without_crashing_status(self):
        real_exists = Path.exists
        for target in (self.profile, cluster.SERVICE_RESERVATION, cluster.SETUP_CHECKPOINT):
            def exists(path):
                if path == target:
                    raise PermissionError('isolated inaccessible parent')
                return real_exists(path)
            with self.subTest(target=target), patch.object(Path, 'exists', exists):
                self.assertTrue(cluster.reserved())
                self.assertEqual(cluster.read_status(), {'state': 'unknown'})
                with self.assertRaises(ConfigError):
                    cluster.require_independent_control('retiring')

    def test_reserved_nodes_are_guarded_even_if_the_profile_is_malformed(self):
        self.profile.write_text('invalid')
        with self.assertRaises(ConfigError):
            cluster.require_independent_control('retiring this node')
        self.assertEqual(cluster.read_status(), {'state': 'unknown'})

    def test_removing_only_the_profile_does_not_bypass_service_reservation(self):
        cluster.SERVICE_RESERVATION.write_text('[Service]\n')
        with self.assertRaises(ConfigError):
            cluster.require_independent_control('upgrading')

    def test_checkpoint_alone_guards_interrupted_reservation(self):
        cluster.SETUP_CHECKPOINT.write_text('{}')
        self.assertEqual(cluster.read_status(), {'state': 'unknown'})
        with self.assertRaises(ConfigError):
            cluster.require_independent_control('retiring')

    @unittest.skipIf(os.name == 'nt', 'Linux symlink boundary.')
    def test_dangling_profile_cannot_bypass_the_guard(self):
        self.profile.symlink_to(self.root / 'missing')
        with self.assertRaises(ConfigError):
            cluster.require_independent_control('restoring a backup')

    @unittest.skipUnless(os.name == 'posix' and getattr(os, 'geteuid', lambda: -1)() == 0, 'Root-owned status fixture.')
    def test_only_fresh_consistent_root_evidence_is_accepted(self):
        self.profile.write_text('{}')
        good = dict(version=1, checked=1000, state='protected', owners=1, migrationPhase='active',
                    operation='a' * 32, planHash='b' * 64, transportProof='private',
                    quorate=True, witnessConnected=True, fencingConfigured=True, fencingReady=True, policySafe=True)
        self.status.write_text(json.dumps(good))
        self.status.chmod(0o640)
        self.assertEqual(cluster.read_status(now=1001)['state'], 'protected')
        self.assertNotIn('migrationPhase', cluster.read_status(now=1001))
        self.assertNotIn('operation', cluster.read_status(now=1001))
        migration = cluster.read_status(now=1001, include_migration=True)
        self.assertEqual(migration['migrationPhase'], 'active')
        self.assertEqual(migration['operation'], 'a' * 32)
        self.assertEqual(migration['planHash'], 'b' * 64)
        self.assertNotIn('transportProof', migration)
        self.assertNotIn('operation', cluster.read_status(now=1019.5, include_migration=True))
        self.assertEqual(cluster.read_status(now=1021)['state'], 'unknown')
        self.assertNotIn('migrationPhase', cluster.read_status(now=1021, include_migration=True))
        self.assertEqual(cluster.read_status(now=990)['state'], 'unknown')
        self.status.write_text(json.dumps({**good, 'operation': 'x' * 32}))
        self.assertNotIn('planHash', cluster.read_status(now=1001, include_migration=True))
        for changed in ({'owners': 2}, {'quorate': False}, {'checked': True}, {'policySafe': 'yes'}):
            self.status.write_text(json.dumps({**good, **changed}))
            self.assertEqual(cluster.read_status(now=1001)['state'], 'unknown')
        self.status.write_text(json.dumps(good))
        self.status.chmod(0o666)
        self.assertEqual(cluster.read_status(now=1001)['state'], 'unknown')

    def test_malformed_or_missing_export_never_claims_health(self):
        self.profile.write_text('{}')
        for body in ('{', '[]', '{"state":"protected"}'):
            self.status.write_text(body)
            self.assertEqual(cluster.read_status(now=1001)['state'], 'unknown')
