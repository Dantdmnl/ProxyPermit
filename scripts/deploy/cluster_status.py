"""Export redacted root cluster evidence; never grant the web user haclient access."""
import json
import os
from pathlib import Path
import stat
import subprocess
import time

from app.core import cluster, cluster_setup
from app.core.cluster_evidence import summarize
from app.core.files import sync_directory
from app.core.tinyproxy import atomic_write
from scripts.deploy import ha_resource

MARKER = '# Managed by ProxyPermit cluster status\n'
FENCING_PROFILE = Path('/etc/proxypermit/proxmox-fencing.json')


def command(*args, accepted=(0,)):
    result = subprocess.run(args, capture_output=True, text=True, timeout=3,
                            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
    if result.returncode not in accepted:
        result.check_returncode()
    return result.stdout


def native_credential_status(now=None):
    """Report only local expiry, using the same private file the agent reads."""
    try:
        profile = cluster_setup.protected(FENCING_PROFILE, private=True)
        if isinstance(profile, dict) and type(profile.get('version')) is int and profile['version'] == 1:
            expires = profile.get('expires')
            if type(expires) is int and expires > 0:
                return 'current' if expires > (time.time() if now is None else now) else 'expired'
    except (OSError, ValueError, RecursionError):
        pass
    return 'unknown'


def evidence():
    if not cluster.reserved():
        return dict(state='not-configured', owners=0, quorate=False, witnessConnected=False,
                    fencingConfigured=False, fencingReady=False, policySafe=False)
    try:
        profile = ha_resource.profile()
        ha_resource.validate_service()
        return summarize(command('cibadmin', '--query'), command('crm_mon', '--one-shot', '--as-xml'),
            command('corosync-qdevice-tool', '-s', '-v'),
            command('corosync-cmapctl', 'totem.crypto_cipher', 'totem.crypto_hash',
                    'runtime.votequorum.wait_for_all_status'),
            ha_resource.owns_vip(profile), command('crm_node', '--name').strip(),
            quorum_text=command('corosync-quorumtool', '-s', accepted=(0, 2)),
            native_credential=native_credential_status())
    except (OSError, ValueError, subprocess.SubprocessError):
        return dict(state='unknown', owners=0, quorate=False, witnessConnected=False,
                    fencingConfigured=False, fencingReady=False, policySafe=False)


def publish(value):
    import grp  # pylint: disable=import-error
    folder = cluster.STATUS.parent
    folder.mkdir(mode=0o750, parents=False, exist_ok=True)
    info = folder.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('The cluster status directory must be protected by root.')
    os.chown(folder, 0, grp.getgrnam('proxypermit').gr_gid)  # pylint: disable=no-member
    os.chmod(folder, 0o750)
    if cluster.STATUS.exists() or cluster.STATUS.is_symlink():
        info = cluster.STATUS.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or info.st_nlink != 1:
            raise ValueError('The cluster status file must be protected by root.')
    previous = None
    try:
        previous = json.loads(cluster.STATUS.read_text()).get('state')
    except (OSError, ValueError, AttributeError):
        pass
    exported = dict(version=1, checked=int(time.time()), **value)
    try:
        checkpoint = cluster_setup.protected(cluster.SETUP_CHECKPOINT, private=True)
        if (isinstance(checkpoint, dict) and type(checkpoint.get('version')) is int
                and checkpoint['version'] == 1 and checkpoint.get('phase') in cluster.MIGRATION_PHASES):
            exported['migrationPhase'] = checkpoint['phase']
            exported.update(cluster.migration_reference(checkpoint.get('operation'), checkpoint.get('planHash')))
    except (OSError, ValueError, RecursionError):
        pass
    atomic_write(cluster.STATUS, json.dumps(exported), 0o640)
    os.chown(cluster.STATUS, 0, grp.getgrnam('proxypermit').gr_gid)  # pylint: disable=no-member
    with cluster.STATUS.open('rb') as source:
        os.fsync(source.fileno())
    sync_directory(folder)
    if value['state'] != previous:
        print('Cluster protection status changed: ' + value['state'], flush=True)


def assets(release):
    result = {
        Path('/etc/systemd/system/proxypermit-cluster-status.service'): (
            '[Unit]\nDescription=Export private ProxyPermit cluster status\nAfter=network.target\n'
            '[Service]\nType=oneshot\nUser=root\nGroup=proxypermit\nLogNamespace=proxypermit\n'
            f'WorkingDirectory={release}\nExecStart={release}/.venv/bin/python -m scripts.deploy.cluster_status\n'
            'RuntimeDirectory=proxypermit-cluster\nRuntimeDirectoryMode=0750\nRuntimeDirectoryPreserve=yes\n'
            'UMask=0027\nNoNewPrivileges=yes\nProtectHome=yes\nPrivateTmp=yes\nProtectSystem=strict\n'
            'ProtectKernelTunables=yes\nProtectKernelModules=yes\nProtectControlGroups=yes\n'
            'RestrictAddressFamilies=AF_UNIX AF_NETLINK AF_INET AF_INET6\n'
            'ReadWritePaths=/run/proxypermit-cluster\nTimeoutStartSec=20\n'),
        Path('/etc/systemd/system/proxypermit-cluster-status.timer'): (
            '[Unit]\nDescription=Refresh ProxyPermit cluster evidence\n[Timer]\n'
            'OnBootSec=5s\nOnUnitActiveSec=5s\nAccuracySec=1s\n'
            '[Install]\nWantedBy=timers.target\n')}
    return {path: MARKER + content for path, content in result.items()}


def main():
    if os.name != 'posix' or os.geteuid() != 0:  # pylint: disable=no-member
        return 1
    try:
        publish(evidence())
        if not cluster.reserved():
            from dotenv import dotenv_values
            from scripts.deploy.cluster_setup import refresh
            refresh(cluster.STATUS.parent, dotenv_values('/etc/proxypermit/.env', interpolate=False))
        return 0
    except (OSError, ValueError):
        print('Cluster status could not be exported. Review root-owned status storage.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
