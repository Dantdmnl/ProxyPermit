"""Prepare OS cluster dependencies without starting cluster services or moving the VIP.

This is a guarded first step, not cluster activation. An interrupted package
installation leaves the new cluster units masked until explicit preparation
resumes. Existing cluster configuration is never adopted automatically.
"""
import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import time

from app.core import cluster, cluster_setup
from app.core.files import sync_directory
from app.core.tinyproxy import atomic_write
from scripts.deploy.install import maintenance_lock, require_linux_root, require_trusted_path

FOLDER = Path('/var/lib/proxypermit-cluster-bootstrap')
JOURNAL = FOLDER / 'preparation.json'
UNIT_FOLDER = Path('/etc/systemd/system')
CONFIG = Path('/etc/corosync/corosync.conf')
CIB = Path('/var/lib/pacemaker/cib/cib.xml')
PACKAGES = {'node': ('pacemaker', 'pacemaker-cli-utils', 'corosync', 'corosync-qdevice', 'pcs', 'libnss3-tools'),
            'witness': ('corosync-qnetd', 'libnss3-tools')}
UNITS = {'node': ('pcsd.service', 'pacemaker.service', 'corosync.service', 'corosync-qdevice.service'),
         'witness': ('corosync-qnetd.service',)}
ENVIRONMENT = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C',
               'DEBIAN_FRONTEND': 'noninteractive', 'APT_LISTCHANGES_FRONTEND': 'none'}


def run(*args, check=True, timeout=15, cwd=None):
    result = subprocess.run(args, check=False, capture_output=True, text=True,
                            timeout=timeout, env=ENVIRONMENT, cwd=cwd)
    if len(result.stdout) + len(result.stderr) > 2 * 1024 * 1024:
        raise ValueError('Cluster preparation response exceeds its bound.')
    if check and result.returncode:
        raise ValueError('OS cluster preparation failed; retained state requires review.')
    return result


def save(value):
    atomic_write(JOURNAL, json.dumps(value), 0o600)
    os.chown(JOURNAL, 0, 0)  # pylint: disable=no-member
    sync_directory(FOLDER)


def plan(role):
    value = cluster_setup.validate_plan(cluster_setup.protected(cluster_setup.PLAN, private=True))
    addresses = {item['local'] for row in json.loads(run('/usr/sbin/ip', '-j', '-4', 'address', 'show').stdout)
                 for item in row.get('addr_info', [])}
    expected = {node['address'] for node in value['nodes']} if role == 'node' else {value['witness']}
    if len(addresses & expected) != 1:
        raise ValueError('The plan must identify exactly one local cluster address.')
    properties = dict(line.split('=', 1) for line in run('/usr/bin/timedatectl', 'show',
                      '--property=NTP', '--property=NTPSynchronized').stdout.splitlines() if '=' in line)
    if properties.get('NTP') != 'yes' or properties.get('NTPSynchronized') != 'yes':
        raise ValueError('Confirm working network time before preparing cluster dependencies.')
    digest = cluster_setup.plan_hash(value)
    return dict(version=1, role=role, planHash=digest, packages=list(PACKAGES[role]), units=list(UNITS[role]))


def prepare(role, *, expected_hash=None):
    require_linux_root('Cluster dependency preparation')
    with maintenance_lock():
        if cluster.reserved():
            raise ValueError('An existing or pending protected cluster requires its maintenance workflow.')
        proposed = plan(role)
        if expected_hash is not None:
            if proposed['planHash'] != expected_hash:
                raise ValueError('The staged cluster plan changed before dependency preparation.')
            from scripts.deploy.cluster_setup import check_host
            check_host(cluster_setup.validate_plan(cluster_setup.protected(cluster_setup.PLAN, private=True)), role)
        require_trusted_path(FOLDER.parent, directory=True)
        FOLDER.mkdir(mode=0o700, exist_ok=True)
        require_trusted_path(FOLDER, directory=True)
        if FOLDER.stat().st_mode & 0o077:
            raise ValueError('Cluster preparation storage must be root-only.')
        if JOURNAL.exists() or JOURNAL.is_symlink():
            current = cluster_setup.protected(JOURNAL, private=True)
            if (current.get('version') != 1 or current.get('role') != role or current.get('planHash') != proposed['planHash']
                    or current.get('phase') not in ('masking', 'installing', 'ready')
                    or current.get('units') != proposed['units']):
                raise ValueError('The retained preparation differs; review it without overwriting.')
            if current['phase'] == 'ready':
                return current
        else:
            if CONFIG.exists() or CONFIG.is_symlink() or CIB.exists() or CIB.is_symlink():
                raise ValueError('Existing Corosync/Pacemaker configuration needs an explicit migration plan.')
            for unit in proposed['units']:
                if run('/usr/bin/systemctl', 'is-active', '--quiet', unit, check=False).returncode == 0:
                    raise ValueError('Existing cluster services must not be adopted automatically.')
                target = UNIT_FOLDER / unit
                if target.exists() or target.is_symlink():
                    raise ValueError('An administrator-owned unit override already exists.')
            current = dict(proposed, phase='masking', created=int(time.time()))
            save(current)
        require_trusted_path(UNIT_FOLDER, directory=True)
        # Record intent before each filesystem mutation. A crash leaves the
        # journal and /dev/null masks intact, never unguarded boot activation.
        for unit in proposed['units']:
            target = UNIT_FOLDER / unit
            if target.is_symlink():
                if os.readlink(target) != '/dev/null':
                    raise ValueError('The unit mask differs from the recorded preparation.')
            elif target.exists():
                raise ValueError('A unit override changed during preparation.')
            else:
                target.symlink_to('/dev/null')
                sync_directory(UNIT_FOLDER)
        run('/usr/bin/systemctl', 'daemon-reload')
        current['phase'] = 'installing'
        save(current)
        run('/usr/bin/apt-get', 'install', '-y', '--no-install-recommends', *proposed['packages'], timeout=600)
        # Disable boot activation while still masked, then remove only the
        # exact masks created by this recorded initial preparation.
        for unit in proposed['units']:
            run('/usr/bin/systemctl', 'disable', unit)
        for unit in proposed['units']:
            target = UNIT_FOLDER / unit
            if not target.is_symlink() or os.readlink(target) != '/dev/null':
                raise ValueError('A recorded unit mask changed; leave preparation stopped.')
            target.unlink()
            sync_directory(UNIT_FOLDER)
        run('/usr/bin/systemctl', 'daemon-reload')
        for unit in proposed['units']:
            if run('/usr/bin/systemctl', 'is-active', '--quiet', unit, check=False).returncode == 0:
                raise ValueError('A prepared cluster service unexpectedly became active.')
        current['phase'] = 'ready'
        save(current)
        return current


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', choices=tuple(PACKAGES), required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    try:
        require_linux_root('Cluster dependency preparation')
        result = prepare(args.role) if args.apply else plan(args.role)
        print(json.dumps(dict(role=result['role'], phase=result.get('phase', 'plan'), packages=result['packages'],
              clusterActivated=False, servicesStarted=False), indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('Cluster dependency preparation stopped. Review the retained root checkpoint and unit masks; do not start the cluster manually.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
