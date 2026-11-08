import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('installer', Path(__file__).resolve().parents[2] / 'scripts/deploy/install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
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
            self.assertEqual(list(Path(directory).iterdir()), [])

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
