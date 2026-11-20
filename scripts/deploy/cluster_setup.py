"""Inspect protected-cluster prerequisites or stage a secret-free OS plan.

This step never starts a cluster, changes the firewall, or powers off guests.
Staging is preparation, not permission to activate an unsafe cluster.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import time
from xml.etree import ElementTree as ET

from app.core import cluster_setup as setup
from app.core.files import sync_directory
from app.core.tinyproxy import atomic_write
from scripts.deploy.install import maintenance_lock, require_linux_root, require_trusted_path

ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'}
PACKAGES = ('pcs', 'corosync', 'pacemakerd', 'corosync-qdevice-tool')


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=3, check=True, env=ENV)
    if len(result.stdout.encode()) > 512 * 1024:
        raise ValueError('Cluster prerequisite response is too large.')
    return result.stdout


def metadata_parameters(agent):
    path = Path('/usr/sbin') / agent
    info = path.lstat()
    if not path.is_file() or path.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('The fencing agent must be an OS-protected executable.')
    text = command(str(path), '--action=metadata')
    if '<!DOCTYPE' in text or '<!ENTITY' in text:
        raise ValueError('Invalid fencing metadata.')
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise ValueError('Invalid fencing metadata.') from None
    if root.get('name') != agent:
        raise ValueError('Fencing agent identity does not match its metadata.')
    return {item.get('name') for item in root.findall('parameters/parameter')}


def inspect(values):
    checks = dict.fromkeys(setup.CHECKS, 'pending')
    checks['managedHa'] = 'pass' if values.get('PROXYPERMIT_MANAGED_HA') == 'true' else 'blocked'
    try:
        properties = dict(line.split('=', 1) for line in command('/usr/bin/timedatectl', 'show',
                          '--property=NTP', '--property=NTPSynchronized').splitlines() if '=' in line)
        checks['time'] = 'pass' if properties.get('NTP') == properties.get('NTPSynchronized') == 'yes' else 'blocked'
    except (OSError, ValueError, subprocess.SubprocessError):
        checks['time'] = 'unknown'
    checks['packages'] = 'pass' if all(shutil.which(name, path=ENV['PATH']) for name in PACKAGES) else 'blocked'
    try:
        plan = setup.validate_plan(setup.protected(setup.PLAN, private=True))
        addresses = [node['address'] for node in plan['nodes']]
        if values.get('PROXYPERMIT_MANAGED_HA') != 'true' or addresses != [values.get('NODE0_ADDRESS'), values.get('NODE1_ADDRESS')]:
            raise ValueError('The plan does not match the installed HA pair.')
        checks['plan'] = 'pass'
        try:
            parameters = metadata_parameters(setup.AGENTS[plan['platform']])
            checks['agent'] = 'pass'
            # The installed agent must actually support the selected safe transport.
            # Presence of metadata does not prove credentials or a power-off path.
            if plan['platform'] == 'proxmox':
                if 'api_token' not in parameters:
                    checks['authentication'] = 'blocked'
                else:
                    try:
                        credential = setup.protected(Path('/etc/proxypermit/proxmox-fencing.json'), private=True)
                        role = values.get('PROXYPERMIT_HA_NODE')
                        expected = plan['nodes'][1 if role == 'a' else 0]['target']
                        if role not in ('a', 'b') or str(credential['target']) != expected:
                            raise ValueError('The fencing profile must target the installed peer.')
                        # No credential enters command arguments or the public report.
                        output = command('/usr/sbin/fence_proxypermit_pve', '--action=status',
                            '--ip=' + credential['host'], '--pve-node=' + credential['pveNode'], '--plug=' + expected)
                        checks['authentication'] = 'pass' if output.strip() == 'Status: ON' else 'blocked'
                    except FileNotFoundError:
                        checks['authentication'] = 'pending'
                    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
                        checks['authentication'] = 'blocked'
        except (OSError, ValueError, subprocess.SubprocessError):
            checks['agent'] = 'blocked'
            checks['authentication'] = 'unknown'
    except FileNotFoundError:
        checks['plan'] = 'pending'
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        checks['plan'] = 'blocked'
    # No green readiness claim without actual witness, credential and destructive
    # fencing tests. These must be completed by the later activation workflow.
    return dict(version=1, checked=int(time.time()), checks=checks)


def refresh(folder, values):
    path = folder / 'readiness.json'
    try:
        cached = setup.protected(path)
        age = time.time() - cached.get('checked', 0)
        plan_mtime = setup.PLAN.lstat().st_mtime if setup.PLAN.exists() else 0
        if 0 <= age < 30 and plan_mtime <= cached['checked']:
            return
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    atomic_write(path, json.dumps(inspect(values)), 0o640)
    os.chown(path, 0, folder.stat().st_gid)  # pylint: disable=no-member
    sync_directory(folder)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, help='Read a secret-free plan; print validation only by default.')
    parser.add_argument('--stage', action='store_true', help='Save the reviewed plan as root-only preparation.')
    args = parser.parse_args(argv)
    try:
        require_linux_root('Protected cluster preparation')
        if args.stage and not args.plan:
            raise ValueError('Staging requires an explicit reviewed plan.')
        if args.plan:
            # The input may be operator-owned, but may never introduce secrets,
            # executable paths or commands into the root-owned staged profile.
            descriptor = os.open(args.plan, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
            with os.fdopen(descriptor, encoding='utf-8') as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 16384 or info.st_nlink != 1:
                    raise ValueError('Use a bounded regular plan file.')
                content = source.read(16385)
            if len(content) > 16384:
                raise ValueError('The plan is too large.')
            plan = setup.validate_plan(json.loads(content))
            if args.stage:
                with maintenance_lock():
                    require_trusted_path(setup.PLAN.parent, directory=True)
                    if setup.PLAN.exists() or setup.PLAN.is_symlink():
                        setup.protected(setup.PLAN, private=True)
                    atomic_write(setup.PLAN, json.dumps(plan), 0o600)
                    os.chown(setup.PLAN, 0, 0)  # pylint: disable=no-member
            print(json.dumps(dict(valid=True, staged=args.stage, activation=False,
                  requiredAgent=setup.AGENTS[plan['platform']], witnessTlsRequired=True,
                  realPowerOffTestRequired=True), indent=2))
        else:
            from dotenv import dotenv_values
            print(json.dumps(inspect(dotenv_values('/etc/proxypermit/.env', interpolate=False)), indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        print('Cluster preparation refused. Review the plan, permissions and OS prerequisites.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
