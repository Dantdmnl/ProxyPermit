"""Prerequisite checks never activate HA or confuse metadata with a fencing test."""
from argparse import Namespace
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from app.core import cluster_setup as core
from scripts.deploy import cluster_setup as setup
from scripts.dev import proxmox_fencing as delegation
from scripts.deploy import proxmox_fence


def plan():
    return dict(version=1, platform='proxmox', witness='192.0.2.13', fenceAddress='192.0.2.100',
                nodes=[dict(name='node-a', address='192.0.2.11', target='8101'),
                       dict(name='node-b', address='192.0.2.12', target='8102')])


class ClusterPreparationTests(unittest.TestCase):
    def test_plan_rejects_secrets_shared_targets_and_invalid_witnesses(self):
        self.assertEqual(core.validate_plan(plan()), plan())
        invalid = []
        for key, value in [('password', 'private'), ('platform', []), ('witness', '192.0.2.11'),
                           ('witness', '127.0.0.1'), ('fenceAddress', 1)]:
            item = plan();item[key] = value;invalid.append(item)
        for key in ('name', 'address', 'target'):
            item = plan();item['nodes'][1][key] = item['nodes'][0][key];invalid.append(item)
        item = plan();item['nodes'][0]['target'] = 'guest-name';invalid.append(item)
        for item in invalid:
            with self.subTest(value=item), self.assertRaises(ValueError):
                core.validate_plan(item)

    def test_synchronized_clock_and_capable_agent_still_do_not_prove_fencing(self):
        values = dict(PROXYPERMIT_MANAGED_HA='true', NODE0_ADDRESS='192.0.2.11', NODE1_ADDRESS='192.0.2.12')
        def profile(path, **_kwargs):
            if path == core.PLAN:return plan()
            raise FileNotFoundError
        with patch.object(core, 'protected', side_effect=profile), \
                patch.object(setup, 'command', return_value='NTP=yes\nNTPSynchronized=yes\n') as command, \
                patch.object(setup.shutil, 'which', return_value='/usr/sbin/fixture'), \
                patch.object(setup, 'metadata_parameters', return_value={'api_token'}):
            result = setup.inspect(values)
        self.assertEqual(result['checks']['time'], 'pass')
        self.assertEqual(result['checks']['authentication'], 'pending')
        self.assertEqual(result['checks']['powerTest'], 'pending')
        self.assertNotIn('192.0.2.', json.dumps(result))
        self.assertNotIn('ready', result)
        self.assertEqual(command.call_count, 1)

    def test_upstream_agent_build_is_pinned_and_syntax_checked(self):
        source = proxmox_fence.content()
        compile(source, 'fence_proxypermit_pve', 'exec')
        self.assertIn(proxmox_fence.MARKER, source)
        self.assertNotIn('conn.setopt(pycurl.VERBOSE, True)', source)
        with patch.object(proxmox_fence, 'SOURCE_SHA256', '0' * 64), self.assertRaises(ValueError):
            proxmox_fence.content()

    @unittest.skipUnless(os.name == 'posix' and getattr(os, 'geteuid', lambda: -1)() == 0, 'Root-owned credential fixture.')
    def test_peer_token_guard_rejects_other_targets_unverified_tls_expiry_and_wrong_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'fencing.json'
            profile = dict(version=1, host='hypervisor.example', pveNode='host', target=8102,
                expires=int(time.time()) + 3600, tokenId='fixture@pve!node-a', token='a' * 36)
            path.write_text(json.dumps(profile));path.chmod(0o600)
            namespace = {'json': json}
            exec(proxmox_fence.GUARD.replace('/etc/proxypermit/proxmox-fencing.json', str(path)), namespace)
            options = {'--ip': profile['host'], '--pve-node': 'host', '--action': 'status', '--plug': '8102'}
            good = dict(options);namespace['proxypermit_credentials'](good)
            self.assertEqual(good['--api-token'], 'fixture@pve!node-a=' + 'a' * 36)
            for key, value in [('--plug', '8101'), ('--ip', 'elsewhere.example'), ('--ssl-insecure', True),
                               ('--action', 'on'), ('--api-token', 'external'), ('--ipport', '443')]:
                with self.subTest(key=key), self.assertRaises(ValueError):
                    namespace['proxypermit_credentials']({**options, key: value})
            profile['expires'] = int(time.time()) - 1;path.write_text(json.dumps(profile))
            with self.assertRaises(ValueError):namespace['proxypermit_credentials'](options)
            path.chmod(0o644)
            with self.assertRaises(ValueError):namespace['proxypermit_credentials'](options)

    def test_unsynchronized_clock_old_agent_and_topology_mismatch_block_preparation(self):
        values = dict(PROXYPERMIT_MANAGED_HA='true', NODE0_ADDRESS='192.0.2.11', NODE1_ADDRESS='192.0.2.12')
        with patch.object(core, 'protected', return_value=plan()), \
                patch.object(setup, 'command', return_value='NTP=yes\nNTPSynchronized=no\n'), \
                patch.object(setup.shutil, 'which', return_value=None), \
                patch.object(setup, 'metadata_parameters', return_value={'passwd'}):
            result = setup.inspect(values)
            values['NODE1_ADDRESS'] = '192.0.2.14'
            mismatch = setup.inspect(values)
        for key in ('time', 'packages', 'authentication'):
            self.assertEqual(result['checks'][key], 'blocked')
        self.assertEqual(mismatch['checks']['plan'], 'blocked')

    def test_public_evidence_expires_and_discards_unknown_private_fields(self):
        now = int(time.time())
        value = dict(version=1, checked=now, checks=dict.fromkeys(core.CHECKS, 'pending'), token='never public')
        with patch.object(core, 'protected', return_value=value):
            self.assertEqual(core.read_readiness(now + 91)['state'], 'unknown')
            self.assertEqual(core.read_readiness(now - 6)['state'], 'unknown')
            self.assertNotIn('token', core.read_readiness(now))
            value['checks']['time'] = 'green'
            self.assertEqual(core.read_readiness(now)['state'], 'unknown')

    @unittest.skipUnless(os.name == 'posix' and getattr(os, 'geteuid', lambda: -1)() == 0, 'Root-owned plan fixtures.')
    def test_private_plan_permissions_symlinks_and_dry_run_are_enforced(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder);source = root / 'input.json';source.write_text(json.dumps(plan()))
            stored = root / 'cluster-plan.json'
            with patch.object(core, 'PLAN', stored), patch.object(setup, 'maintenance_lock') as lock:
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(setup.main(['--plan', str(source)]), 0)
                lock.assert_not_called();self.assertFalse(stored.exists())
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(setup.main(['--plan', str(source), '--stage']), 0)
                self.assertEqual(stored.stat().st_mode & 0o777, 0o600)
                stored.chmod(0o644)
                with self.assertRaises(ValueError):core.protected(stored, private=True)
                stored.unlink();stored.symlink_to(source)
                with self.assertRaises(OSError):core.protected(stored, private=True)


class ProxmoxDelegationTests(unittest.TestCase):
    def test_each_token_is_limited_to_its_peer_and_no_secret_is_printed_or_sent_as_an_argument(self):
        args = Namespace(cluster='fixture', pool='fixture-pool', node_a=8101, node_b=8102, days=14)
        mutations = []
        def query(*command):
            if command[:3] == ('/usr/bin/pvesh', 'get', '/pools/fixture-pool'):
                return dict(members=[dict(type='qemu', vmid=8101), dict(type='qemu', vmid=8102)])
            if command[-1] == 'list':return []
            if command[1:4] == ('user', 'token', 'add'):
                return {'full-tokenid': command[4] + '!' + command[5], 'value': 'isolated-fence-secret'}
            if command[1:4] == ('user', 'token', 'permissions'):
                target = 8102 if command[5] == 'node-a' else 8101
                return {'/vms/' + str(target): {'VM.Audit': 1, 'VM.PowerMgmt': 1}}
            raise AssertionError(command)
        with tempfile.TemporaryDirectory() as folder, patch.object(delegation, 'query', side_effect=query), \
                patch.object(delegation, 'run', side_effect=lambda *command: mutations.append(command)), \
                patch.object(delegation, 'write') as write, redirect_stdout(io.StringIO()) as output:
            target = Path(folder) / 'credentials'
            # The fixture parent is root-owned on Linux root runs. Patch only the
            # root ownership check for Windows/non-root test environments.
            original = Path.lstat
            def lstat(path):
                value = original(path)
                if path == target.parent:
                    from types import SimpleNamespace
                    return SimpleNamespace(st_uid=0, st_mode=0o40700)
                return value
            with patch.object(Path, 'lstat', lstat):
                delegation.apply(args, target)
            self.assertNotIn('isolated-fence-secret', output.getvalue())
            self.assertNotIn('isolated-fence-secret', repr(mutations))
            records = [call.args[1] for call in write.call_args_list]
            self.assertEqual([item['target'] for item in records if 'token' in item], [8102, 8101])
            acl = [command for command in mutations if 'modify' in command]
            self.assertTrue(all(command[3] in ('/vms/8101', '/vms/8102') for command in acl))
            self.assertFalse(any('Administrator' in command or 'PVEAdmin' in command for command in mutations))
