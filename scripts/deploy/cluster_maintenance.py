"""Root-only, cluster-wide maintenance reservation with version-checked standby.

The reservation never expires automatically. A crash leaves the node in standby
until an OS administrator verifies recovery. No cluster credentials are logged.
"""
from contextlib import contextmanager
import argparse
import json
import os
from pathlib import Path
import secrets
import re
import stat
import subprocess
import sys
import time
from xml.etree import ElementTree as ET

from app.core import cluster_lease
from app.core.cluster_evidence import xml, RESOURCE, enabled
from app.core.files import sync_directory
from app.core.tinyproxy import ConfigError, atomic_write
from scripts.deploy import cluster_status, ha_resource

CHECKPOINT = Path('/etc/proxypermit/cluster-maintenance.json')
PEER_RELEASE_WAIT = 20
PEER_RELEASE_INTERVAL = 1
ENVIRONMENT = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'}


def command(*args, content=None, cwd=None):
    try:
        result = subprocess.run(args, input=content, capture_output=True, timeout=10, env=ENVIRONMENT, cwd=cwd)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConfigError('The cluster maintenance command could not be completed; review OS cluster status.') from exc
    if len(result.stdout) > 2 * 1024 * 1024:
        raise ConfigError('The cluster response exceeds the maintenance limit.')
    return result


def query():
    result = command('cibadmin', '--query')
    if result.returncode:
        raise ConfigError('The cluster configuration could not be read.')
    return xml(result.stdout.decode('utf-8'))


def replace(root):
    # Keep the original CIB generation. Pacemaker rejects stale full-CIB
    # replacements; --force or an artificial generation increment is unsafe.
    try:
        return command('cibadmin', '--replace', '--xml-pipe', '--sync-call',
                       content=ET.tostring(root)).returncode == 0
    except ConfigError:
        return False  # A timeout may have committed: callers must re-read.


def protected_directory():
    info = CHECKPOINT.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ConfigError('Cluster maintenance requires a root-protected configuration directory.')


def load():
    protected_directory()
    try:
        descriptor = os.open(CHECKPOINT, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, encoding='utf-8') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077
                or info.st_nlink != 1 or info.st_size > 4096):
            raise ConfigError('The cluster maintenance checkpoint must be a private root-owned file.')
        value = json.load(source)
    if (not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1
            or value.get('phase') not in ('prepared', 'entered', 'releasing')
            or not isinstance(value.get('node'), str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,253}', value['node'])
            or not isinstance(value.get('token'), str) or not re.fullmatch(r'[0-9a-f]{32}', value['token'])
            or not isinstance(value.get('standbyId'), str) or not value['standbyId']
            or value.get('before') not in (None, 'on', 'off', 'true', 'false')
            or (value.get('before') is None and value.get('standbySet') != 'proxypermit-maintenance-local-' + value['token'])):
        raise ConfigError('Invalid cluster maintenance checkpoint. OS review is required.')
    return value


def save(value):
    protected_directory()
    atomic_write(CHECKPOINT, json.dumps(value), 0o600)
    os.chown(CHECKPOINT, 0, 0)  # pylint: disable=no-member
    with CHECKPOINT.open('rb') as source:
        os.fsync(source.fileno())
    sync_directory(CHECKPOINT.parent)


def forget():
    CHECKPOINT.unlink()
    sync_directory(CHECKPOINT.parent)


def local_name():
    result = command('crm_node', '--name')
    if result.returncode:
        raise ConfigError('The local cluster identity could not be verified.')
    return result.stdout.decode('utf-8').strip()


def peer_ready(name):
    result = command('crm_mon', '--one-shot', '--as-xml')
    if result.returncode:
        return False
    monitor = xml(result.stdout.decode('utf-8'))
    members = monitor.findall('nodes/node')
    if len(members) != 2 or len({node.get('name') for node in members}) != 2:
        return False
    peer = next((node for node in members if node.get('name') != name), None)
    return bool(peer is not None and enabled(peer.get('online'))
        and not any(enabled(peer.get(key)) for key in ('standby', 'maintenance', 'unclean', 'pending', 'shutdown')))


def drained(name):
    profile = ha_resource.profile()
    if ha_resource.owns_vip(profile) or cluster_status.evidence()['state'] != 'protected':
        return False
    result = command('crm_mon', '--one-shot', '--as-xml')
    if result.returncode:
        return False
    monitor = xml(result.stdout.decode('utf-8'))
    resource = monitor.find(f"resources/resource[@id='{RESOURCE}']")
    owners = resource.findall('node') if resource is not None else []
    return len(owners) == 1 and owners[0].get('name') != name and peer_ready(name)


def acquire():
    require_root()
    if load() is not None:
        raise ConfigError('Unfinished cluster maintenance exists. Recover it through OS administration first.')
    if cluster_status.evidence()['state'] != 'protected':
        raise ConfigError('Maintenance requires healthy quorum, witness, fencing and exactly one VIP owner.')
    name = local_name()
    if not peer_ready(name):
        raise ConfigError('The other cluster node must be online and available before maintenance.')
    token = secrets.token_hex(16)
    for _ in range(5):
        root = query()
        saved = dict(version=1, phase='prepared', **cluster_lease.claim(root, name, token, int(time.time())))
        save(saved)  # Persist how to restore standby before changing the CIB.
        replace(root)
        current = cluster_lease.marker(query())
        if current == dict(node=name, token=token):
            saved['phase'] = 'entered'
            save(saved)
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if drained(name):
                    return saved
                time.sleep(.5)
            raise ConfigError('The peer did not take over safely. Maintenance remains reserved and this node stays in standby.')
        if current is not None:
            # Our mutation was not accepted; preserve the winning reservation.
            forget()
            raise ConfigError('Another node acquired cluster maintenance. No local services were changed.')
        # A concurrently updated CIB won. Refresh without changing its generation.
    raise ConfigError('The cluster kept changing. The prepared checkpoint requires OS review before retrying.')


def restored(root, saved):
    node = next((node for node in cluster_lease.nodes(root) if node.get('uname') == saved.get('node')), None)
    if node is None:
        return False
    pairs = [pair for pair in node.findall('instance_attributes/nvpair') if pair.get('id') == saved.get('standbyId')]
    return not pairs if saved.get('before') is None else len(pairs) == 1 and pairs[0].get('value') == saved['before']


def complete(saved):
    require_root()
    if saved.get('node') != local_name():
        raise ConfigError('The maintenance checkpoint belongs to another node.')
    deadline = time.monotonic() + PEER_RELEASE_WAIT
    writes = 0
    while writes < 5:
        root = query()
        current = cluster_lease.marker(root)
        if current != dict(node=saved['node'], token=saved['token']):
            if saved['phase'] == 'releasing' and restored(root, saved):
                forget()  # Release committed, but the response/checkpoint deletion was interrupted.
                return
            raise ConfigError('The maintenance reservation changed. Automatic recovery was refused.')
        if not drained(saved['node']):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ConfigError('The peer must still serve the protected VIP before maintenance can be released.')
            time.sleep(min(PEER_RELEASE_INTERVAL, remaining))
            continue
        cluster_lease.release(root, saved)
        saved['phase'] = 'releasing'
        save(saved)
        writes += 1
        replace(root)
        latest = query()
        if cluster_lease.marker(latest) != current and restored(latest, saved):
            forget()
            return
    raise ConfigError('Maintenance release could not be confirmed. Preserve the checkpoint and review cluster status.')


def record(event, saved, install_dir):
    base = Path(install_dir) / 'current'
    raw = os.getenv('SUDO_UID', '')
    actor = int(raw) if raw.isascii() and raw.isdecimal() else os.getuid()  # pylint: disable=no-member
    result = command('runuser', '-u', 'proxypermit', '--', str(base / '.venv/bin/python'),
        '-m', 'scripts.tools.record_maintenance', event, saved['token'], str(actor), cwd=base)
    if result.returncode:
        raise ConfigError('The cluster maintenance audit event could not be recorded.')


@contextmanager
def rolling(health, install_dir):
    saved = acquire()
    try:
        record('cluster_maintenance_started', saved, install_dir)
        yield
        if not health():
            raise ConfigError('The updated node is not healthy. It remains in standby with maintenance reserved.')
        record('cluster_maintenance_verified', saved, install_dir)
        complete(saved)
    except BaseException:
        print('Cluster maintenance was not released. Review local health and peer protection, then use the cluster maintenance --recover command. The checkpoint was retained.', flush=True)
        raise


def require_root():
    if sys.platform != 'linux' or os.geteuid() != 0:  # pylint: disable=no-member
        raise ConfigError('Cluster maintenance requires Linux and OS root privileges.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recover', action='store_true', required=True,
                        help='Release an interrupted maintenance reservation only after local health and peer protection are verified.')
    parser.add_argument('--install-dir', default='/opt/proxypermit')
    args = parser.parse_args(argv)
    try:
        require_root()
        from scripts.deploy import install
        with install.maintenance_lock():
            saved = load()
            if saved is None:
                raise ConfigError('No local maintenance checkpoint exists. Do not clear another node\'s reservation.')
            # A prepared checkpoint may indicate a lost commit reply. Recovery
            # still requires the matching CIB reservation; never fabricate it.
            install.installed_deployment('/', args.install_dir)
            if not install.cluster_upgrade_health(args.install_dir):
                raise ConfigError('Restore a healthy manager and local proxy before releasing maintenance.')
            record('cluster_maintenance_recovered', saved, args.install_dir)
            complete(saved)
            print('Cluster maintenance completed; the original standby setting was restored.')
        return 0
    except (OSError, ValueError, RuntimeError):
        print('Cluster maintenance recovery was refused. Review local health, the private checkpoint and OS cluster status.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
