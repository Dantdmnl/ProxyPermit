"""Initial dependency preparation is inert, resumable and refuses existing clusters."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app.core import cluster_setup
from scripts.deploy import cluster_bootstrap as bootstrap


@unittest.skipUnless(os.name == 'posix' and getattr(os, 'geteuid', lambda: -1)() == 0, 'Root-only preparation fixtures.')
class ClusterBootstrapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plan = self.root / 'plan.json'
        self.plan.write_text(json.dumps(dict(version=1, platform='proxmox', witness='192.0.2.13',
            fenceAddress='192.0.2.100', nodes=[dict(name='node-a',address='192.0.2.11',target='101'),
                                             dict(name='node-b',address='192.0.2.12',target='102')])))
        self.plan.chmod(0o600)
        self.folder = self.root / 'state'
        self.units = self.root / 'units';self.units.mkdir()
        for item in (patch.object(cluster_setup, 'PLAN', self.plan), patch.object(bootstrap, 'FOLDER', self.folder),
                     patch.object(bootstrap, 'JOURNAL', self.folder / 'preparation.json'),
                     patch.object(bootstrap, 'UNIT_FOLDER', self.units), patch.object(bootstrap, 'CONFIG', self.root / 'corosync.conf'),
                     patch.object(bootstrap, 'CIB', self.root / 'cib.xml'), patch.object(bootstrap.cluster, 'reserved', return_value=False),
                     patch.object(bootstrap, 'maintenance_lock')):
            item.start();self.addCleanup(item.stop)
        self.commands = []
        self.fail_packages = False
        self.clock = 'yes'
        def run(*args, **_kwargs):
            self.commands.append(args)
            if args[0] == '/usr/sbin/ip':
                return subprocess.CompletedProcess(args,0,json.dumps([dict(addr_info=[dict(local='192.0.2.11')])]),'')
            if args[0] == '/usr/bin/timedatectl':
                return subprocess.CompletedProcess(args,0,'NTP=yes\nNTPSynchronized='+self.clock+'\n','')
            if 'is-active' in args:return subprocess.CompletedProcess(args,3,'','')
            if args[0] == '/usr/bin/apt-get' and self.fail_packages:raise ValueError('Simulated package outage')
            return subprocess.CompletedProcess(args,0,'','')
        mock = patch.object(bootstrap, 'run', side_effect=run);mock.start();self.addCleanup(mock.stop)

    def test_plan_only_reads_state_and_keeps_proxy_services_untouched(self):
        with redirect_stdout(io.StringIO()):self.assertEqual(bootstrap.main(['--role','node']),0)
        self.assertFalse(self.folder.exists())
        self.assertEqual(len(self.commands),2)

    def test_web_preparation_refuses_changed_plan_before_installing_anything(self):
        with self.assertRaisesRegex(ValueError, 'staged cluster plan changed'):
            bootstrap.prepare('node', expected_hash='a' * 64)
        self.assertFalse(self.folder.exists())
        self.assertFalse(any(command[0] == '/usr/bin/apt-get' for command in self.commands))

    def test_interruption_keeps_masks_and_resumes_without_starting_cluster_services(self):
        self.fail_packages = True
        with self.assertRaises(ValueError):bootstrap.prepare('node')
        self.assertEqual(json.loads(bootstrap.JOURNAL.read_text())['phase'],'installing')
        self.assertTrue(all((self.units / unit).is_symlink() for unit in bootstrap.UNITS['node']))
        self.fail_packages = False
        result = bootstrap.prepare('node')
        self.assertEqual(result['phase'],'ready')
        self.assertFalse(any((self.units / unit).is_symlink() for unit in bootstrap.UNITS['node']))
        self.assertFalse(any('start' in command or 'restart' in command or 'keepalived.service' in command
                             or 'tinyproxy.service' in command for command in self.commands))

    def test_existing_cluster_configuration_or_foreign_override_is_not_adopted(self):
        bootstrap.CONFIG.write_text('administrator configuration')
        with self.assertRaises(ValueError):bootstrap.prepare('node')
        self.assertFalse(bootstrap.JOURNAL.exists())
        bootstrap.CONFIG.unlink()
        (self.units / 'pacemaker.service').write_text('foreign unit')
        with self.assertRaises(ValueError):bootstrap.prepare('node')
        self.assertEqual((self.units / 'pacemaker.service').read_text(),'foreign unit')

    def test_clock_and_changed_plan_prevent_mutation_or_resume(self):
        self.clock = 'no'
        with self.assertRaises(ValueError):bootstrap.prepare('node')
        self.assertFalse(self.folder.exists())
        self.clock = 'yes';self.fail_packages = True
        with self.assertRaises(ValueError):bootstrap.prepare('node')
        value=json.loads(self.plan.read_text());value['witness']='192.0.2.14';self.plan.write_text(json.dumps(value))
        count=len(self.commands)
        with self.assertRaises(ValueError):bootstrap.prepare('node')
        self.assertFalse(any(command[0]=='/usr/bin/apt-get' for command in self.commands[count:]))
