import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('installer', Path(__file__).resolve().parents[2] / 'scripts/deploy/install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        # Installer self-tests also run on real protected cluster hosts. Each
        # test supplies its own cluster state rather than using the host profile.
        reservation = patch.object(installer.cluster, 'reserved', return_value=False)
        reservation.start()
        self.addCleanup(reservation.stop)
        # Rollback fixtures must not discover a real node's managed TLS profile.
        from scripts.deploy import management_tls
        tls_state = tempfile.TemporaryDirectory()
        self.addCleanup(tls_state.cleanup)
        tls_folder = patch.object(management_tls, 'FOLDER', Path(tls_state.name) / 'management-tls')
        tls_folder.start()
        self.addCleanup(tls_folder.stop)

    @unittest.skipIf(os.name == 'nt', 'Directory descriptors require POSIX')
    def test_native_draft_tree_is_validated_without_privileged_recursive_migration(self):
        uid = getattr(os, 'getuid')()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backups = root / 'drafts'
            backups.mkdir(mode=0o700)
            fixture = backups / 'pending.json'
            fixture.write_text('isolated draft')
            fixture.chmod(0o600)
            installer.validate_native_backups(backups, uid)
            before = fixture.stat()
            fixture.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'OS review'):
                installer.validate_native_backups(backups, uid)
            self.assertEqual(fixture.stat().st_mode & 0o777, 0o644)
            fixture.chmod(0o600)
            link = backups / 'redirect.json'
            link.symlink_to(fixture)
            with self.assertRaisesRegex(ValueError, 'OS review'):
                installer.validate_native_backups(backups, uid)
            self.assertEqual(fixture.stat().st_uid, before.st_uid)
            self.assertEqual(fixture.read_text(), 'isolated draft')
            with self.assertRaises((OSError, ValueError)):
                installer.validate_native_backups(link, uid)

    def test_fresh_install_prepares_backups_before_unprivileged_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transaction = installer.Transaction(root / 'transaction')
            backups = root / '.backups'
            ownership = []
            installer.prepare_filter_backups(backups, transaction, 123, 456,
                lambda path, uid, gid: ownership.append((path, uid, gid)))
            self.assertTrue(backups.is_dir())
            self.assertEqual(ownership, [(backups, 123, 456)])
            transaction.restore()
            self.assertFalse(backups.exists())

    @unittest.skipIf(os.name == 'nt', 'Symlink creation requires POSIX')
    def test_filter_backup_symlink_cannot_redirect_privileged_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'operator-folder'
            target.mkdir()
            backups = root / '.backups'
            backups.symlink_to(target, target_is_directory=True)
            with patch.object(installer.os, 'chown') as chown:
                with self.assertRaises(ValueError):
                    installer.prepare_filter_backups(backups, installer.Transaction(root / 'transaction'), 123, 456, chown)
                chown.assert_not_called()

    def test_cluster_maintenance_is_restricted_to_protected_managed_upgrades(self):
        from types import SimpleNamespace
        good = dict(action='upgrade', mode='ha', managedHa=True)
        with patch.object(installer.cluster, 'reserved', return_value=True):
            installer.validate_cluster_upgrade(good, SimpleNamespace(upgrade=True))
            for plan in ({**good, 'mode': 'standalone'}, {**good, 'action': 'install'},
                         {**good, 'ha': {'node': 'a'}}, {**good, 'managedHa': False}):
                with self.assertRaises(ValueError):
                    installer.validate_cluster_upgrade(plan, SimpleNamespace(upgrade=True))
        with patch.object(installer.cluster, 'reserved', return_value=False):
            with self.assertRaises(ValueError):
                installer.validate_cluster_upgrade(good, SimpleNamespace(upgrade=True))
        self.assertEqual(installer.main(['--plan', '--cluster-maintenance']), 1)

    def test_protected_rollback_selects_only_the_current_protected_upgrade_receipt(self):
        stamp = '20261122T120000000002Z'
        root = Path('/opt/proxypermit')
        marker = root / 'installed.json'
        previous = root / 'releases/20261122T120000000001Z'
        current = root / 'releases' / stamp
        installed = dict(mode='ha', release=str(current), backup=str(root / 'backups' / stamp))
        receipt = dict(protectedUpgrade=dict(before=str(previous), after=str(current)))
        with patch.object(installer, 'installed_deployment', return_value=(installed, None, None, marker)), \
                patch.object(installer, 'require_trusted_path'), \
                patch.object(installer.cluster_setup, 'protected', return_value=receipt) as read:
            self.assertEqual(installer.cluster_rollback_release(str(root), stamp), previous)
            read.assert_called_once_with(root / 'backups' / stamp / 'service-state.json', private=True)
            with self.assertRaisesRegex(ValueError, 'currently installed'):
                installer.cluster_rollback_release(str(root), previous.name)
            receipt['protectedUpgrade']['after'] = str(previous)
            with self.assertRaisesRegex(ValueError, 'does not match'):
                installer.cluster_rollback_release(str(root), stamp)
            receipt.clear()
            with self.assertRaisesRegex(ValueError, 'no protected-upgrade record'):
                installer.cluster_rollback_release(str(root), stamp)

    def test_cluster_agent_upgrade_tracks_only_the_previous_release_agent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            previous = root / 'previous.py'
            candidate = root / 'candidate.py'
            agent = root / 'vip'
            previous.write_bytes(b'old agent\r\n')
            candidate.write_bytes(b'new agent\n')
            agent.write_bytes(b'local custom agent\n')
            transaction = installer.Transaction(root / 'backup')
            with patch.object(installer, 'require_trusted_path'):
                installer.upgrade_cluster_agent(transaction, agent, previous, candidate)
                self.assertEqual(agent.read_bytes(), b'local custom agent\n')
                self.assertEqual(transaction.entries, [])

                agent.write_bytes(b'old agent\n')
                installer.upgrade_cluster_agent(transaction, agent, previous, candidate)
                self.assertEqual(agent.read_bytes(), candidate.read_bytes())
                self.assertEqual(len(transaction.entries), 1)
                transaction.restore()
                self.assertEqual(agent.read_bytes(), b'old agent\n')

    def test_detached_cleanup_requires_persistent_service_masks_and_no_local_vip(self):
        from scripts.deploy import ha_resource
        checkpoint = dict(version=1, phase='active', name='node-b', planHash='a' * 64)
        profile = dict(address='192.0.2.10', vip='192.0.2.10/24', interface='ens18')
        with patch.object(installer.cluster_setup, 'protected', return_value=checkpoint), \
                patch.object(installer.socket, 'gethostname', return_value='node-b'), \
                patch.object(ha_resource, 'profile', return_value=profile), \
                patch.object(installer, 'local_ipv4_addresses', return_value={'192.0.2.12'}) as addresses, \
                patch.object(installer, 'run') as run:
            run.return_value.stdout = 'ActiveState=inactive\nUnitFileState=masked\n'
            self.assertEqual(installer.detached_cluster(), (checkpoint, profile))
            self.assertEqual(run.call_count, 4)
            for unsafe in ('ActiveState=active\nUnitFileState=masked\n',
                           'ActiveState=inactive\nUnitFileState=masked-runtime\n',
                           'ActiveState=inactive\nUnitFileState=disabled\n'):
                run.return_value.stdout = unsafe
                with self.assertRaisesRegex(ValueError, 'permanently mask'):
                    installer.detached_cluster()
            run.return_value.stdout = 'ActiveState=inactive\nUnitFileState=masked\n'
            addresses.return_value = {'192.0.2.10', '192.0.2.12'}
            with self.assertRaisesRegex(ValueError, 'still has'):
                installer.detached_cluster()

    def test_detached_uninstall_requires_matching_retirement_and_stopped_proxy(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(installer, 'detached_cluster', return_value=({'planHash': 'a' * 64}, {})), \
                patch.object(installer, 'HA_FIREWALL_STATE', Path(folder) / 'ha-firewall.json'), \
                patch.object(installer.cluster_setup, 'protected') as read, \
                patch.object(installer, 'run') as run:
            run.return_value.stdout = 'ActiveState=inactive\nUnitFileState=disabled\n'
            read.return_value = dict(version=1, planHash='a' * 64)
            installer.require_retired_cluster()
            read.return_value['planHash'] = 'b' * 64
            with self.assertRaisesRegex(ValueError, 'Complete detached HA retirement'):
                installer.require_retired_cluster()
            read.return_value['planHash'] = 'a' * 64
            run.return_value.stdout = 'ActiveState=failed\nUnitFileState=disabled\n'
            with self.assertRaisesRegex(ValueError, 'Complete detached HA retirement'):
                installer.require_retired_cluster()

    def test_cluster_reservation_blocks_independent_service_mutations(self):
        from app.core import cluster
        with patch.object(cluster, 'reserved', return_value=True), \
                patch.object(installer, 'require_linux_root'), \
                patch.object(installer, 'maintenance_lock', return_value=nullcontext()), \
                patch.object(installer, 'run') as run:
            for action in (lambda: installer.apply({}, None), lambda: installer.uninstall('/opt/proxypermit'),
                           lambda: installer.build_ha_retirement_plan('/opt/proxypermit')):
                with self.assertRaises(ValueError):
                    action()
            run.assert_not_called()

    @unittest.skipUnless(os.name != 'nt' and shutil.which('setfacl'), 'POSIX ACL tools required.')
    def test_recovery_restores_acl_and_its_original_absence(self):
        import subprocess
        from app.core.files import access_acl
        for original_acl in (False, True):
            with self.subTest(original_acl=original_acl), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'native.conf'
                path.write_text('original')
                path.chmod(0o640)
                if original_acl:
                    subprocess.run(['setfacl', '--no-mask', '-m', 'u:42424:r--', str(path)], check=True)
                before = access_acl(path)
                transaction = installer.Transaction(Path(directory) / 'backup')
                transaction.track(path)
                subprocess.run(['setfacl', '--no-mask', '-m', 'u:42425:r--', str(path)], check=True)
                transaction.restore()
                self.assertEqual(access_acl(path), before)
                self.assertEqual(path.stat().st_mode & 0o777, 0o640)

    @unittest.skipUnless(os.name != 'nt' and shutil.which('setfacl'), 'POSIX ACL tools required.')
    def test_reload_acl_does_not_unmask_custom_denied_readers(self):
        import subprocess
        from app.core.files import access_acl
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'native.conf'
            path.write_text('User nobody\n')
            subprocess.run(['setfacl', '-m', 'u:42424:r--,m::---', str(path)], check=True)
            before = access_acl(path)
            with patch.object(installer, 'run') as run:
                with self.assertRaises(ValueError):
                    installer.configure_reload_access(path, path)
            self.assertEqual(access_acl(path), before)
            run.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'POSIX directory and executable permissions.')
    def test_private_source_permissions_do_not_block_runtime_or_expose_existing_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / 'existing-secret'
            secret.write_text('isolated test value')
            secret.chmod(0o600)
            release = root / 'new-release'
            module = release / 'app' / 'web'
            module.mkdir(parents=True, mode=0o700)
            code = module / 'manager.py'
            code.write_text('pass\n')
            code.chmod(0o600)
            script = release / 'install.sh'
            script.write_text('#!/bin/sh\n')
            script.chmod(0o700)
            installer.prepare_release_permissions(release)
            self.assertEqual(module.stat().st_mode & 0o777, 0o755)
            self.assertEqual(code.stat().st_mode & 0o777, 0o644)
            self.assertEqual(script.stat().st_mode & 0o777, 0o755)
            self.assertEqual(secret.stat().st_mode & 0o777, 0o600)

    @unittest.skipIf(os.name == 'nt', 'POSIX symlinks.')
    def test_release_permission_normalization_does_not_follow_external_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / 'existing-secret'
            secret.write_text('isolated test value')
            secret.chmod(0o600)
            release = root / 'new-release'
            release.mkdir()
            (release / 'unexpected-link').symlink_to(secret)
            with self.assertRaises(ValueError):
                installer.prepare_release_permissions(release)
            self.assertEqual(secret.stat().st_mode & 0o777, 0o600)

    def test_health_helper_upgrade_is_reversible_and_preserves_custom_helpers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'health'
            transaction = installer.Transaction(Path(directory) / 'backup')
            path.write_bytes(installer.LEGACY_KEEPALIVED_HEALTH_TEXT.encode('utf-8'))
            path.chmod(0o755)
            with patch.object(installer, 'require_trusted_path'):
                self.assertTrue(installer.upgrade_health_helper(transaction, path))
                self.assertEqual(path.read_text(), installer.KEEPALIVED_HEALTH_TEXT)
                transaction.restore()
                self.assertEqual(path.read_text(), installer.LEGACY_KEEPALIVED_HEALTH_TEXT)
                path.write_text('#!/bin/sh\nexit 1\n', encoding='utf-8')
                self.assertFalse(installer.upgrade_health_helper(transaction, path))
                self.assertEqual(path.read_text(), '#!/bin/sh\nexit 1\n')
                path.write_bytes(b'\x7fELF\xff')
                self.assertFalse(installer.upgrade_health_helper(transaction, path))
                self.assertEqual(path.read_bytes(), b'\x7fELF\xff')

    def test_recovery_removes_created_tls_directory_without_deleting_unknown_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tls = root / 'tls'
            transaction = installer.Transaction(root / 'backup')
            transaction.track(tls)
            transaction.track(tls / 'cert.pem')
            tls.mkdir()
            (tls / 'cert.pem').write_text('example certificate')
            transaction.restore()
            self.assertFalse(tls.exists())
            tls.mkdir()
            (tls / 'unknown').write_text('operator file')
            with self.assertRaises(OSError):
                transaction.restore()
            self.assertEqual((tls / 'unknown').read_text(), 'operator file')

    @unittest.skipIf(os.name == 'nt', 'The installer manages Linux symlinks and systemd.')
    def test_uninstall_is_scoped_and_can_restore_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / 'opt/proxypermit'
            base.mkdir(parents=True)
            release = '/opt/proxypermit/releases/fixture'
            marker = base / 'installed.json'
            marker.write_text(json.dumps({'release': release, 'mode': 'ha'}))
            current = base / 'current'
            current.symlink_to(release)
            unit = root / 'etc/systemd/system/proxypermit.service'
            unit.parent.mkdir(parents=True)
            unit.write_text('WorkingDirectory=/opt/proxypermit/current\n')
            config = root / 'etc/tinyproxy.conf'
            config.write_text('Port 3128\n')
            sudoers = root / 'etc/sudoers.d/proxypermit'
            sudoers.parent.mkdir()
            sudoers.write_text('# Administrator-managed rule\n')

            plan = installer.build_uninstall_plan(root, '/opt/proxypermit')
            self.assertEqual(plan['paths'], list(map(str, (unit, current, marker))))
            self.assertTrue(current.is_symlink())
            previous = {'active': True, 'enabled': False}
            fragment = subprocess.CompletedProcess([], 0, stdout=(
                'FragmentPath=/etc/systemd/system/proxypermit.service\n'
                f'WorkingDirectory={base}/current\n'
            ))
            with patch.multiple(installer, require_linux_root=lambda _: None, maintenance_lock=nullcontext,
                                build_uninstall_plan=lambda *_: plan, service_state=lambda _: previous):
                foreign = subprocess.CompletedProcess([], 0, stdout='FragmentPath=/run/systemd/system/proxypermit.service\n')
                with patch.object(installer, 'run', return_value=foreign) as command:
                    with self.assertRaisesRegex(ValueError, 'different unit file'):
                        installer.uninstall(str(base))
                    command.assert_called_once()
                self.assertFalse((base / 'backups').exists())
                with patch.object(installer, 'run', side_effect=[
                    fragment, None, subprocess.CalledProcessError(1, 'daemon-reload'), None,
                ]), patch.object(installer, 'restore_auxiliary_services') as restore_services:
                    with self.assertRaises(subprocess.CalledProcessError):
                        installer.uninstall(str(base))
                    restore_services.assert_called_once_with({'proxypermit.service': previous})
                self.assertTrue(current.is_symlink())
                self.assertTrue(unit.is_file())
                self.assertTrue(marker.is_file())
                with patch.object(installer, 'run', side_effect=[fragment, None, None]) as command:
                    installer.uninstall(str(base))
                    command.assert_any_call('systemctl', 'disable', '--now', 'proxypermit.service')
                self.assertFalse(current.is_symlink())
                self.assertFalse(unit.exists())
                self.assertFalse(marker.exists())
                self.assertEqual(config.read_text(), 'Port 3128\n')
                self.assertTrue(sudoers.is_file())
                self.assertEqual(len(list((base / 'backups').glob('*/manifest.json'))), 2)
            backup = max(path.parent.name for path in (base / 'backups').glob('*/manifest.json'))
            with (
                patch.multiple(installer, require_linux_root=lambda _: None, maintenance_lock=nullcontext,
                               manager_values=lambda *_: {}, probe_manager=lambda _: True),
                patch.object(installer, 'run') as command,
                patch.object(installer.subprocess, 'run') as systemctl,
            ):
                installer.rollback_install(str(base), backup, execute=True)
                command.assert_any_call('systemctl', 'start', 'proxypermit.service')
                systemctl.assert_any_call(['systemctl', 'disable', 'proxypermit.service'], check=False)
            self.assertEqual(os.readlink(current), release)
            self.assertTrue(unit.is_file())
            self.assertTrue(marker.is_file())

    def test_upgrade_and_uninstall_refuse_an_unmanaged_path(self):
        with tempfile.TemporaryDirectory() as directory:
            for operation in ('--upgrade', '--uninstall'):
                with self.subTest(operation=operation):
                    self.assertEqual(installer.main([operation, '--root', directory]), 1)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_new_ha_plans_both_nodes_with_reciprocal_vrrp(self):
        with tempfile.TemporaryDirectory() as directory:
            for node, local, peer, priority in (('a', '192.0.2.11', '192.0.2.12', 150),
                                                ('b', '192.0.2.12', '192.0.2.11', 100)):
                with self.subTest(node=node):
                    ha = installer.ha_topology(node, 'ens18', '192.0.2.11', '192.0.2.12',
                                               '192.0.2.10/24', '198.51.100.0/24', 51)
                    plan = installer.build_plan(directory, '/opt/proxypermit', 'ha', ha=ha)
                    self.assertEqual(plan['action'], 'install')
                    config = installer.keepalived_text(plan['ha'])
                    self.assertIn(f'unicast_src_ip {local}', config)
                    self.assertIn(f'unicast_peer {{\n        {peer}', config)
                    self.assertIn(f'priority {priority}', config)
                    self.assertIn('192.0.2.10/24 dev ens18', config)
                    self.assertIn('script_user nobody', config)
                    incoming, peer_in, peer_out = plan['firewallRules']
                    self.assertEqual(incoming[:12], ['allow', 'in', 'on', 'ens18', 'from', '198.51.100.0/24',
                                                     'to', '192.0.2.10', 'port', '3128', 'proto', 'tcp'])
                    self.assertEqual(peer_in[4:10], ['from', peer, 'to', local, 'proto', 'vrrp'])
                    self.assertEqual(peer_out[4:10], ['from', local, 'to', peer, 'proto', 'vrrp'])
                    self.assertIn(' HA IN - ', peer_in[-1])
                    self.assertIn(' HA OUT - ', peer_out[-1])
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_firewall_dry_runs_finish_before_any_rule_is_added(self):
        rules = [['allow', 'in'], ['allow', 'out']]
        existing = subprocess.CompletedProcess([], 0, stdout='')
        with patch.object(installer, 'run', side_effect=[existing, None, subprocess.CalledProcessError(1, 'ufw')]) as command:
            with self.assertRaises(subprocess.CalledProcessError):
                installer.apply_ha_firewall(rules)
            self.assertEqual(command.call_count, 3)
            command.assert_any_call('ufw', '--dry-run', 'allow', 'in', capture_output=True)
            command.assert_any_call('ufw', '--dry-run', 'allow', 'out', capture_output=True)

    def test_firewall_rejects_replacing_an_existing_rule(self):
        existing = subprocess.CompletedProcess([], 0, stdout="ufw allow 3128/tcp comment 'Original'\n")
        changed = subprocess.CompletedProcess([], 0, stdout="ufw allow 3128/tcp comment 'Replacement'\n")
        with patch.object(installer, 'run', side_effect=[existing, None, None, changed]):
            with self.assertRaisesRegex(ValueError, 'existing UFW rule conflicts'):
                installer.apply_ha_firewall([['allow', '3128/tcp', 'comment', 'Replacement']])

    def test_ha_firewall_cleanup_preserves_unrelated_rules(self):
        owned = "ufw allow in from 192.0.2.1 to 192.0.2.2 proto vrrp comment 'ProxyPermit HA IN - Keepalived VRRP from peer'"
        unrelated = 'ufw allow 22/tcp'
        with patch.object(installer, 'ufw_added_rules', side_effect=[{owned, unrelated}, {unrelated}]), \
                patch.object(installer, 'run') as command:
            installer.remove_owned_ha_firewall([owned])
            command.assert_called_once_with('ufw', '--force', 'delete', 'allow', 'in', 'from', '192.0.2.1',
                                            'to', '192.0.2.2', 'proto', 'vrrp', 'comment',
                                            'ProxyPermit HA IN - Keepalived VRRP from peer')

    def test_detached_firewall_cleanup_preserves_pinned_management_rules(self):
        owned = "ufw allow in from 192.0.2.1 to 192.0.2.2 port 5405 proto udp comment 'ProxyPermit cluster IN - Corosync peer'"
        management = "ufw allow in from 192.0.2.3 to any port 22 proto tcp comment 'ProxyPermit access safety IN - SSH'"
        with patch.object(installer, 'ufw_added_rules', side_effect=[{owned, management}, {management}]), \
                patch.object(installer, 'run') as command:
            installer.remove_owned_ha_firewall([owned], include_cluster=True)
            self.assertEqual(command.call_args.args[-1], 'ProxyPermit cluster IN - Corosync peer')
        with patch.object(installer, 'ufw_added_rules', return_value={owned, management}), \
                patch.object(installer, 'run') as command:
            with self.assertRaisesRegex(ValueError, 'firewall rule record is invalid'):
                installer.remove_owned_ha_firewall([owned, management], include_cluster=True)
            command.assert_not_called()

    def test_retirement_refuses_to_stop_services_when_vip_is_local(self):
        plan = {'vip': '192.0.2.10', 'peer': '192.0.2.12'}
        addresses = subprocess.CompletedProcess([], 0, stdout=json.dumps([
            {'addr_info': [{'local': plan['vip']}]},
        ]))
        with (
            patch.multiple(installer, require_linux_root=lambda _: None, maintenance_lock=nullcontext,
                           build_ha_retirement_plan=lambda _, **_options: plan, verify_live_manager=lambda _: None),
            patch.object(installer, 'run', return_value=addresses) as command,
            patch.object(installer.socket, 'create_connection') as connection,
        ):
            with self.assertRaisesRegex(ValueError, 'Move the virtual IP'):
                installer.retire_ha_node('/opt/proxypermit')
            command.assert_called_once_with('ip', '-j', '-4', 'address', 'show', capture_output=True, text=True)
            connection.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'The installer manages Linux ownership and symlinks.')
    def test_privileged_paths_reject_symlinks_and_writable_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / 'runtime'
            runtime.mkdir()
            runtime.chmod(0o777)
            with self.assertRaisesRegex(ValueError, 'root-owned path'):
                installer.require_trusted_path(runtime, directory=True)
            lock = runtime / 'install.lock'
            lock.symlink_to(root / 'target')
            with self.assertRaisesRegex(ValueError, 'root-owned path'):
                installer.require_trusted_path(lock, directory=False)
            self.assertFalse((root / 'target').exists())

    def test_new_ha_rejects_unsafe_or_incomplete_topology(self):
        args = ['a', 'ens18', '192.0.2.11', '192.0.2.12', '192.0.2.10/24', '198.51.100.0/24', 51]
        for index, invalid in ((1, 'ens18\nfoo'), (3, '192.0.2.11'), (4, '203.0.113.5/24'),
                               (5, '0.0.0.0/0'), (6, 256)):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                values = args.copy()
                values[index] = invalid
                installer.ha_topology(*values)
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            installer.build_plan(directory, '/opt/proxypermit', 'ha')

    def test_fresh_plan_is_standalone_and_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = installer.build_plan(directory, '/opt/proxypermit')
            self.assertEqual(plan['mode'], 'standalone')
            self.assertEqual(plan['action'], 'install')
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_existing_ha_topology_is_discovered_and_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / 'opt/proxypermit/.env'
            env.parent.mkdir(parents=True)
            content = 'NODE0_ADDRESS=192.0.2.1\nNODE1_ADDRESS=192.0.2.2\nFLASK_SECRET_KEY=keep-me\n'
            env.write_text(content)
            plan = installer.build_plan(directory, '/opt/proxypermit')
            self.assertEqual(plan['mode'], 'ha')
            self.assertEqual(plan['action'], 'upgrade')
            self.assertEqual(env.read_text(), content)
            with self.assertRaises(ValueError):
                installer.build_plan(directory, '/opt/proxypermit', 'standalone')

    def test_rollback_restores_existing_and_removes_new_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, new = root / 'old.conf', root / 'new.conf'
            old.write_text('before')
            transaction = installer.Transaction(root / 'backup')
            transaction.track(old)
            transaction.track(new)
            old.write_text('after')
            new.write_text('new')
            transaction.restore()
            self.assertEqual(old.read_text(), 'before')
            self.assertFalse(new.exists())

    def test_standalone_service_uses_account_and_loopback(self):
        service = installer.service_text('/opt/proxypermit/current', 'standalone', '127.0.0.1', 8080)
        self.assertIn('User=proxypermit', service)
        self.assertIn('GUNICORN_BIND=127.0.0.1:8080', service)

    def test_upgrade_preserves_existing_nginx_socket_or_tls_binding(self):
        service = installer.service_text('/opt/proxypermit/current', 'ha', '127.0.0.1', 8080, preserve_binding=True)
        self.assertNotIn('Environment=GUNICORN_BIND=', service)
        self.assertNotIn('Environment=GUNICORN_USE_TLS=', service)

    def test_invalid_rollback_path_is_rejected(self):
        with self.assertRaises(ValueError):
            installer.rollback_install('/opt/proxypermit', '../../etc')

    def test_server_selection_is_explicit_and_preserved_on_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / 'etc/proxypermit/.env'
            env.parent.mkdir(parents=True)
            env.write_text('APP_SERVER=granian\n')
            self.assertEqual(installer.build_plan(directory, '/opt/proxypermit')['server'], 'granian')
            self.assertEqual(installer.build_plan(directory, '/opt/proxypermit', requested_server='gunicorn')['server'], 'gunicorn')

    def test_binding_from_old_systemd_unit_survives_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            env, unit = Path(directory) / '.env', Path(directory) / 'service'
            env.write_text('APP_SERVER=granian\n')
            unit.write_text('Environment=GUNICORN_BIND=127.0.0.1:8098\nEnvironment=GUNICORN_USE_TLS=false\n')
            self.assertEqual(installer.manager_values(env, unit)['GUNICORN_BIND'], '127.0.0.1:8098')
            env.write_text('GUNICORN_BIND=unix:/run/proxypermit/app.sock\n')
            self.assertEqual(installer.manager_values(env, unit)['GUNICORN_BIND'], 'unix:/run/proxypermit/app.sock')


if __name__ == '__main__':
    unittest.main()
