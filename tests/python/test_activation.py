"""Reload confirmation must fail closed without a disruptive restart fallback."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.core.activation import acknowledged_reload, activation_action, service_activation_action


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / 'tinyproxy.conf'
        self.config.write_text('Port 3128\nAllow 127.0.0.1\nSyslog Yes\nLogLevel Connect\n', encoding='utf-8')
        self.commands = []
        self.message = 'Reloading config file finished'
        self.pid = '42'
        self.invocation = 'test-invocation'
        self.stale = False

    def command(self, *args):
        self.commands.append(args)
        if args[0] == '/usr/bin/tinyproxy':
            value = 'tinyproxy 1.11.3\n'
        elif 'show' in args:
            value = 'MainPID=42\nInvocationID=test-invocation\n'
        elif args[0] == '/usr/bin/journalctl':
            record = {'_PID': self.pid, 'MESSAGE': self.message,
                                '_SYSTEMD_INVOCATION_ID': self.invocation,
                                '__MONOTONIC_TIMESTAMP': '1' if self.stale else str(time.monotonic_ns() // 1000 + 1000)}
            value = json.dumps({**record, 'MESSAGE': 'Reloading config file'}) + '\n' + json.dumps(record) if any(arg.startswith('--after-cursor=') for arg in args) else json.dumps({'__CURSOR': 'cursor-before'})
        else:
            value = ''
        return SimpleNamespace(stdout=value)

    def reload(self, **kwargs):
        with patch('app.core.activation._run', side_effect=self.command), \
                patch('app.core.activation.proxy_responsive', return_value=True), \
                patch.dict('os.environ', {'SERVICE_CONTROL_SUDO': 'true'}):
            return acknowledged_reload(self.config, **kwargs)

    def test_only_proven_filter_options_reload(self):
        base = self.config.read_text(encoding='utf-8') + 'FilterType ere\nFilterDefaultDeny Yes\n'
        self.assertEqual(activation_action(base, base.replace('Deny Yes', 'Deny No')), 'reload')
        for changed in (base.replace('3128', '3129'), base.replace('ere', 'fnmatch'), base + 'Timeout 600\n'):
            self.assertEqual(activation_action(base, changed), 'restart')

    def test_reload_requires_explicit_host_opt_in(self):
        base = self.config.read_text(encoding='utf-8')
        with patch.dict('os.environ', {'TINYPROXY_RELOAD_ENABLED': 'false'}):
            self.assertEqual(service_activation_action(base, base), 'restart')
        with patch.dict('os.environ', {'TINYPROXY_RELOAD_ENABLED': 'true'}):
            self.assertEqual(service_activation_action(base, base), 'reload')

    def test_acknowledgement_and_health_confirm_reload(self):
        self.assertEqual(self.reload(), (True, None))
        signal = next(command for command in self.commands if 'kill' in command)
        self.assertEqual(signal, ('/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'kill', '--kill-whom=main', '--signal=SIGUSR1', 'tinyproxy.service'))
        self.assertTrue(all('--namespace=proxypermit-traffic' in command for command in self.commands if command[0] == '/usr/bin/journalctl'))
        cursor_reads = [command for command in self.commands if any(arg.startswith('--after-cursor=') for arg in command)]
        self.assertTrue(cursor_reads)
        self.assertFalse(any(arg.startswith('--lines') for command in cursor_reads for arg in command))

    def test_short_service_alias_uses_the_same_narrow_canonical_signal_rule(self):
        self.assertTrue(self.reload(service='tinyproxy')[0])
        self.assertTrue(all(command[-1] == 'tinyproxy.service' for command in self.commands if 'kill' in command))

    def test_rejected_reload_never_restarts(self):
        self.message = 'Reloading config file failed!'
        success, error = self.reload()
        self.assertFalse(success)
        self.assertIn('rejected', error)
        self.assertFalse(any('restart' in command for command in self.commands))

    def test_wrong_pid_cannot_acknowledge(self):
        self.pid = '43'
        with patch('app.core.activation.time.monotonic', side_effect=[0, .1, 6]), patch('app.core.activation.time.sleep'):
            success, error = self.reload()
        self.assertFalse(success)
        self.assertIn('not acknowledged', error)

    def test_old_completion_from_same_process_is_not_success(self):
        self.stale = True
        with patch('app.core.activation.time.monotonic', side_effect=[0, .1, 6]), patch('app.core.activation.time.sleep'):
            success, error = self.reload()
        self.assertFalse(success)
        self.assertIn('not acknowledged', error)

    def test_different_service_invocation_cannot_acknowledge(self):
        self.invocation = 'another-invocation'
        with patch('app.core.activation.time.monotonic', side_effect=[0, .1, 6]), patch('app.core.activation.time.sleep'):
            self.assertFalse(self.reload()[0])

    def test_process_change_is_not_success(self):
        with patch('app.core.activation._identity', side_effect=[{'MainPID': '42'}, {'MainPID': '43'}]):
            success, error = self.reload()
        self.assertFalse(success)
        self.assertIn('changed process', error)

    def test_failed_health_is_not_success(self):
        with patch('app.core.activation._run', side_effect=self.command), patch('app.core.activation.proxy_responsive', return_value=False):
            success, error = acknowledged_reload(self.config)
        self.assertFalse(success)
        self.assertIn('health check', error)

    def test_unsupported_version_never_signals(self):
        with patch('app.core.activation._run', return_value=SimpleNamespace(stdout='tinyproxy 1.10.0')) as command:
            success, error = acknowledged_reload(self.config)
        self.assertFalse(success)
        self.assertIn('1.11.1 and 1.11.3 only', error)
        self.assertEqual(command.call_count, 1)

    def test_unreadable_filter_never_signals(self):
        self.config.write_text(self.config.read_text(encoding='utf-8') + f'Filter "{self.temp.name}/missing"\n', encoding='utf-8')
        self.assertFalse(self.reload()[0])
        self.assertFalse(any('kill' in command for command in self.commands))

    def test_missing_cursor_never_signals(self):
        with patch('app.core.activation._run', side_effect=self.command), patch('app.core.activation.json.loads', return_value={}):
            self.assertFalse(acknowledged_reload(self.config)[0])
        self.assertFalse(any('kill' in command for command in self.commands))
