"""Prepare separate peer-only Proxmox fencing credentials, never a host-admin key.

Run the reviewed file as root on the Proxmox host. Without --apply this prints
the requested scope only. Existing users, roles and outputs are never adopted.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time


def run(*args):
    result = subprocess.run(args, check=True, capture_output=True,
                            text=True, timeout=15, env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
    if len(result.stdout) > 1024 * 1024:
        raise ValueError('The Proxmox response is too large.')
    return result.stdout


def query(*args):
    return json.loads(run(*args, '--output-format', 'json'))


def write(path, value, gid):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as target:
        json.dump(value, target)
        target.flush()
        os.fsync(target.fileno())
    os.chown(path, 0, gid)  # pylint: disable=no-member
    os.chmod(path, 0o640 if gid else 0o600)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)  # pylint: disable=no-member
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def apply(args, output, gid=0):
    user = 'proxypermit-fence-' + args.cluster + '@pve'
    role = 'ProxyPermitFence_' + args.cluster
    pool = query('/usr/bin/pvesh', 'get', '/pools/' + args.pool)
    guests = {int(row['vmid']) for row in pool.get('members', []) if row.get('type') == 'qemu'}
    if not {args.node_a, args.node_b} <= guests:
        raise ValueError('Both reviewed VM IDs must belong to the requested existing pool.')
    if (any(row.get('userid') == user for row in query('/usr/sbin/pveum', 'user', 'list'))
            or any(row.get('roleid') == role for row in query('/usr/sbin/pveum', 'role', 'list'))):
        raise ValueError('A requested fencing user or role already exists; review it without overwriting.')
    if output.exists() or output.is_symlink():
        raise ValueError('The credential output already exists; review any earlier or partial operation.')
    info = output.parent.lstat()
    if not output.parent.is_dir() or output.parent.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('The credential parent must be a protected root-owned directory.')
    output.mkdir(mode=0o700)
    # A durable checkpoint precedes any Proxmox write. On interruption retain it;
    # never guess that credentials/ACLs can safely be removed or recreated.
    write(output / 'operation.json', dict(version=1, phase='preparing', user=user,
          role=role, pool=args.pool, nodeA=args.node_a, nodeB=args.node_b), 0)
    run('/usr/sbin/pveum', 'role', 'add', role, '--privs', 'VM.Audit VM.PowerMgmt')
    run('/usr/sbin/pveum', 'user', 'add', user, '--comment', 'ProxyPermit peer-only fencing: ' + args.cluster)
    for target in (args.node_a, args.node_b):
        run('/usr/sbin/pveum', 'acl', 'modify', '/vms/' + str(target), '--users', user, '--roles', role)
    expires = int(time.time()) + args.days * 86400
    for label, target in (('a', args.node_b), ('b', args.node_a)):
        name = 'node-' + label
        token = query('/usr/sbin/pveum', 'user', 'token', 'add', user, name,
                      '--privsep', '1', '--expire', str(expires))
        expected = user + '!' + name
        if not isinstance(token, dict) or token.get('full-tokenid') != expected or not isinstance(token.get('value'), str):
            raise ValueError('Unexpected token response. Review the private operation checkpoint.')
        # Store a new credential immediately, before continuing to ACL changes.
        write(output / ('node-' + label + '.json'), dict(version=1, tokenId=expected,
              token=token['value'], target=target, expires=expires), gid)
        run('/usr/sbin/pveum', 'acl', 'modify', '/vms/' + str(target),
              '--tokens', expected, '--roles', role)
        permissions = query('/usr/sbin/pveum', 'user', 'token', 'permissions', user, name)
        allowed = {path: {priv for priv, enabled in privileges.items() if enabled}
                   for path, privileges in permissions.items() if any(privileges.values())}
        if allowed != {'/vms/' + str(target): {'VM.Audit', 'VM.PowerMgmt'}}:
            raise ValueError('The resulting token permissions differ from the reviewed peer-only scope.')
    write(output / 'complete.json', dict(version=1, phase='prepared', expires=expires,
          nodeATarget=args.node_b, nodeBTarget=args.node_a), gid)
    if gid:
        os.chown(output, 0, gid)  # pylint: disable=no-member
        os.chmod(output, 0o750)
    print('Peer-only fencing credentials prepared. Values remain in private files; no guest or cluster was changed.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cluster', required=True)
    parser.add_argument('--pool', required=True)
    parser.add_argument('--node-a', type=int, required=True)
    parser.add_argument('--node-b', type=int, required=True)
    parser.add_argument('--days', type=int, default=14, help='Lab token lifetime; replace before expiry (default: 14 days).')
    parser.add_argument('--delegate-group', help='Optionally allow this existing trusted group to read the new lab credentials.')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    try:
        if (not re.fullmatch(r'[a-z][a-z0-9-]{0,23}', args.cluster)
                or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', args.pool)
                or not 100 <= args.node_a <= 999999999 or not 100 <= args.node_b <= 999999999
                or args.node_a == args.node_b or not 1 <= args.days <= 365):
            raise ValueError('Use distinct VM IDs, valid names and a bounded credential lifetime.')
        output = Path('/etc') / ('proxypermit-fencing-' + args.cluster)
        if not args.apply:
            print(json.dumps(dict(nodeACanPowerOnly=args.node_b, nodeBCanPowerOnly=args.node_a,
                pool=args.pool, privileges=['VM.Audit', 'VM.PowerMgmt'], output=output.as_posix(), days=args.days), indent=2))
            return 0
        if os.name != 'posix' or os.geteuid() != 0:  # pylint: disable=no-member
            raise ValueError('Apply requires root on the reviewed Proxmox host.')
        gid = 0
        if args.delegate_group:
            import grp  # pylint: disable=import-error
            gid = grp.getgrnam(args.delegate_group).gr_gid
        apply(args, output, gid)
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('Fencing delegation stopped. Review locally; any partial private state was retained. No automatic deletion was attempted.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
