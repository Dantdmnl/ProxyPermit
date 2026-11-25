"""A protected VIP is healthy only with service, address and HTTP evidence."""
from contextlib import redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from subprocess import CompletedProcess, TimeoutExpired

from scripts.deploy import ha_resource as agent


class ProtectedResourceTests(unittest.TestCase):
    value = {'interface': 'ens3', 'address': '192.0.2.10'}

    def runner(self, active=True, vip=True, healthy=True, stop=True, mode='control-group', state=None):
        def command(*args, **_options):
            code, output = 0, ''
            if args[0] == 'ip':
                output = json.dumps([{'addr_info': [{'local': '192.0.2.10'}] if vip else []}])
            elif args[0] == 'runuser':
                code = 0 if healthy else 1
            elif args[:2] == ('systemctl', 'stop'):
                code = 0 if stop else 1
            elif args[-1] == '--property=LoadState,ActiveState':
                unit_state = state or ('active' if active else 'inactive')
                output = f'ActiveState={unit_state}\nLoadState=loaded\n'
            elif args[:2] == ('systemctl', 'show'):
                output = {'KillMode': mode, 'SendSIGKILL': 'yes', 'Restart': 'no'}[args[-2]]
            elif args[:2] == ('systemctl', 'is-enabled'):
                code, output = 1, 'disabled'
            return CompletedProcess(args, code, output, '')
        return command

    def test_monitor_requires_real_service_vip_and_proxy_health(self):
        for active, vip, healthy, expected in ((True, True, True, 0), (True, True, False, 1),
                (True, False, True, 1), (False, True, True, 1), (False, False, True, 7)):
            with self.subTest(active=active, vip=vip, healthy=healthy), \
                    patch.object(agent, 'command', side_effect=self.runner(active, vip, healthy)):
                self.assertEqual(agent.monitor(self.value), expected)

    def test_leftover_vip_or_failed_stop_never_counts_as_safe_stop(self):
        for vip, stop, expected in ((True, True, 1), (False, False, 1), (False, True, 0)):
            with patch.object(agent, 'command', side_effect=self.runner(False, vip, stop=stop)):
                self.assertEqual(agent.resource('stop', self.value), expected)

    def test_running_children_do_not_count_as_stopped(self):
        with patch.object(agent, 'command', side_effect=self.runner(active=True, vip=False)):
            self.assertEqual(agent.resource('stop', self.value), 1)

    def test_failed_or_transitioning_service_is_not_a_verified_stop(self):
        for state in ('failed', 'activating', 'deactivating', 'unknown'):
            with self.subTest(state=state), patch.object(
                    agent, 'command', side_effect=self.runner(vip=False, state=state)):
                self.assertEqual(agent.resource('stop', self.value), agent.ERROR)
                self.assertEqual(agent.monitor(self.value), agent.ERROR)

    def test_failed_or_unloaded_unit_status_cannot_confirm_inactivity(self):
        for code, load in ((1, 'loaded'), (0, 'not-found')):
            with self.subTest(code=code, load=load), patch.object(agent, 'command', return_value=CompletedProcess(
                    [], code, f'ActiveState=inactive\nLoadState={load}\n', '')):
                with self.assertRaisesRegex(ValueError, 'unit state could not be verified'):
                    agent.keepalived_state()

    def test_unsafe_kill_mode_is_rejected_before_start(self):
        with patch.object(agent, 'command', side_effect=self.runner(mode='process')) as command:
            with self.assertRaises(ValueError):
                agent.resource('start', self.value)
            self.assertFalse(any(call.args[:2] == ('systemctl', 'start') for call in command.call_args_list))

    def test_independent_boot_activation_is_rejected(self):
        original = self.runner()
        def command(*args):
            if args[:2] == ('systemctl', 'is-enabled'):
                return CompletedProcess(args, 0, 'enabled', '')
            return original(*args)
        with patch.object(agent, 'command', side_effect=command):
            with self.assertRaises(ValueError):
                agent.validate_service()

    def test_missing_interface_is_an_error(self):
        with patch.object(agent, 'command', return_value=CompletedProcess([], 1, '', '')):
            with self.assertRaises(ValueError):
                agent.owns_vip(self.value)

    def test_monitor_timeout_identifies_command_without_its_output(self):
        error = TimeoutExpired(('runuser', '-u', 'proxypermit'), 2, output='private probe output')
        logged = StringIO()
        with patch.object(agent.os, 'name', 'posix'), patch.object(agent.os, 'geteuid', return_value=0, create=True), \
                patch.object(agent, 'profile', return_value=self.value), \
                patch.object(agent, 'resource', side_effect=error), \
                patch.dict(os.environ, OCF_EXIT_REASON_PREFIX='test-ocf:'), redirect_stderr(logged):
            self.assertEqual(agent.main(['monitor']), agent.ERROR)
        self.assertTrue(logged.getvalue().startswith('test-ocf:'))
        self.assertIn('monitor timed out in runuser -u (limit 2 seconds)', logged.getvalue())
        self.assertNotIn('private probe output', logged.getvalue())

    def test_monitor_reports_a_missing_vip_in_pacemaker_exit_reason_format(self):
        logged = StringIO()
        with patch.object(agent, 'command', side_effect=self.runner(vip=False)), \
                patch.dict(os.environ, OCF_EXIT_REASON_PREFIX=''), redirect_stderr(logged):
            self.assertEqual(agent.resource('monitor', self.value), agent.ERROR)
        self.assertEqual(logged.getvalue(),
                         'ocf-exit-reason:The virtual IP is missing from the protected interface.\n')

    @unittest.skipUnless(os.name == 'posix' and getattr(os, 'geteuid', lambda: -1)() == 0, 'Root-owned Linux profile check.')
    def test_profile_rejects_writable_files_and_symlinks(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'profile.json'
            path.write_text(json.dumps(dict(version=1, interface='ens3', vip='192.0.2.10/24')))
            path.chmod(0o600)
            with patch.object(agent, 'PROFILE', path):
                self.assertEqual(agent.profile()['address'], '192.0.2.10')
                path.chmod(0o666)
                with self.assertRaises(ValueError):
                    agent.profile()
            link = path.with_name('link')
            link.symlink_to(path)
            with patch.object(agent, 'PROFILE', link):
                with self.assertRaises(OSError):
                    agent.profile()
