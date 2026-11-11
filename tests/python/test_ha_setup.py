"""Regression coverage for the privileged HA setup boundary."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import ha
from scripts.deploy import ha_setup
from app.web import ha_network


class HaSetupTests(unittest.TestCase):
    def test_new_membership_discards_only_old_replication_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = ('ha-peer.json', 'ha-applied-policy.json', 'ha-replication-status.json', 'accounts.json')
            for name in names:
                (root / name).write_text('fixture')
            ha_setup.reset_standby_history(root / 'ha-peer.json')
            self.assertFalse((root / 'ha-applied-policy.json').exists())
            self.assertFalse((root / 'ha-replication-status.json').exists())
            self.assertEqual((root / 'ha-peer.json').read_text(), 'fixture')
            self.assertEqual((root / 'accounts.json').read_text(), 'fixture')

    def test_network_inventory_avoids_known_addresses_and_ignores_loopback(self):
        addresses = [dict(ifname='lo', flags=['UP', 'LOOPBACK'], addr_info=[dict(local='127.0.0.1', prefixlen=8)]),
                     dict(ifname='ens18', flags=['UP'], addr_info=[dict(local='192.0.2.11', prefixlen=24)]),
                     dict(ifname='ens19', flags=['UP'], addr_info=[dict(local='198.51.100.5', prefixlen=24, dynamic=True)])]
        with patch.object(ha_network, 'ip_json', side_effect=[addresses,
                [dict(gateway='192.0.2.10')], [dict(dst='192.0.2.2')]]) as command:
            networks = ha_network.discover_network()
        self.assertEqual(len(networks), 2)
        self.assertEqual(networks[0]['network'], '192.0.2.0/24')
        self.assertEqual(networks[0]['suggestedVip'], '192.0.2.3/24')
        self.assertFalse(networks[0]['dynamic'])
        self.assertTrue(networks[1]['dynamic'])
        self.assertEqual(command.call_args_list, [unittest.mock.call('address', 'show'),
            unittest.mock.call('route', 'show'), unittest.mock.call('neigh', 'show')])

    def test_vip_probe_rejects_an_answer_and_never_probes_joining_nodes(self):
        topology = ha.ha_topology('a', 'ens18', '192.0.2.11', '192.0.2.12',
                                 '192.0.2.10/24', '192.0.2.0/24', 51)
        with patch.object(ha_setup.deploy.subprocess, 'run') as probe:
            probe.return_value.returncode = 1
            with self.assertRaisesRegex(ValueError, 'virtual IP'):
                ha_setup.verify_unused_vip(topology)
            probe.return_value.returncode = 0
            ha_setup.verify_unused_vip(topology)
            probe.reset_mock()
            ha_setup.verify_unused_vip({**topology, 'node': 'b'})
            probe.assert_not_called()

    @unittest.skipIf(os.name == 'nt' or not hasattr(os, 'geteuid') or os.geteuid() != 0,  # pylint: disable=no-member
                     'The directory guard is exercised by the root Linux helper.')
    def test_directory_guard_rejects_lock_symlinks_and_restores_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            path.chmod(0o775)
            lock = path / 'filter.lock'
            with ha_setup.frozen_proxy_directory(path, lock):
                self.assertEqual(path.stat().st_mode & 0o022, 0)
            self.assertEqual(path.stat().st_mode & 0o777, 0o775)
            target = path / 'protected'
            target.write_text('protected fixture')
            lock.symlink_to(target)
            with self.assertRaisesRegex(ValueError, 'lock file'):
                with ha_setup.frozen_proxy_directory(path, lock):
                    self.fail('A lock symlink must be rejected.')
            self.assertEqual(target.read_text(), 'protected fixture')
            self.assertEqual(path.stat().st_mode & 0o777, 0o775)

    def test_host_config_preserves_filters_and_existing_acl_order(self):
        topology = ha.ha_topology('a', 'ens18', '192.0.2.11', '192.0.2.12',
                                 '192.0.2.10/24', '192.0.2.0/24', 51)
        source = 'Port 3128\nListen 127.0.0.1\nAllow 127.0.0.1\nDeny 198.51.100.0/24\nFilter "/etc/tinyproxy/policy"\n'
        updated = ha.ha_proxy_config(source, topology)
        self.assertEqual(updated.count('Listen '), 1)
        self.assertIn('Listen 0.0.0.0\n', updated)
        self.assertLess(updated.index('Allow 192.0.2.0/24'), updated.index('Allow 127.0.0.1'))
        self.assertLess(updated.index('Allow 127.0.0.1'), updated.index('Deny 198.51.100.0/24'))
        self.assertIn('Filter "/etc/tinyproxy/policy"', updated)
        outgoing = ha.ha_setup_firewall_rules({**topology, 'node': 'b', 'local': topology['peer'], 'peer': topology['local']})
        tls = next(rule for rule in outgoing if rule[-1] == 'ProxyPermit HA OUT - Policy TLS to peer')
        self.assertEqual(tls[tls.index('to') + 1], '192.0.2.11')
        self.assertEqual(tls[tls.index('port') + 1], '8443')

    def test_plan_revision_tracks_environment_and_both_proxy_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env, config, filters = (root / name for name in ('env', 'config', 'filters'))
            for path in (env, config, filters):
                path.write_text('initial')
            with patch.object(ha, 'HA_SETUP_ENV', env):
                revision = ha.setup_revision(config, filters)
                for path in (env, config, filters):
                    path.write_text('changed')
                    self.assertNotEqual(ha.setup_revision(config, filters), revision)
                    path.write_text('initial')

    @unittest.skipIf(os.name == 'nt', 'The root helper uses Linux O_NOFOLLOW and file ownership.')
    def test_request_rejects_symlinks_broad_permissions_and_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'request.json'
            target = path.with_name('target.json')
            topology = ha.ha_topology('a', 'ens18', '192.0.2.11', '192.0.2.12',
                                     '192.0.2.10/24', '192.0.2.0/24', 51)
            data = dict(id='a' * 32, topology=topology, created=int(time.time()))
            target.write_text(json.dumps(data))
            target.chmod(0o600)
            path.symlink_to(target)
            with patch.object(ha_setup, 'HA_SETUP_REQUEST', path), patch.object(
                ha_setup.deploy, 'service_ownership', return_value=(os.getuid(), os.getgid(), os.chown)  # pylint: disable=no-member
            ):
                with self.assertRaises(OSError):
                    ha_setup.read_request()
                path.unlink()
                path.write_text(json.dumps(data))
                path.chmod(0o644)
                with self.assertRaises(ValueError):
                    ha_setup.read_request()
                path.chmod(0o600)
                self.assertEqual(ha_setup.read_request()[1]['node'], 'a')
                path.unlink()
                os.mkfifo(path, 0o600)  # pylint: disable=no-member
                with self.assertRaises(ValueError):
                    ha_setup.read_request()
                path.unlink()
                path.write_text(json.dumps(data))
                path.chmod(0o600)
                path.write_text(json.dumps({**data, 'created': int(time.time()) - 301}))
                with self.assertRaises(ValueError):
                    ha_setup.read_request()
