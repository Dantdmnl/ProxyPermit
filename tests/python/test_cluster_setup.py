"""Prerequisite checks never activate HA or confuse metadata with a fencing test."""
from argparse import Namespace
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.core import cluster_setup as core
from scripts.deploy import cluster_setup as setup
from scripts.dev import proxmox_fencing as delegation
from scripts.deploy import cluster_web, proxmox_fence


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

    def test_readiness_confirms_only_the_current_verified_power_observation(self):
        from scripts.deploy import cluster_native
        expected = core.plan_hash(plan())
        operation = 'a' * 32
        pointer = dict(operation=operation, planHash=expected)
        values = dict(PROXYPERMIT_MANAGED_HA='true', PROXYPERMIT_HA_NODE='a',
                      NODE0_ADDRESS='192.0.2.11', NODE1_ADDRESS='192.0.2.12')

        def record(path, **_kwargs):
            if path == core.PLAN:
                return plan()
            if path.name == 'proxmox-fencing.json':
                return dict(host='pve.example', pveNode='host', target=8102)
            if path.name == 'preflight-current.json':
                return pointer
            raise FileNotFoundError

        with patch.object(core, 'protected', side_effect=record), \
                patch.object(setup, 'command', side_effect=['NTP=yes\nNTPSynchronized=yes', 'Status: ON'] * 2), \
                patch.object(setup, 'metadata_parameters', return_value={'api_token'}), \
                patch.object(cluster_native, 'verify_power_receipt') as verify:
            self.assertEqual(setup.inspect(values)['checks']['powerTest'], 'pass')
            verify.assert_called_once_with(setup.cluster_bootstrap.FOLDER / operation, expected)
            verify.side_effect = ValueError('old receipt')
            self.assertEqual(setup.inspect(values)['checks']['powerTest'], 'pending')

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
        value = dict(version=1, checked=now, checks=dict.fromkeys(core.CHECKS, 'pending'), token='never public',
                     planRevision=core.plan_hash(plan()), stagedPlan=plan())
        with patch.object(core, 'protected', return_value=value):
            self.assertEqual(core.read_readiness(now + 91)['state'], 'unknown')
            self.assertEqual(core.read_readiness(now - 6)['state'], 'unknown')
            self.assertNotIn('token', core.read_readiness(now))
            self.assertNotIn('stagedPlan', core.read_readiness(now))
            self.assertEqual(core.read_readiness(now, include_plan=True)['stagedPlan'], plan())
            value['planRevision'] = 'a' * 64
            self.assertNotIn('stagedPlan', core.read_readiness(now, include_plan=True))
            value['checks']['time'] = 'green'
            self.assertEqual(core.read_readiness(now)['state'], 'unknown')

    @unittest.skipUnless(os.name == 'posix' and getattr(os, 'geteuid', lambda: -1)() == 0, 'Linux request permissions.')
    def test_cluster_web_worker_only_accepts_fixed_jobs_and_rejects_symlinks(self):
        job = dict(id='a' * 32, action='stage', plan=plan(), expected='', username='admin',
                   accountRevision='admin-v1', created=int(time.time()))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'request.json'
            path.write_text(json.dumps(job))
            path.chmod(0o600)
            with patch.object(core, 'WEB_REQUEST', path):
                self.assertEqual(cluster_web.read_request(0), job)
                for changed in (dict(command='fixture'), dict(created=0), dict(action='activate'),
                                dict(action='prepare'), dict(plan={**plan(), 'token': 'fixture'})):
                    path.write_text(json.dumps({**job, **changed}))
                    with self.assertRaises(ValueError):
                        cluster_web.read_request(0)
                path.write_text(json.dumps(job))
                path.chmod(0o644)
                with self.assertRaises(ValueError):
                    cluster_web.read_request(0)
                target = path.rename(path.with_name('real.json'))
                path.symlink_to(target)
                with self.assertRaises(OSError):
                    cluster_web.read_request(0)

    def test_worker_only_calls_local_plan_preparation_and_rechecks_the_actor(self):
        job = dict(id='a' * 32, action='stage', plan=plan(), expected='', username='admin')
        base = Path('/opt/proxypermit/current')
        with patch.object(cluster_web, 'verify_actor') as verify, patch.object(setup, 'stage_plan') as stage, \
                patch.object(setup.cluster_bootstrap, 'prepare') as prepare, patch.object(setup.cluster_bootstrap, 'run'):
            self.assertEqual(cluster_web.execute(job, 123, base), core.plan_hash(job['plan']))
            stage.assert_called_once_with(job['plan'], 'node', expected='')
            verify.assert_called_once_with(job, 123)
            digest = core.plan_hash(job['plan'])
            prepare.return_value = {'planHash': digest}
            cluster_web.execute({**job, 'action': 'prepare', 'plan': None, 'expected': digest}, 123, base)
            prepare.assert_called_once_with('node', expected_hash=digest)
            verify.side_effect = ValueError('changed account')
            with self.assertRaisesRegex(ValueError, 'changed account'):
                cluster_web.execute(job, 123, base)
            self.assertEqual(stage.call_count, 1)

    @unittest.skipUnless(os.name == 'posix' and getattr(os, 'geteuid', lambda: -1)() == 0, 'Linux root-owned worker fixture.')
    def test_worker_stages_a_root_owned_plan_and_publishes_redacted_result(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pending, result = root / 'request.json', root / 'runtime/result.json'
            stored, environment, accounts = (root / name for name in ('plan.json', 'native.env', 'accounts.json'))
            environment.write_text(f'AUTH_ACCOUNTS_FILE={accounts}\n')
            accounts.write_text(json.dumps({'admin': {'role': 'admin', 'revision': 'admin-v1'}}))
            job = dict(id='a' * 32, action='stage', plan=plan(), expected='', username='admin',
                       accountRevision='admin-v1', created=int(time.time()))
            pending.write_text(json.dumps(job))
            pending.chmod(0o600)
            for source in (environment, accounts):
                source.chmod(0o640)
            with patch.multiple(core, WEB_REQUEST=pending, WEB_RESULT=result, PLAN=stored), \
                    patch('pwd.getpwnam', return_value=SimpleNamespace(pw_uid=0, pw_gid=0)), \
                    patch.object(cluster_web.install, 'require_trusted_path'), \
                    patch.object(cluster_web.management_tls, 'ENVIRONMENT', environment), \
                    patch.object(setup, 'maintenance_lock'), patch.object(setup, 'check_host', return_value='a'), \
                    patch.object(setup.cluster, 'reserved', return_value=False), \
                    patch.object(setup.cluster_bootstrap, 'JOURNAL', root / 'preparation.json'), \
                    patch.object(setup.cluster_bootstrap, 'run') as audit:
                self.assertEqual(cluster_web.main(['--install-dir', folder]), 0)
                self.assertFalse(pending.exists())
                self.assertEqual(core.protected(stored, private=True), job['plan'])
                self.assertEqual(core.web_result(), dict(id=job['id'], action='stage', phase='complete',
                                                        planHash=core.plan_hash(job['plan'])))
                self.assertIn('cluster_web_plan_saved', audit.call_args.args)

    def test_host_check_distinguishes_ha_nodes_and_witness(self):
        env = ('PROXYPERMIT_MANAGED_HA=true\nPROXYPERMIT_HA_NODE=a\n'
               'NODE0_ADDRESS=192.0.2.11\nNODE1_ADDRESS=192.0.2.12\nVIP_ADDRESS=192.0.2.10\n')
        def interfaces(*addresses):
            return json.dumps([dict(addr_info=[dict(family='inet', local=address) for address in addresses])])
        with patch('scripts.deploy.management_tls.protected', return_value=env) as installed, \
                patch.object(setup.socket, 'gethostname', return_value='node-a.lab.example'), \
                patch.object(setup, 'command', return_value=interfaces('192.0.2.11', '192.0.2.10')) as ip:
            self.assertEqual(setup.check_host(plan(), 'node'), 'a')
            self.assertRaises(ValueError, setup.check_host, plan(), 'witness')
            changed = plan()
            changed['witness'] = '192.0.2.10'
            self.assertRaises(ValueError, setup.check_host, changed, 'node')
            ip.return_value = interfaces('192.0.2.11', '192.0.2.12')
            self.assertRaises(ValueError, setup.check_host, plan(), 'node')
            ip.return_value = interfaces('192.0.2.13')
            installed.reset_mock()
            self.assertEqual(setup.check_host(plan(), 'witness'), 'witness')
            installed.assert_not_called()

    @unittest.skipUnless(os.name == 'posix' and getattr(os, 'geteuid', lambda: -1)() == 0, 'Root-owned plan fixtures.')
    def test_private_plan_permissions_symlinks_and_dry_run_are_enforced(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder);source = root / 'input.json';source.write_text(json.dumps(plan()))
            stored = root / 'cluster-plan.json'
            journal = root / 'preparation.json'
            with patch.object(core, 'PLAN', stored), patch.object(setup, 'maintenance_lock') as lock, \
                    patch.object(setup, 'check_host', return_value='a') as check_host, \
                    patch.object(setup.cluster, 'reserved', return_value=False) as reserved, \
                    patch.object(setup.cluster_bootstrap, 'JOURNAL', journal):
                with redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(setup.main(['--plan', str(source)]), 0)
                self.assertEqual(json.loads(output.getvalue())['planHash'], core.plan_hash(plan()))
                lock.assert_not_called();self.assertFalse(stored.exists())
                check_host.assert_not_called()
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(setup.main(['--plan', str(source), '--stage']), 0)
                self.assertEqual(stored.stat().st_mode & 0o777, 0o600)
                changed = {**plan(), 'witness': '192.0.2.20'}
                with self.assertRaisesRegex(ValueError, 'staged plan changed'):
                    setup.stage_plan(changed, 'node', expected='')
                journal.write_text(json.dumps(dict(planHash=core.plan_hash(plan()), role='node')))
                journal.chmod(0o600)
                self.assertEqual(setup.stage_plan(plan(), 'node'), 'a')
                changed = {**plan(), 'witness': '192.0.2.20'}
                with self.assertRaisesRegex(ValueError, 'already started'):
                    setup.stage_plan(changed, 'node')
                reserved.return_value = True
                with self.assertRaisesRegex(ValueError, 'maintenance workflow'):
                    setup.stage_plan(plan(), 'node')
                self.assertEqual(core.protected(stored, private=True), plan())
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
