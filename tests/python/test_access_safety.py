"""Anti-lockout boundaries, local HA exceptions and protected UFW ownership."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from unittest.mock import Mock
import io
from contextlib import redirect_stdout

from app.core.access_safety import ensure_access, firewall_rules, protect_configuration, without_safety
from app.core.ha_policy import baseline, merge_policy, portable_config
from app.core.tinyproxy import ConfigError
from scripts.deploy import access_safety as deployment
from scripts.deploy.install import Transaction
from scripts.deploy import install


class AccessSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'safety.json'
        self.environment = patch.dict(os.environ, PROXYPERMIT_ACCESS_SAFETY_FILE=str(self.path), DEMO_MODE='true')
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.profile = dict(version=1, managementNetworks=['192.0.2.8/32'],
                            proxyNetworks=['127.0.0.1/32', '192.0.2.8/32', '::1/128'],
                            sshPorts=[22], webPorts=[443], proxyPort=3128, listeners=['0.0.0.0'])
        self.path.write_text(json.dumps(self.profile), encoding='utf-8')
        self.original = 'Port 3128\nListen 0.0.0.0\nAllow 198.51.100.0/24\nDeny 0.0.0.0/0\n'
        self.safe = protect_configuration(self.original, self.profile)

    def test_protected_prefix_and_binding_changes_cannot_lock_out_management(self):
        ensure_access(self.safe)
        for change in (self.original, self.safe.replace('Port 3128', 'Port 4000'),
                       self.safe.replace('Listen 0.0.0.0', 'Listen 127.0.0.1'),
                       'Deny 192.0.2.8\n' + self.safe,
                       self.safe.replace('Allow 192.0.2.8/32\n', '')):
            with self.assertRaises(ConfigError):
                ensure_access(change)

    def test_local_exceptions_survive_shared_policy_and_do_not_change_replication_baseline(self):
        self.assertEqual(without_safety(self.safe), self.original)
        self.assertEqual(baseline(self.safe, '# rules\n'), baseline(self.original, '# rules\n'))
        self.assertEqual(portable_config(self.safe), portable_config(self.original))
        updated = merge_policy(self.safe, 'Allow 203.0.113.0/24\nDeny 0.0.0.0/0\n')
        ensure_access(updated)
        self.assertIn('Allow 192.0.2.8/32', updated)
        self.assertIn('Allow 203.0.113.0/24', updated)
        # A shared setting before the local block must not move shared ACLs
        # ahead of the fixed management exception during reconciliation.
        updated = merge_policy('Timeout 600\n' + self.safe,
                               'Timeout 500\nDeny 0.0.0.0/0\n')
        ensure_access(updated)
        self.assertLess(updated.index('Allow 192.0.2.8/32'), updated.index('Deny 0.0.0.0/0'))

    def test_firewall_exceptions_are_narrow_additive_and_not_owned_by_ha_cleanup(self):
        rules = firewall_rules(self.profile)
        self.assertEqual(len(rules), 3)
        for rule in rules:
            self.assertEqual(rule[:3], ['prepend', 'allow', 'in'])
            self.assertIn('192.0.2.8/32', rule)
            self.assertTrue(rule[-1].startswith('ProxyPermit SAFETY IN - '))
            self.assertFalse(rule[-1].startswith('ProxyPermit HA '))
        self.assertEqual({rule[rule.index('port') + 1] for rule in rules}, {'22', '443', '3128'})
        safety = "ufw allow in from 192.0.2.8 to any port 22 proto tcp comment 'ProxyPermit SAFETY IN - SSH management'"
        with patch.object(install, 'ufw_added_rules', return_value={safety}), patch.object(install, 'run') as command:
            with self.assertRaises(ValueError):
                install.remove_owned_ha_firewall([safety])
            command.assert_not_called()

    def test_unconfigured_installs_are_compatible_and_broad_profiles_fail_closed(self):
        self.path.unlink()
        ensure_access(self.original)
        with self.assertRaises(ConfigError):
            ensure_access(self.safe)

        self.profile['managementNetworks'] = ['0.0.0.0/0']
        self.path.write_text(json.dumps(self.profile), encoding='utf-8')
        with self.assertRaises(ConfigError):
            ensure_access(self.safe)

    def test_installer_checks_all_rules_before_applying_and_can_restore_profile_and_config(self):
        root = Path(self.temporary.name)
        config, filters = root / 'tinyproxy.conf', root / 'filter.txt'
        config.write_text(self.original, encoding='utf-8')
        filters.write_text('# example\n', encoding='utf-8')
        self.path.unlink()
        ufw = [root / 'user.rules', root / 'user6.rules']
        for path in ufw:
            path.write_text('# existing firewall\n', encoding='utf-8')
        host = SimpleNamespace(run=Mock(), require_trusted_path=Mock(), chown=Mock(), UFW_RULE_FILES=ufw)
        args = SimpleNamespace(management_cidr=['192.0.2.8/32'], management_ssh_port=[22], management_web_port=[443], manager_port=8080)
        transaction = Transaction(root / 'backup')
        transaction.track(config)
        with patch.object(deployment, 'PROFILE', self.path):
            self.assertTrue(deployment.configure(host, transaction, config, filters, args, {}, 0))
        ensure_access(config.read_text())
        commands = [call.args for call in host.run.call_args_list]
        self.assertEqual(len(commands), 6)
        self.assertTrue(all(command[:2] == ('ufw', '--dry-run') for command in commands[:3]))
        self.assertTrue(all(command[:2] == ('ufw', 'prepend') for command in commands[3:]))
        transaction.restore()
        self.assertFalse(self.path.exists())
        self.assertEqual(config.read_text(), self.original)

    def test_firewall_validation_failure_preserves_configuration_and_profile(self):
        root = Path(self.temporary.name)
        config, filters = root / 'tinyproxy.conf', root / 'filter.txt'
        config.write_text(self.safe, encoding='utf-8')
        filters.write_text('# example\n', encoding='utf-8')
        previous = self.path.read_bytes()
        host = SimpleNamespace(run=Mock(side_effect=ValueError('Firewall rejected rule')),
                               require_trusted_path=Mock(), chown=Mock(), UFW_RULE_FILES=[])
        args = SimpleNamespace(management_cidr=['198.51.100.8/32'], management_ssh_port=[2222],
                               management_web_port=[8443], manager_port=8080)
        with patch.object(deployment, 'PROFILE', self.path):
            with self.assertRaisesRegex(ValueError, 'Firewall rejected'):
                deployment.configure(host, Transaction(root / 'backup'), config, filters, args, {}, 0)
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertEqual(config.read_text(), self.safe)
        self.assertEqual(host.run.call_args.args[:2], ('ufw', '--dry-run'))
        host.chown.assert_not_called()

    def test_non_root_wizard_keeps_reviewed_access_sources_and_real_host_ports(self):
        args = SimpleNamespace(install_dir='/opt/proxypermit', admin_user='admin', manager_port=8080,
                               management_cidr=None, management_ssh_port=None, management_web_port=None)
        plan = dict(action='install', mode='standalone', server='gunicorn')
        terminal = Mock()
        terminal.choose.side_effect = [0, 0, 1]
        terminal.ask.side_effect = ['admin', 8080, '192.0.2.8/32', 2222, 443]
        output = io.StringIO()
        with patch.object(install, 'InstallTerminal', return_value=terminal), \
                patch.object(install, 'build_plan', return_value=plan), \
                patch.object(install.sys, 'platform', 'linux'), \
                patch.object(install.os, 'geteuid', return_value=1000, create=True), \
                patch.dict(os.environ, SSH_CONNECTION=''), redirect_stdout(output):
            self.assertEqual(install.run_wizard(args), 0)
        self.assertIn('--management-cidr 192.0.2.8/32', output.getvalue())
        self.assertIn('--management-ssh-port 2222', output.getvalue())
        self.assertIn('--management-web-port 443', output.getvalue())
        self.assertIsNone(terminal.ask.call_args_list[2].args[1])
