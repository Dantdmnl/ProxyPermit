"""Public prerequisite evidence and secret-free protected-cluster plans."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import time

PLAN = Path('/etc/proxypermit/cluster-plan.json')
READINESS = Path('/run/proxypermit-cluster/readiness.json')
WEB_UNIT = 'proxypermit-cluster-web.service'
WEB_MARKER = '# Managed by ProxyPermit cluster web preparation\n'
WEB_REQUEST = Path('/var/lib/proxypermit/cluster-plan-web-request.json')
WEB_RESULT = Path('/run/proxypermit-cluster-web/result.json')
WEB_COMMAND = ('/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'start', '--no-block', WEB_UNIT)
WEB_ACTIONS = ('stage', 'prepare')
REQUEST_AGE = 120
JOB_TIMEOUT = 660
MAX_AGE = 90
AGENTS = {'proxmox': 'fence_proxypermit_pve', 'libvirt': 'fence_xvm',
          'vmware': 'fence_vmware_rest', 'ipmi': 'fence_ipmilan'}
CHECKS = ('managedHa', 'time', 'packages', 'plan', 'agent', 'authentication', 'powerTest')


def protected(path, private=False):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, encoding='utf-8') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                or info.st_mode & (0o077 if private else 0o022) or info.st_size > 16384):
            raise ValueError('Cluster preparation metadata must be protected by root.')
        content = source.read(16385)
        if len(content) > 16384:
            raise ValueError('Cluster preparation metadata is too large.')
        return json.loads(content)


def validate_plan(value):
    """Reject unknown fields, secrets, ambiguous identities and shared power targets."""
    if (not isinstance(value, dict) or set(value) != {'version', 'platform', 'witness', 'fenceAddress', 'nodes'}
            or type(value['version']) is not int or value['version'] != 1
            or not isinstance(value['platform'], str) or value['platform'] not in AGENTS
            or not isinstance(value['nodes'], list) or len(value['nodes']) != 2):
        raise ValueError('Use a version 1 plan with exactly two nodes and a supported platform.')
    result = dict(version=1, platform=value['platform'], nodes=[])
    for key in ('witness', 'fenceAddress'):
        if not isinstance(value[key], str):
            raise ValueError('Use explicit infrastructure IPv4 addresses.')
        address = ipaddress.IPv4Address(value[key])
        if address.is_unspecified or address.is_multicast or address.is_loopback or address.is_link_local:
            raise ValueError('Use reachable unicast addresses for cluster infrastructure.')
        result[key] = str(address)
    for node in value['nodes']:
        if (not isinstance(node, dict) or set(node) != {'name', 'address', 'target'}
                or not isinstance(node['name'], str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9-]{0,62}', node['name'])
                or not isinstance(node['target'], str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}', node['target'])):
            raise ValueError('Use two explicit node names, addresses and distinct power targets.')
        if not isinstance(node['address'], str):
            raise ValueError('Use explicit node IPv4 addresses.')
        address = ipaddress.IPv4Address(node['address'])
        if address.is_unspecified or address.is_multicast or address.is_loopback or address.is_link_local:
            raise ValueError('Use reachable unicast node addresses.')
        if value['platform'] == 'proxmox' and not re.fullmatch(r'[1-9][0-9]{2,8}', node['target']):
            raise ValueError('Proxmox fencing requires explicit numeric VM IDs.')
        result['nodes'].append(dict(name=node['name'], address=str(address), target=node['target']))
    if (any(len({node[key].lower() for node in result['nodes']}) != 2 for key in ('name', 'address', 'target'))
            or result['witness'] in {node['address'] for node in result['nodes']}
            or result['fenceAddress'] in {node['address'] for node in result['nodes']}):
        raise ValueError('Node identities and power targets must differ; use a separate witness host.')
    return result


def require_installed_pair(plan, values):
    """Check a validated plan against the installed HA network."""
    if ((values.get('PROXYPERMIT_MANAGED_HA') or '').lower() != 'true'
            or [node['address'] for node in plan['nodes']] != [values.get('NODE0_ADDRESS'), values.get('NODE1_ADDRESS')]):
        raise ValueError('The plan must match the installed HA pair in A/B order.')
    vip = str(ipaddress.IPv4Address(values.get('VIP_ADDRESS', '')))
    infrastructure = {plan['witness'], plan['fenceAddress']}
    if len(infrastructure) != 2 or vip in infrastructure:
        raise ValueError('Use separate addresses for the witness, fencing endpoint and virtual IP.')


def plan_hash(plan):
    """Fingerprint the normalized plan consistently across all three hosts."""
    return hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()


def web_enabled():
    from .management_https import root_text
    try:
        return root_text(Path('/etc/systemd/system') / WEB_UNIT).startswith(WEB_MARKER)
    except (OSError, ValueError):
        return False


def plan_revision(value):
    return isinstance(value, str) and (value == '' or re.fullmatch(r'[0-9a-f]{64}', value) is not None)


def web_result(now=None):
    now = time.time() if now is None else now
    try:
        value = protected(WEB_RESULT)
        if (not isinstance(value, dict) or not isinstance(value.get('id'), str)
                or not re.fullmatch(r'[0-9a-f]{32}', value['id']) or value.get('action') not in WEB_ACTIONS
                or value.get('phase') not in ('running', 'complete', 'failed')
                or type(value.get('created')) is not int or not -5 <= now - value['created'] <= 86400):
            return None
        phase = value['phase']
        if phase == 'running' and now - value['created'] > JOB_TIMEOUT + REQUEST_AGE:
            phase = 'failed'
        result = dict(id=value['id'], action=value['action'], phase=phase)
        if phase == 'complete' and plan_revision(value.get('planHash')) and value['planHash']:
            result['planHash'] = value['planHash']
        return result
    except (OSError, ValueError, RecursionError):
        return None


def read_readiness(now=None, *, include_plan=False):
    """Return fixed public checks only; never expose addresses, targets or credentials."""
    unknown = dict(state='unknown', checks={key: 'unknown' for key in CHECKS})
    try:
        value = protected(READINESS)
        now = time.time() if now is None else now
        if (not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1
                or type(value.get('checked')) is not int or not -5 <= now - value['checked'] <= MAX_AGE
                or not isinstance(value.get('checks'), dict) or set(value['checks']) != set(CHECKS)
                or any(state not in ('pass', 'blocked', 'pending', 'unknown') for state in value['checks'].values())):
            return unknown
        result = dict(state='preparation', checked=value['checked'], validFor=max(0, int(value['checked'] + MAX_AGE - now)),
                      checks={key: value['checks'][key] for key in CHECKS})
        if plan_revision(value.get('planRevision')):
            result['planRevision'] = value['planRevision']
        for field in ('packagesPrepared', 'planLocked'):
            if type(value.get(field)) is bool:
                result[field] = value[field]
        if include_plan and 'stagedPlan' in value:
            plan = validate_plan(value['stagedPlan'])
            if plan_hash(plan) == result.get('planRevision'):
                result['stagedPlan'] = plan
        return result
    except (OSError, ValueError, TypeError, RecursionError):
        return unknown
