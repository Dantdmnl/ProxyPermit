import importlib.util
import json
import os
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

    def test_retirement_refuses_to_stop_services_when_vip_is_local(self):
        plan = {'vip': '192.0.2.10', 'peer': '192.0.2.12'}
        addresses = subprocess.CompletedProcess([], 0, stdout=json.dumps([
            {'addr_info': [{'local': plan['vip']}]},
        ]))
        with (
            patch.multiple(installer, require_linux_root=lambda _: None, maintenance_lock=nullcontext,
                           build_ha_retirement_plan=lambda _: plan, verify_live_manager=lambda _: None),
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
