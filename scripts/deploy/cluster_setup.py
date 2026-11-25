"""Inspect protected-cluster prerequisites or stage a secret-free OS plan.

This step never starts a cluster, changes the firewall, or powers off guests.
Staging is preparation, not permission to activate an unsafe cluster.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import time
from xml.etree import ElementTree as ET

from app.core import cluster, cluster_setup as setup
from app.core.files import sync_directory
from app.core.tinyproxy import atomic_write
from scripts.deploy.install import maintenance_lock, require_linux_root, require_trusted_path
from scripts.deploy import cluster_bootstrap

ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'}
PACKAGES = ('pcs', 'corosync', 'pacemakerd', 'corosync-qdevice-tool', 'certutil')


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


def check_host(plan, role):
    rows = json.loads(command('/usr/sbin/ip', '-j', '-4', 'address', 'show'))
    local = {item['local'] for row in rows for item in row.get('addr_info', []) if item.get('family') == 'inet'}
    members = {node['address'] for node in plan['nodes']} | {plan['witness'], plan['fenceAddress']}
    if role == 'witness':
        if local & members != {plan['witness']} or plan['witness'] == plan['fenceAddress']:
            raise ValueError('Only the dedicated witness address may be present on this host.')
        return 'witness'
    from io import StringIO
    from dotenv import dotenv_values
    from scripts.deploy.management_tls import ENVIRONMENT, protected
    values = dotenv_values(stream=StringIO(protected(ENVIRONMENT)), interpolate=False)
    setup.require_installed_pair(plan, values)
    side = values.get('PROXYPERMIT_HA_NODE')
    if side not in ('a', 'b'):
        raise ValueError('The installed node role must be A or B.')
    node = plan['nodes'][0 if side == 'a' else 1]
    if local & members != {node['address']} or socket.gethostname().split('.')[0] != node['name']:
        raise ValueError('The plan does not match this node hostname and local IPv4 address.')
    return side


def stage_plan(plan, role, *, expected=None):
    with maintenance_lock():
        if cluster.reserved():
            raise ValueError('An existing protected cluster requires its maintenance workflow.')
        host = check_host(plan, role)
        require_trusted_path(setup.PLAN.parent, directory=True)
        current = None
        if setup.PLAN.exists() or setup.PLAN.is_symlink():
            current = setup.protected(setup.PLAN, private=True)
        if expected is not None and expected != (setup.plan_hash(setup.validate_plan(current)) if current is not None else ''):
            raise ValueError('The staged plan changed. Review the current node again.')
        if cluster_bootstrap.JOURNAL.exists() or cluster_bootstrap.JOURNAL.is_symlink():
            retained = setup.protected(cluster_bootstrap.JOURNAL, private=True)
            if (current != plan or not isinstance(retained, dict) or retained.get('planHash') != setup.plan_hash(plan)
                    or retained.get('role') != role):
                raise ValueError('Dependency preparation has already started. Review the retained plan before making changes.')
        if current == plan:
            return host
        atomic_write(setup.PLAN, json.dumps(plan), 0o600)
        os.chown(setup.PLAN, 0, 0)  # pylint: disable=no-member
        sync_directory(setup.PLAN.parent)
        return host


def inspect_preflight(plan, stamp, checks):
    from scripts.deploy import cluster_native
    try:
        pointer = setup.protected(cluster_bootstrap.FOLDER / 'preflight-current.json', private=True)
        operation = pointer['operation']
        if (not isinstance(operation, str) or not re.fullmatch(r'[0-9a-f]{32}', operation)
                or pointer['planHash'] != stamp):
            return
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return
    folder = cluster_bootstrap.FOLDER / operation
    try:
        if cluster_native.transport_receipt(plan, stamp, folder) is not None:
            key = cluster_native.private_bytes(folder / 'authkey', 1024)
            checks['enrollment'] = 'pass' if len(key) == 1024 else 'blocked'
    except FileNotFoundError:
        pass
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        checks['enrollment'] = 'blocked'
    if checks['authentication'] == 'pass':
        try:
            cluster_native.verify_power_receipt(folder, stamp)
            checks['powerTest'] = 'pass'
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            pass


def inspect(values, *, include_plan=False):
    checks = dict.fromkeys(setup.CHECKS, 'pending')
    extra = {}
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
        extra['planRevision'] = setup.plan_hash(plan)
        if include_plan:
            extra['stagedPlan'] = plan
        try:
            journal = setup.protected(cluster_bootstrap.JOURNAL, private=True)
            extra['planLocked'] = True
            extra['packagesPrepared'] = (isinstance(journal, dict) and journal.get('role') == 'node'
                and journal.get('phase') == 'ready' and journal.get('planHash') == extra['planRevision'])
        except FileNotFoundError:
            extra['planLocked'] = False
            extra['packagesPrepared'] = False
        except (OSError, ValueError, RecursionError):
            extra['planLocked'] = True
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
        if plan['platform'] == 'proxmox':
            inspect_preflight(plan, extra['planRevision'], checks)
    except FileNotFoundError:
        checks['plan'] = 'pending'
        extra['planRevision'] = ''
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        checks['plan'] = 'blocked'
    # Local evidence remains separate from live witness and quorum checks.
    return dict(version=1, checked=int(time.time()), checks=checks, **extra)


def refresh(folder, values):
    path = folder / 'readiness.json'
    try:
        cached = setup.protected(path)
        age = time.time() - cached.get('checked', 0)
        changed = max((item.lstat().st_mtime if item.exists() else 0
                       for item in (setup.PLAN, cluster_bootstrap.JOURNAL, cluster_bootstrap.FOLDER / 'preflight-current.json')))
        if 0 <= age < 30 and changed <= cached['checked']:
            return
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    atomic_write(path, json.dumps(inspect(values, include_plan=True)), 0o640)
    os.chown(path, 0, folder.stat().st_gid)  # pylint: disable=no-member
    sync_directory(folder)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, help='Read a secret-free plan; print validation only by default.')
    parser.add_argument('--stage', action='store_true', help='Save the reviewed plan as root-only preparation.')
    parser.add_argument('--role', choices=('node', 'witness'), help='Check the plan against this host before staging.')
    args = parser.parse_args(argv)
    try:
        require_linux_root('Protected cluster preparation')
        if (args.stage or args.role) and not args.plan:
            raise ValueError('Host validation and staging require an explicit reviewed plan.')
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
            host = None
            if args.stage:
                host = stage_plan(plan, args.role or 'node')
            elif args.role:
                host = check_host(plan, args.role)
            print(json.dumps(dict(valid=True, staged=args.stage, activation=False, checkedHost=host, planHash=setup.plan_hash(plan),
                  requiredAgent=setup.AGENTS[plan['platform']], witnessTlsRequired=True,
                  realPowerOffTestRequired=True), indent=2))
        else:
            from dotenv import dotenv_values
            print(json.dumps(inspect(dotenv_values('/etc/proxypermit/.env', interpolate=False)), indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RecursionError, subprocess.SubprocessError):
        print('Cluster preparation refused. Review the plan, permissions and OS prerequisites.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
