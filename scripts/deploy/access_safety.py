"""Install a reviewed, root-owned anti-lockout profile and additive UFW rules."""
import ipaddress
import json
import os
from pathlib import Path

from app.core.access_safety import PROFILE, firewall_rules, protect_configuration, read_profile
from app.core.files import file_lock
from app.core.tinyproxy import TinyproxyConfigStore, atomic_write, parse


def network(value):
    result = ipaddress.ip_network(value, strict=False)
    if result.prefixlen == 0:
        raise ValueError('A management network cannot allow the entire internet.')
    return str(result)


def requested(args):
    return bool(getattr(args, 'management_cidr', None) or os.getenv('SSH_CONNECTION'))


def port(value):
    if not value.isascii() or not value.isdecimal() or len(value) > 5 or not 1 <= int(value) <= 65535:
        raise ValueError('Management ports must be between 1 and 65535.')
    return int(value)


def configure(deploy, transaction, config, filters, args, values, gid):
    existing = None
    if PROFILE.exists() or PROFILE.is_symlink():
        deploy.require_trusted_path(PROFILE.parent, directory=True)
        deploy.require_trusted_path(PROFILE, directory=False)
        existing = read_profile(PROFILE)
    ssh = os.getenv('SSH_CONNECTION', '').split()
    addresses = getattr(args, 'management_cidr', None) or (existing or {}).get('managementNetworks')
    if not addresses and len(ssh) == 4:
        addresses = [ssh[0]]
    if not addresses:
        return False
    networks = sorted(set((existing or {}).get('managementNetworks', []) + [network(value) for value in addresses]))
    ssh_ports = getattr(args, 'management_ssh_port', None) or (existing or {}).get('sshPorts') or [int(ssh[3]) if len(ssh) == 4 else 22]
    web_ports = getattr(args, 'management_web_port', None) or (existing or {}).get('webPorts') or [int(values.get('MANAGER_PORT', args.manager_port))]
    ssh_ports = sorted(set(ssh_ports + (existing or {}).get('sshPorts', [])))
    web_ports = sorted(set(web_ports + (existing or {}).get('webPorts', [])))
    if any(type(port) is not int or not 1 <= port <= 65535 for port in ssh_ports + web_ports):
        raise ValueError('Management ports must be between 1 and 65535.')
    if values.get('PROXYPERMIT_ACCESS_SAFETY_FILE', str(PROFILE)) != str(PROFILE):
        raise ValueError('Custom access-safety paths require operator-managed deployment.')
    lock_path = Path(str(filters) + '.lock')
    if not lock_path.exists() and not lock_path.is_symlink():
        transaction.track_new(lock_path)
    with file_lock(lock_path):
        store = TinyproxyConfigStore(config)
        if store.read()['staged']:
            raise ValueError('Publish or discard configuration drafts before enabling or updating anti-lockout protection.')
        content = config.read_text(encoding='utf-8-sig')
        native = {row['name'].lower(): row['args'] for row in parse(content)}
        profile = dict(version=1, managementNetworks=networks,
                       proxyNetworks=sorted(set(['127.0.0.1/32', '::1/128'] + networks + (existing or {}).get('proxyNetworks', []))),
                       sshPorts=ssh_ports, webPorts=web_ports,
                       proxyPort=(existing or {}).get('proxyPort', int(native.get('port', ['3128'])[0])),
                       listeners=(existing or {}).get('listeners', native.get('listen', ['0.0.0.0'])))
        rules = firewall_rules(profile)
        for rule in rules:
            deploy.run('ufw', '--dry-run', *rule, capture_output=True)
        transaction.track(PROFILE)
        for path in deploy.UFW_RULE_FILES:
            deploy.require_trusted_path(path, directory=False)
            transaction.track(path)
        atomic_write(PROFILE, json.dumps(profile), 0o640)
        deploy.chown(PROFILE, 0, gid)
        updated = protect_configuration(content, profile)
        if updated != content:
            atomic_write(config, updated, config.stat().st_mode & 0o777)
        for rule in rules:
            deploy.run('ufw', *rule, capture_output=True)
    return True
