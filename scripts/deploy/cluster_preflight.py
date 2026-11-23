"""Prepare root-only evidence for a protected HA migration without moving the VIP."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import ssl
import stat
import subprocess
import tempfile
import time

from app.core import cluster, cluster_setup, cluster_topology
from app.core.tinyproxy import atomic_write
from scripts.deploy import cluster_bootstrap as bootstrap, cluster_native as native, install

NSS = Path('/etc/corosync/qdevice/net/nssdb')
FENCE = '/usr/sbin/fence_proxypermit_pve'
CREDENTIAL = Path('/etc/proxypermit/proxmox-fencing.json')
OFF_AGE = 600


def operation_directory(folder):
    install.require_trusted_path(folder.parent, directory=True)
    folder.mkdir(mode=0o700, exist_ok=True)
    install.require_trusted_path(folder, directory=True)
    if folder.stat().st_mode & 0o077:
        raise ValueError('The operation directory must be root-only.')


def secret_key(side, folder):
    path = folder / 'authkey'
    if side == 'a' and not (path.exists() or path.is_symlink()):
        native.write_key(path, os.urandom(1024))
    if len(native.private_bytes(path, 1024)) != 1024:
        raise ValueError('Copy the exact 1024-byte key from A via a trusted private channel.')


def certificate_material(cluster_name, ca_hash):
    if not isinstance(ca_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', ca_hash):
        raise ValueError('Supply the verified SHA-256 fingerprint of the witness CA.')
    for directory in (NSS.parent, *NSS.parent.parents):
        install.require_trusted_path(directory, directory=True)
    for path in (NSS, NSS / 'cert9.db', NSS / 'key4.db', NSS / 'pwdfile.txt'):
        info = path.lstat()
        expected_type = stat.S_ISDIR if path == NSS else stat.S_ISREG
        if (not expected_type(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
                or info.st_mode & 0o002 or (path != NSS and info.st_nlink != 1)
                or (path.name in ('key4.db', 'pwdfile.txt') and info.st_mode & 0o007)):
            raise ValueError('The qdevice NSS store must be controlled by root.')
    db = 'sql:' + str(NSS)
    ca = bootstrap.run('/usr/bin/certutil', '-L', '-d', db, '-n', 'QNet CA', '-a').stdout
    client = bootstrap.run('/usr/bin/certutil', '-L', '-d', db, '-n', 'Cluster Cert', '-a').stdout
    if hashlib.sha256(ssl.PEM_cert_to_DER_cert(ca)).hexdigest() != ca_hash:
        raise ValueError('The installed witness CA does not match the approved fingerprint.')
    keys = bootstrap.run('/usr/bin/certutil', '-K', '-d', db, '-f', str(NSS / 'pwdfile.txt')).stdout
    if not re.search(r'(?m)^[ \t]*<[ \t]*[0-9]+>[^\r\n]*[ :\t]Cluster Cert[ \t]*$', keys):
        raise ValueError('The client certificate has no matching private key in NSS.')
    with tempfile.TemporaryDirectory(prefix='proxypermit-witness-check-') as folder:
        ca_file, client_file = Path(folder) / 'ca.pem', Path(folder) / 'client.pem'
        ca_file.write_text(ca, encoding='ascii')
        client_file.write_text(client, encoding='ascii')
        subject = bootstrap.run('/usr/bin/openssl', 'x509', '-in', str(client_file),
                                '-noout', '-subject', '-nameopt', 'RFC2253').stdout.strip()
        if subject != 'subject=CN=' + cluster_name:
            raise ValueError('The witness client certificate belongs to a different cluster.')
        bootstrap.run('/usr/bin/openssl', 'verify', '-trusted', str(ca_file),
                      '-purpose', 'sslclient', str(client_file))
        bootstrap.run('/usr/bin/certutil', '-V', '-d', db, '-n', 'Cluster Cert', '-u', 'C')
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(client)).hexdigest()


def power_observation(plan, side, stamp, folder, action):
    if action not in ('fence-off', 'fence-on'):
        raise ValueError('Unknown power observation.')
    credential = cluster_setup.protected(CREDENTIAL, private=True)
    peer = plan['nodes'][1 if side == 'a' else 0]
    if not isinstance(credential, dict) or type(credential.get('target')) is not int or str(credential['target']) != peer['target']:
        raise ValueError('The fencing credential must be limited to the other node in this plan.')
    install.require_trusted_path(Path(FENCE), directory=False)
    endpoints = {address[4][0] for address in socket.getaddrinfo(
        credential['host'], 8006, socket.AF_INET, socket.SOCK_STREAM)}
    if endpoints != {plan['fenceAddress']}:
        raise ValueError('The fencing endpoint must resolve to the fencing address in the plan.')
    identity = native.fencing_identity(stamp, credential)
    pending = folder / 'fencing-off-observed.json'
    receipt = folder / 'fencing-tested.json'
    before = None
    if action == 'fence-on':
        before = cluster_setup.protected(pending, private=True)
        if (not isinstance(before, dict) or any(before.get(key) != value for key, value in identity.items())
                or type(before.get('checked')) is not int or not -5 <= time.time() - before['checked'] <= OFF_AGE):
            raise ValueError('Record a fresh power-off observation with the current peer credential first.')
    status = bootstrap.run(FENCE, '--action=status', '--ip=' + credential['host'],
                           '--pve-node=' + credential['pveNode'], '--plug=' + peer['target'], check=False, timeout=20)
    expected = (2, 'Status: OFF') if action == 'fence-off' else (0, 'Status: ON')
    if (status.returncode, status.stdout.strip()) != expected:
        raise ValueError('The authenticated fencing agent did not confirm the required peer power state.')
    if native.fencing_identity(stamp, credential) != identity:
        raise ValueError('The OS fencing configuration changed during the power observation.')
    if before is None:
        receipt.unlink(missing_ok=True)
        atomic_write(pending, json.dumps(dict(**identity, checked=int(time.time()))), 0o600)
    else:
        atomic_write(receipt, json.dumps(dict(**identity, offConfirmed=True,
                     recoveryConfirmed=True, checked=int(time.time()))), 0o600)
        atomic_write(folder.parent / 'preflight-current.json', json.dumps(
            dict(operation=folder.name, planHash=stamp)), 0o600)
        pending.unlink()
    return {'peer': peer['name'], 'power': expected[1].removeprefix('Status: ')}


def perform(action, operation, ca_hash=None):
    plan, topology, stamp, folder = native.context(operation)
    if cluster.reserved():
        raise ValueError('Finish preflight before reserving the node for cluster migration.')
    native.require_prepared(stamp)
    operation_directory(folder)
    name = cluster_topology.name(plan)
    result = dict(operation=operation, planHash=stamp, cluster=name)
    if action == 'workspace':
        result['directory'] = str(folder)
    elif action == 'key':
        secret_key(topology['node'], folder)
        result['keyReady'] = True
    elif action == 'witness':
        ca_hash = ca_hash.replace(':', '').lower() if isinstance(ca_hash, str) else None
        client_hash = certificate_material(name, ca_hash)
        receipt = dict(version=1, cluster=name, planHash=stamp, caHash=ca_hash, certificateHash=client_hash)
        atomic_write(folder / 'qdevice-ready.json', json.dumps(receipt), 0o600)
        result.update(certificateHash=client_hash)
    else:
        result.update(power_observation(plan, topology['node'], stamp, folder, action))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('workspace', 'key', 'witness', 'fence-off', 'fence-on'))
    parser.add_argument('--operation', required=True)
    parser.add_argument('--ca-sha256')
    args = parser.parse_args(argv)
    try:
        install.require_linux_root('Native cluster preflight')
        with install.maintenance_lock():
            value = perform(args.action, args.operation, args.ca_sha256)
        print(json.dumps(value, indent=2))
        return 0
    except ValueError as exc:
        print(f'Cluster preflight stopped: {exc}')
    except (OSError, KeyError, TypeError, RecursionError, subprocess.SubprocessError):
        print('Cluster preflight stopped. Check the local preparation, certificate store and fencing profile.')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
