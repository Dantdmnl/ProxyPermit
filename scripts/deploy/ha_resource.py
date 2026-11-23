#!/usr/bin/python3
"""OCF resource for quorum/fencing-managed Keepalived; no web privileges.

Experimental until the cluster fault matrix is complete. Pacemaker owns the
resource assignment; Keepalived alone assigns/removes the virtual IP.
"""
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

PROFILE = Path('/etc/proxypermit/cluster-protection.json')
AGENT_PATH = Path('/usr/lib/ocf/resource.d/proxypermit/vip')
HEALTH = '/etc/keepalived/proxypermit-health'
SUCCESS, ERROR, INVALID, NOT_RUNNING = 0, 1, 6, 7


def command(*args, timeout=2):
    return subprocess.run(args, capture_output=True, text=True, check=False, timeout=timeout)


def profile():
    descriptor = os.open(PROFILE, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, encoding='utf-8') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                or info.st_nlink != 1 or info.st_size > 4096):
            raise ValueError('The cluster protection profile must be a protected root-owned file.')
        value = json.load(source)
    if (not isinstance(value, dict) or value.get('version') != 1
            or not isinstance(value.get('interface'), str)
            or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', value['interface'])
            or not isinstance(value.get('vip'), str)):
        raise ValueError('Invalid cluster protection profile.')
    value['address'] = str(ipaddress.IPv4Interface(value['vip']).ip)
    return value


def owns_vip(value):
    result = command('ip', '-j', '-4', 'address', 'show', 'dev', value['interface'])
    if result.returncode:
        raise ValueError('The protected interface could not be inspected.')
    rows = json.loads(result.stdout)
    if not isinstance(rows, list):
        raise ValueError('Invalid interface status.')
    return any(address.get('local') == value['address'] for row in rows for address in row.get('addr_info', []))


def keepalived_state():
    result = command('systemctl', 'show', 'keepalived.service', '--property=LoadState,ActiveState')
    properties = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if result.returncode or properties.get('LoadState') != 'loaded' or not properties.get('ActiveState'):
        raise ValueError('The Keepalived unit state could not be verified.')
    return properties['ActiveState']


def monitor(value, *, report=False):
    state = keepalived_state()
    present = owns_vip(value)
    if state == 'inactive' and not present:
        return NOT_RUNNING
    if state != 'active':
        reason = 'Keepalived has not reached the expected unit state.'
    elif not present:
        reason = 'The virtual IP is missing from the protected interface.'
    else:
        # Probe as the existing unprivileged account using the root-installed helper.
        if command('runuser', '-u', 'proxypermit', '--', HEALTH).returncode == 0:
            return SUCCESS
        reason = 'The local Tinyproxy health check failed.'
    if report:
        print(reason, file=sys.stderr)
    return ERROR


def validate_service():
    for name, expected in (('KillMode', 'control-group'), ('SendSIGKILL', 'yes'), ('Restart', 'no')):
        result = command('systemctl', 'show', 'keepalived.service', '--property', name, '--value')
        if result.returncode or result.stdout.strip() != expected:
            raise ValueError('Keepalived must use control-group termination, SIGKILL and no independent restart.')
    result = command('systemctl', 'is-enabled', 'keepalived.service')
    if result.stdout.strip() not in ('disabled', 'static'):
        raise ValueError('Disable independent Keepalived boot activation before enabling this resource.')


def resource(action, value):
    if action == 'monitor':
        return monitor(value, report=True)
    if action == 'stop':
        result = command('systemctl', 'stop', 'keepalived.service', timeout=25)
        if result.returncode or keepalived_state() != 'inactive' or owns_vip(value):
            return ERROR  # Pacemaker must fence if a safe stop cannot be proved.
        return SUCCESS
    validate_service()
    if action == 'validate-all':
        return SUCCESS
    if action == 'start':
        if monitor(value) == SUCCESS:
            return SUCCESS
        if command('systemctl', 'start', 'keepalived.service', timeout=25).returncode:
            return ERROR
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if monitor(value) == SUCCESS:
                return SUCCESS
            time.sleep(.25)
        return ERROR
    return 3  # OCF_ERR_UNIMPLEMENTED


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args == ['meta-data']:
        print('''<?xml version="1.0"?>
<resource-agent name="proxypermit-vip" version="1.0">
<version>1.0</version>
<longdesc lang="en">Fencing-managed Keepalived with VIP removal and proxy health verification.</longdesc>
<shortdesc lang="en">ProxyPermit protected virtual IP</shortdesc>
<parameters/>
<actions>
<action name="start" timeout="90s"/>
<action name="stop" timeout="60s"/>
<action name="monitor" timeout="10s" interval="5s"/>
<action name="validate-all" timeout="10s"/>
<action name="meta-data" timeout="5s"/>
</actions></resource-agent>''')
        return SUCCESS
    if len(args) != 1 or args[0] not in ('start', 'stop', 'monitor', 'validate-all'):
        return 3
    if os.name != 'posix' or os.geteuid() != 0:  # pylint: disable=no-member
        return INVALID
    try:
        return resource(args[0], profile())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        print('Protected Keepalived operation could not be verified.', file=sys.stderr)
        return ERROR


if __name__ == '__main__':
    raise SystemExit(main())
