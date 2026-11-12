"""Root-pinned management access, independent of shared destination policy."""
import ipaddress
import json
import os
import stat
from pathlib import Path

from .tinyproxy import ConfigError, parse

PROFILE = Path('/etc/proxypermit/access-safety.json')
MARKER = '# ProxyPermit anti-lockout'


def profile_path():
    return Path(os.getenv('PROXYPERMIT_ACCESS_SAFETY_FILE', str(PROFILE)))


def read_profile(location=None):
    try:
        path = Path(location) if location is not None else profile_path()
        if os.name != 'nt' and (path == PROFILE or os.getenv('DEMO_MODE') != 'true'):
            info = path.lstat()
            parent = path.parent.stat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                    or parent.st_uid != 0 or parent.st_mode & 0o022):
                raise ConfigError('The management safety profile is invalid. Contact the operating-system administrator.')
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except ValueError:
        raise ConfigError('The management safety profile is invalid. Contact the operating-system administrator.') from None
    if (not isinstance(value, dict) or type(value.get('version')) is not int or value.get('version') != 1
            or not isinstance(value.get('managementNetworks'), list)
            or not isinstance(value.get('proxyNetworks'), list)):
        raise ConfigError('The management safety profile is invalid. Contact the operating-system administrator.')
    if (not value['managementNetworks'] or len(value['managementNetworks']) > 64
            or type(value.get('proxyPort')) is not int or not 1 <= value['proxyPort'] <= 65535
            or any(not isinstance(value.get(key), list) or not value[key] or any(type(port) is not int or not 1 <= port <= 65535 for port in value[key]) for key in ('sshPorts', 'webPorts'))
            or not isinstance(value.get('listeners'), list)):
        raise ConfigError('The management safety profile is invalid. Contact the operating-system administrator.')
    for network in value['managementNetworks'] + value['proxyNetworks']:
        try:
            valid = isinstance(network, str) and ipaddress.ip_network(network, strict=False).prefixlen != 0
        except ValueError:
            valid = False
        if not valid:
            raise ConfigError('The management safety profile is invalid. Contact the operating-system administrator.')
    return value


def safety_lines(content):
    protected, inside = set(), False
    for number, line in enumerate(content.splitlines(), 1):
        if line == MARKER + ' BEGIN':
            if inside:
                raise ConfigError('Invalid anti-lockout block.')
            inside = True
        if inside:
            protected.add(number)
        if line == MARKER + ' END':
            if not inside:
                raise ConfigError('Invalid anti-lockout block.')
            inside = False
    if inside:
        raise ConfigError('Invalid anti-lockout block.')
    return protected


def without_safety(content):
    protected = safety_lines(content)
    return ''.join(line for number, line in enumerate(content.splitlines(keepends=True), 1) if number not in protected)


def protect_configuration(content, profile):
    """Add the fixed local exceptions before shared ACLs, preserving other text."""
    lines = without_safety(content).splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line.lstrip().lower().startswith(('allow ', 'deny '))), 0)
    rules = [MARKER + ' BEGIN\n'] + [f'Allow {network}\n' for network in profile['proxyNetworks']] + [MARKER + ' END\n']
    return ''.join(lines[:start] + rules + lines[start:])


def ensure_access(content):
    profile = read_profile()
    if profile is None:
        if MARKER in content:
            raise ConfigError('The management safety profile is missing. Contact the operating-system administrator.')
        return
    rows = parse(content)
    acl = [row for row in rows if row['name'].lower() in ('allow', 'deny')]
    expected = profile['proxyNetworks']
    actual = [row['args'] for row in acl[:len(expected)] if row['name'].lower() == 'allow']
    if actual != [[network] for network in expected] or protect_configuration(content, profile) != content:
        raise ConfigError('Anti-lockout protection: keep the pinned management Allow rules before other access rules.')
    values = {row['name'].lower(): row['args'] for row in rows}
    if values.get('port') != [str(profile['proxyPort'])]:
        raise ConfigError('Anti-lockout protection: the proxy port is pinned by the operating-system administrator.')
    baseline = set(profile.get('listeners', []))
    proposed = set(values.get('listen', ['0.0.0.0']))
    if any(address not in proposed and ('::' if ':' in address else '0.0.0.0') not in proposed for address in baseline):
        raise ConfigError('Anti-lockout protection: keep the protected proxy listener reachable.')


def firewall_rules(profile):
    rules = []
    for network in profile['managementNetworks']:
        if ipaddress.ip_network(network).is_loopback:
            continue
        for kind, ports in (('SSH management', profile['sshPorts']), ('Web management', profile['webPorts'])):
            for port in ports:
                rules.append(['prepend', 'allow', 'in', 'from', network, 'to', 'any',
                              'port', str(port), 'proto', 'tcp', 'comment', 'ProxyPermit SAFETY IN - ' + kind])
    for network in profile['proxyNetworks']:
        if not ipaddress.ip_network(network).is_loopback:
            rules.append(['prepend', 'allow', 'in', 'from', network, 'to', 'any',
                          'port', str(profile['proxyPort']), 'proto', 'tcp', 'comment', 'ProxyPermit SAFETY IN - Proxy management'])
    return rules
