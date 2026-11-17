"""Public prerequisite evidence and secret-free protected-cluster plans."""
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import time

PLAN = Path('/etc/proxypermit/cluster-plan.json')
READINESS = Path('/run/proxypermit-cluster/readiness.json')
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


def read_readiness(now=None):
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
        return dict(state='preparation', checked=value['checked'], validFor=max(0, int(value['checked'] + MAX_AGE - now)),
                    checks={key: value['checks'][key] for key in CHECKS})
    except (OSError, ValueError, TypeError, RecursionError):
        return unknown
