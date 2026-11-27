#!/usr/bin/env python3
"""Install ProxyPermit on Debian or Ubuntu. Run without options to open the wizard."""
import argparse
import base64
import getpass
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import time
from collections.abc import Callable
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE))
from app.core.tinyproxy import TinyproxyConfigStore, atomic_write, parse
from app.core.files import file_lock, access_acl, restore_access_acl, sync_directory
from app.core import cluster, cluster_setup
from app.core.ha import ha_firewall_rules, ha_topology
from app.web.ha_network import discover_network
from scripts.deploy.install_terminal import InstallTerminal
from scripts.deploy.logging_policy import MARKER as LOGGING_MARKER, assets as logging_assets, write_asset as write_logging_asset
from app.runtime import policy_recovery
from scripts.deploy.cluster_status import MARKER as CLUSTER_STATUS_MARKER, assets as cluster_status_assets

KEEPALIVED_CONFIG = Path('/etc/keepalived/keepalived.conf')
KEEPALIVED_HEALTH = Path('/etc/keepalived/proxypermit-health')
UFW_RULE_FILES = (Path('/etc/ufw/user.rules'), Path('/etc/ufw/user6.rules'))
UFW_CONFIG = Path('/etc/ufw/ufw.conf')
UFW_DEFAULTS = Path('/etc/default/ufw')
HA_FIREWALL_STATE = Path('/etc/proxypermit/ha-firewall.json')
HA_RETIRED_STATE = Path('/etc/proxypermit/ha-retired.json')
HA_PEER_STATE = (Path('/var/lib/proxypermit/ha-peer.json'), Path('/var/lib/proxypermit/ha-enrollment.json'))
HA_RETIRE_UNIT = Path('/etc/systemd/system/proxypermit-ha-retire.service')
HA_RETIRE_SUDOERS = Path('/etc/sudoers.d/proxypermit-ha-retire')
HA_RETIRE_RULE = 'proxypermit ALL=(root) NOPASSWD: /usr/bin/systemctl start proxypermit-ha-retire.service\n'
HA_SETUP_UNIT = Path('/etc/systemd/system/proxypermit-ha-setup.service')
HA_SETUP_SUDOERS = Path('/etc/sudoers.d/proxypermit-ha-setup')
HA_CERT_RECOVERY_UNIT = Path('/etc/systemd/system/proxypermit-ha-cert-recover.service')


HA_SETUP_RULE = 'proxypermit ALL=(root) NOPASSWD: /usr/bin/systemctl start --no-block proxypermit-ha-setup.service\n'
MAINTENANCE_DIRECTORY = Path('/run/proxypermit-maintenance')
LEGACY_SYNC_SCRIPT = Path('/usr/local/bin/sync_whitelist_v2.py')
LEGACY_KEEPALIVED_HEALTH_TEXT = (
    '#!/usr/bin/python3\nimport socket\n'
    'with socket.create_connection(("127.0.0.1", 3128), timeout=0.5):\n    pass\n'
)
LISTENER_KEEPALIVED_HEALTH_TEXT = (
    '#!/usr/bin/python3\n'
    + (SOURCE / 'app/core/listeners.py').read_text(encoding='utf-8')
    + '\ntry:\n    healthy = tcp_listener(3128)\nexcept OSError:\n    healthy = False\n'
    + 'raise SystemExit(0 if healthy else 1)\n'
)


def ha_certificate_recovery_unit_text(base):
    return ('[Unit]\nDescription=Recover committed ProxyPermit HA certificate renewal\n'
            'Before=proxypermit.service proxypermit-ha-tls.service nginx.service\n'
            '[Service]\nType=oneshot\nLogNamespace=proxypermit\nUMask=0077\n'
            f'WorkingDirectory={base}/current\nExecStart={base}/current/.venv/bin/python '
            '-m scripts.deploy.ha_certificates recover --no-restart '
            f'--install-dir {base}\n[Install]\nWantedBy=multi-user.target\n')


def health_helper_text(configuration=Path('/etc/tinyproxy/tinyproxy.conf')):
    return ('#!/usr/bin/python3\n' + (SOURCE / 'app/core/proxy_health.py').read_text(encoding='utf-8')
            + '\nraise SystemExit(0 if proxy_responsive(' + repr(str(configuration)) + ') else 1)\n')


KEEPALIVED_HEALTH_TEXT = health_helper_text()


def keepalived_text(ha):
    priority = 150 if ha['node'] == 'a' else 100
    return f'''# Managed by ProxyPermit
global_defs {{
    router_id ProxyPermit_{ha['vrid']}_{ha['node']}
    enable_script_security
    script_user nobody
}}
vrrp_script proxypermit_health {{
    script "{KEEPALIVED_HEALTH}"
    user proxypermit
    interval 2
    timeout 2
    fall 2
    rise 2
}}
vrrp_instance PROXYPERMIT {{
    state BACKUP
    interface {ha['interface']}
    virtual_router_id {ha['vrid']}
    priority {priority}
    advert_int 1
    unicast_src_ip {ha['local']}
    check_unicast_src
    track_src_ip
    unicast_peer {{
        {ha['peer']}
    }}
    virtual_ipaddress {{
        {ha['vip']} dev {ha['interface']}
    }}
    track_script {{
        proxypermit_health
    }}
}}
'''


def tinyproxy_text(filter_path, ha):
    listen = '0.0.0.0' if ha else '127.0.0.1'
    clients = f"Allow {ha['clients']}\n" if ha else 'Allow ::1\n'
    return f'''# Managed by ProxyPermit
User tinyproxy
Group tinyproxy
Port 3128
Listen {listen}
Timeout 600
MaxClients 100
Allow 127.0.0.1
{clients}ConnectPort 443
Syslog On
LogLevel Warning
Filter "{filter_path}"
FilterType ere
FilterDefaultDeny Yes
FilterURLs No
'''


class SnapshotIdentity(TypedDict):
    path: str
    existed: bool


class SnapshotEntry(SnapshotIdentity, total=False):
    symlink: str
    mode: int
    uid: int
    gid: int
    directory: bool
    backup: str
    accessAcl: str | None


def env_values(path):
    """Read keys for discovery only; the actual legacy file is preserved verbatim."""
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        match = re.match(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$', line)
        if match:
            values[match[1]] = match[2].strip('"\'')
    return values


def build_plan(root, install_dir, requested_mode=None, requested_server=None, ha=None):
    root = Path(root)
    def host(path):
        return root / path.lstrip('/')
    candidates = [host('/etc/proxypermit/.env'), host(install_dir + '/.env')]
    for candidate in candidates:
        if candidate.parent.exists() and not os.access(candidate.parent, os.R_OK | os.X_OK):
            raise PermissionError('Cannot access the existing configuration directory. Run the plan with sudo to detect the existing installation.')
    # Source .env matters for legacy in-place upgrades, not arbitrary dry-run roots.
    if root == Path('/'):
        candidates.append(SOURCE / '.env')
    existing_env = next((p for p in candidates if p.exists()), None)
    values = env_values(existing_env) if existing_env else {}
    legacy_ha = bool(values.get('NODE0_ADDRESS') and values.get('NODE1_ADDRESS'))
    current_mode = values.get('DEPLOYMENT_MODE') or ('ha' if legacy_ha else 'standalone')
    mode = requested_mode or current_mode
    server = requested_server or values.get('APP_SERVER', 'gunicorn')
    if server not in ('gunicorn', 'granian'):
        raise ValueError('APP_SERVER must be gunicorn or granian.')
    if existing_env and requested_mode and mode != current_mode:
        raise ValueError('Change the topology separately. This installer cannot convert an existing HA deployment to standalone.')
    if mode == 'ha' and not existing_env:
        if ha is None:
            raise ValueError('A new HA cluster requires the --ha options. See install.sh --help.')
    elif ha is not None:
        raise ValueError('The --ha options apply only to a new installation with --mode ha.')
    config = values.get('TINYPROXY_CONFIG_PATH', '/etc/tinyproxy/tinyproxy.conf')
    whitelist = values.get('WHITELIST_PATH', '/etc/tinyproxy/whitelist.txt')
    steps = [
        'Check Debian or Ubuntu, root permissions, and existing configuration',
        'Back up configuration, secrets, services, and the current release link',
        'Install Python venv, Tinyproxy, sudo, UFW, and logrotate with apt',
        'Build a new release in a dedicated virtual environment and run the tests',
        'Preserve the existing environment, filter rules, Tinyproxy configuration, and HA settings',
        'Create an administrator and configure an allowlist for a fresh installation',
        'Install a service account and grant limited service control',
        'Switch to the new release and check the web service',
    ]
    notes = [
        'Upgrades preserve the existing HA services and configuration.',
        'Rollback leaves installed packages in place.',
    ]
    plan: dict[str, Any] = dict(schemaVersion=1, mode=mode, server=server, action='upgrade' if existing_env else 'install',
                installDir=install_dir, existingEnv=str(existing_env) if existing_env else None,
                configPath=config, whitelistPath=whitelist, steps=steps, notes=notes)
    if values.get('TINYPROXY_RELOAD_ENABLED', 'false').lower() == 'true':
        steps.append('Grant the native Tinyproxy account read-only ACLs on its configuration and filter file; verify native reads without management-group access.')
        notes.append('Reload remains version-limited. Existing custom ACL masks are not broadened; review non-readable masks before opting in.')
    if mode == 'standalone' or ha or values.get('PROXYPERMIT_MANAGED_HA') == 'true':
        steps.append('Install private journal namespaces and hourly age/size retention for audit, management, and Tinyproxy logs.')
        notes.append('Logging activation restarts local Tinyproxy and managed Keepalived. Existing host journals and external log files keep their own retention policy.')
    if mode == 'standalone':
        notes.append('Standalone mode omits Keepalived and the sync timer.')
    if ha:
        plan['ha'] = ha
        plan['firewallRules'] = ha_firewall_rules(ha)
        steps.append('Configure scoped UFW rules for the proxy VIP and bidirectional VRRP.')
        steps.append('Install and configure Keepalived, check the local Tinyproxy port, and start VRRP.')
        notes.append('Repeat on the other host with the same cluster settings. Publish policy on each node.')
    if mode == 'ha' and values.get('PROXYPERMIT_MANAGED_HA') == 'true':
        plan['managedHa'] = True
        steps.append('Upgrade the recognized empty-connect health helper to a bounded local HTTP responsiveness check; preserve custom helpers.')
    return plan


class Transaction:
    """Configuration rollback; package changes are deliberately outside the scope."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, mode=0o700)
        self.entries: list[SnapshotEntry] = []

    def track(self, path):
        path = Path(path)
        if any(item['path'] == str(path) for item in self.entries):
            return
        item: SnapshotEntry = {'path': str(path), 'existed': path.exists() or path.is_symlink()}
        if path.is_symlink():
            item['symlink'] = os.readlink(path)
        elif path.exists():
            if not path.is_file() and not path.is_dir():
                raise ValueError(f'Expected a file: {path}')
            info = path.stat()
            item.update(mode=info.st_mode & 0o777, uid=info.st_uid, gid=info.st_gid)
            acl = access_acl(path)
            item['accessAcl'] = base64.b64encode(acl).decode('ascii') if acl is not None else None
            if path.is_dir():
                item['directory'] = True
            else:
                name = str(len(self.entries))
                shutil.copy2(path, self.directory / name)
                os.chmod(self.directory / name, 0o600)
                item['backup'] = name
        self.entries.append(item)
        atomic_write(self.directory / 'manifest.json', json.dumps(self.entries, indent=2))

    def track_new(self, path):
        """Record creation in an untrusted directory without reading its files."""
        path = Path(path)
        if path.exists() or path.is_symlink():
            raise ValueError(f'Expected a new file: {path}')
        self.entries.append({'path': str(path), 'existed': False})
        atomic_write(self.directory / 'manifest.json', json.dumps(self.entries, indent=2))

    @staticmethod
    def _restore_permissions(path: Path, item: SnapshotEntry) -> None:
        mode, uid, gid = item.get('mode'), item.get('uid'), item.get('gid')
        if mode is None or uid is None or gid is None:
            raise ValueError(f'Missing file metadata in the recovery manifest: {path}')
        os.chmod(path, mode)
        if os.name != 'nt':
            os.chown(path, uid, gid)  # pylint: disable=no-member
        if 'accessAcl' in item:
            acl = item['accessAcl']
            restore_access_acl(path, base64.b64decode(acl, validate=True) if acl is not None else None)

    def restore(self):
        for item in reversed(self.entries):
            path = Path(item['path'])
            if item.get('directory'):
                self._restore_permissions(path, item)
                continue
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif not item['existed'] and path.is_dir():
                path.rmdir()
            if 'symlink' in item:
                path.symlink_to(item['symlink'])
            elif 'backup' in item:
                shutil.copy2(self.directory / item['backup'], path)
                self._restore_permissions(path, item)


def run(*args, **kwargs):
    return subprocess.run(list(args), check=True, **kwargs)


def require_trusted_path(path, *, directory):
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError(f'Expected a root-owned path without group or public write access: {path}')


def upgrade_health_helper(transaction, path=KEEPALIVED_HEALTH, content=None):
    """Replace only our exact old helper, with rollback and existing permissions."""
    if not path.exists() and not path.is_symlink():
        return False
    require_trusted_path(path.parent, directory=True)
    require_trusted_path(path, directory=False)
    expected = [text.encode('utf-8') for text in (LEGACY_KEEPALIVED_HEALTH_TEXT, LISTENER_KEEPALIVED_HEALTH_TEXT)]
    if path.stat().st_size not in [len(value) for value in expected] or path.read_bytes() not in expected:
        return False
    transaction.track(path)
    atomic_write(path, content or KEEPALIVED_HEALTH_TEXT, path.stat().st_mode & 0o777)
    return True


def upgrade_cluster_agent(transaction, path, previous, candidate):
    """Update the native agent only when it matches the previous release."""
    if not path.is_file() or path.is_symlink() or not previous.is_file():
        return
    require_trusted_path(path.parent, directory=True)
    require_trusted_path(path, directory=False)
    old = previous.read_text(encoding='utf-8').encode('utf-8')
    new = candidate.read_text(encoding='utf-8')
    if path.stat().st_size != len(old) or path.read_bytes() != old or old == new.encode('utf-8'):
        return
    transaction.track(path)
    atomic_write(path, new, path.stat().st_mode & 0o777)


@contextmanager
def maintenance_lock():
    require_trusted_path(MAINTENANCE_DIRECTORY.parent, directory=True)
    MAINTENANCE_DIRECTORY.mkdir(mode=0o700, exist_ok=True)
    require_trusted_path(MAINTENANCE_DIRECTORY, directory=True)
    lock = MAINTENANCE_DIRECTORY / 'install.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(fd)
    require_trusted_path(lock, directory=False)
    with file_lock(lock, timeout=1):
        yield


def service_state(unit):
    return {key: subprocess.run(['systemctl', f'is-{key}', '--quiet', unit], check=False).returncode == 0
            for key in ('active', 'enabled')}


def restore_auxiliary_services(services):
    for unit, state in services.items():
        # An absent package has no unit to restore.
        if subprocess.run(['systemctl', 'cat', unit], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, check=False).returncode:
            continue
        run('systemctl', 'enable' if state['enabled'] else 'disable', unit)
        run('systemctl', 'restart' if state['active'] else 'stop', unit)


def check_ha_host(ha, config_path):
    if any(path.exists() or path.is_symlink() for path in (config_path, KEEPALIVED_CONFIG, KEEPALIVED_HEALTH)):
        raise ValueError('A new HA deployment requires a clean host without Tinyproxy or Keepalived configuration.')
    if any(service_state(unit)['active'] for unit in ('tinyproxy.service', 'keepalived.service')):
        raise ValueError('Tinyproxy or Keepalived is already running on this host.')
    interfaces = json.loads(run('ip', '-j', '-4', 'address', 'show', 'dev', ha['interface'],
                                capture_output=True, text=True).stdout)
    if not any(address['local'] == ha['local'] for interface in interfaces
               for address in interface.get('addr_info', [])):
        raise ValueError(f"Local node IP {ha['local']} is not assigned to {ha['interface']}.")


def start_ha():
    for _ in range(25):
        try:
            with socket.create_connection(('127.0.0.1', 3128), timeout=0.5):
                break
        except OSError:
            time.sleep(0.2)
    else:
        raise RuntimeError('Tinyproxy is not listening on the local proxy port.')
    run('systemctl', 'enable', 'keepalived.service')
    run('systemctl', 'restart', 'keepalived.service')
    run('systemctl', 'is-active', '--quiet', 'keepalived.service')


def ufw_added_rules():
    output = run('ufw', 'show', 'added', capture_output=True, text=True).stdout
    return {line for line in output.splitlines() if line.startswith('ufw ')}


def apply_ha_firewall(rules):
    existing = ufw_added_rules()
    for rule in rules:
        run('ufw', '--dry-run', *rule, capture_output=True)
    for rule in rules:
        run('ufw', *rule)
    updated = ufw_added_rules()
    if existing - updated:
        raise ValueError('An existing UFW rule conflicts with the HA rules. Restoring the original firewall rules.')
    owned = sorted(updated - existing)
    comments = {rule[-1] for rule in rules}
    if any(shlex.split(line)[-1] not in comments for line in owned):
        raise ValueError('UFW added an unexpected rule. Restoring the original firewall rules.')
    return owned


def ha_retire_unit_text(install_dir):
    return (
        '[Unit]\nDescription=Retire the local ProxyPermit HA node\n\n'
        '[Service]\nType=oneshot\nWorkingDirectory=/\nTimeoutStartSec=75\nKillSignal=SIGINT\n'
        f'ExecStart=/usr/bin/python3 -I {install_dir}/current/scripts/deploy/install.py '
        f'--install-dir {install_dir} --retire-ha --apply\n'
    )


def ha_setup_unit_text(install_dir):
    return (
        '[Unit]\nDescription=Configure ProxyPermit HA from an approved local plan\n'
        '[Service]\nType=oneshot\nWorkingDirectory=/\nTimeoutStartSec=300\nKillSignal=SIGINT\n'
        'UMask=0077\nPrivateTmp=yes\nProtectHome=yes\n'
        f'ExecStart=/usr/bin/python3 -I {install_dir}/current/scripts/deploy/install.py '
        f'--install-dir {install_dir} --configure-ha --apply\n'
    )


def secure_ha_helper_source(base, release):
    for directory in (base, *base.parents, release.parent, release):
        require_trusted_path(directory, directory=True)
    for directory in (release / 'app', release / 'scripts'):
        for path in (directory, *directory.rglob('*')):
            if path.is_symlink() or path.stat().st_uid != 0:
                raise ValueError(f'The HA helper requires root-owned release files: {path}')
            path.chmod(path.stat().st_mode & ~0o022)


def prepare_release_permissions(release):
    """Make only the new, secret-free code tree readable by the runtime account."""
    for path in (release, *release.rglob('*')):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            path.chmod(0o755)
        elif stat.S_ISREG(info.st_mode):
            path.chmod(0o755 if info.st_mode & 0o111 else 0o644)
        else:
            raise ValueError('Release code must contain only regular files and directories.')


def prepare_release_parent(path):
    """Make newly created install ancestors traversable even under a private umask."""
    pending = []
    while not path.exists() and not path.is_symlink():
        pending.append(path)
        path = path.parent
    for directory in (path, *path.parents):
        require_trusted_path(directory, directory=True)
    for directory in reversed(pending):
        directory.mkdir(mode=0o755)
        directory.chmod(0o755)


def configure_reload_access(config_path, filter_path):
    """Grant only native config/filter reads, never management-secret access."""
    if sys.platform != 'linux':
        raise ValueError('Reload permissions require Linux.')
    import pwd  # pylint: disable=import-error
    if any(path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1 for path in (config_path, filter_path)):
        raise ValueError('Reload permissions require regular, unlinked native files.')
    users = [row['args'][0] for row in parse(config_path.read_text(encoding='utf-8-sig')) if row['name'].lower() == 'user']
    user = users[-1] if users else 'root'
    account = pwd.getpwuid(int(user)) if user.isdigit() else pwd.getpwnam(user)
    if account.pw_uid == 0:
        return
    for path in (config_path, filter_path):
        if access_acl(path) is not None and not path.stat().st_mode & 0o040:
            raise ValueError('The native file has a custom ACL with a non-readable mask. Review its effective permissions before enabling reload.')
    for path in (config_path, filter_path):
        permission = f'u:{account.pw_uid}:r--'
        if access_acl(path) is None and not path.stat().st_mode & 0o040:
            permission += ',m::r--'
        run('setfacl', '--no-mask', '-m', permission, str(path))
        run('runuser', '-u', account.pw_name, '--', 'test', '-r', str(path))


def prepare_filter_backups(directory, transaction, uid, gid, chown):
    # Parent remains frozen during the unprivileged import check. Create its
    # application-owned child first; never follow a pre-existing backup symlink.
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise ValueError('Filter backups require a regular directory without symlinks.')
    transaction.track(directory)
    directory.mkdir(mode=0o750, exist_ok=True)
    chown(directory, uid, gid)
    os.chmod(directory, 0o750)


def validate_native_backups(directory, uid):
    # This tree is writable by the application. Do not recursively chown,
    # chmod or snapshot its children as root: a pathname swap could redirect a
    # privileged operation. Legacy root-owned drafts need explicit OS review.
    if not directory.exists() and not directory.is_symlink():
        return
    if sys.platform != 'linux':
        raise ValueError('Native draft backup validation requires Linux.')
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)  # pylint: disable=no-member
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('Native draft backups require a private application-owned directory. Review legacy ownership through OS administration.')
        for name in os.listdir(descriptor):
            child = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if (not stat.S_ISREG(child.st_mode) or child.st_nlink != 1 or child.st_uid != uid
                    or stat.S_IMODE(child.st_mode) != 0o600):
                raise ValueError('Native draft backup ownership or permissions require OS review; automatic recursive migration was refused.')
    finally:
        os.close(descriptor)


def remove_owned_ha_firewall(owned, *, include_cluster=False):
    existing = ufw_added_rules()
    present = set(owned) & existing
    missing = set(owned) - existing
    existing_comments = {shlex.split(line)[-1] for line in existing}
    if any(shlex.split(line)[-1] in existing_comments for line in missing):
        raise ValueError('A managed HA firewall rule was edited. Review it before retiring this node.')
    commands = []
    prefixes = ('ProxyPermit HA ', 'ProxyPermit cluster ') if include_cluster else ('ProxyPermit HA ',)
    for line in sorted(present):
        argv = shlex.split(line)
        if argv[:2] != ['ufw', 'allow'] or argv[-2] != 'comment' or not argv[-1].startswith(prefixes):
            raise ValueError('The managed HA firewall rule record is invalid.')
        commands.append(argv)
    for argv in commands:
        run('ufw', '--force', 'delete', *argv[1:])
    if ufw_added_rules() != existing - present:
        raise ValueError('The firewall changed unexpectedly while removing the HA rules.')


def reload_ufw():
    if ufw_enabled():
        run('ufw', 'reload')


def ufw_enabled():
    return env_values(UFW_CONFIG).get('ENABLED') == 'yes'


def prepare_ufw_activation(transaction):
    for path in (UFW_CONFIG, UFW_DEFAULTS):
        require_trusted_path(path.parent, directory=True)
        require_trusted_path(path, directory=False)
    if ufw_enabled():
        return False
    if env_values(UFW_DEFAULTS).get('DEFAULT_INPUT_POLICY') not in ('DROP', 'REJECT'):
        raise ValueError('Set UFW to deny or reject incoming traffic before enabling it.')
    transaction.track(UFW_CONFIG)
    return True


def ufw_rule_fingerprints():
    if any(path.is_symlink() or not path.is_file() for path in UFW_RULE_FILES):
        raise ValueError('UFW rule files are missing or have changed location.')
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in UFW_RULE_FILES}


def probe_manager(values, expected_fingerprint=None):
    """Check the configured HTTP listener, including legacy TLS/Unix sockets."""
    import http.client
    import socket
    import ssl
    bind = values.get('APP_BIND') or values.get('GUNICORN_BIND') or f"{values.get('MANAGER_BIND', '127.0.0.1')}:{values.get('MANAGER_PORT', '8080')}"
    override = values.get('GUNICORN_USE_TLS', '').lower()
    tls = override in ('true', '1', 'yes') or (not override and values.get('MANAGER_SCHEME') == 'https')
    connection = None
    try:
        if bind.startswith('unix:'):
            address_family = getattr(socket, 'AF_UNIX', None)
            if address_family is None:
                return False
            connection = http.client.HTTPConnection('localhost', timeout=2)
            connection.sock = socket.socket(address_family, socket.SOCK_STREAM)
            connection.sock.settimeout(2)
            connection.sock.connect(bind[5:])
        else:
            host, _, port = bind.rpartition(':')
            host = host.strip('[]')
            host = {'0.0.0.0': '127.0.0.1', '::': '::1'}.get(host, host)
            if tls:
                # Local readiness only; certificate trust is checked by the
                # public reverse proxy/client, not altered by this probe.
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                connection = http.client.HTTPSConnection(host, int(port), timeout=2, context=context)
                if expected_fingerprint:
                    connection.connect()
                    raw = connection.sock.getpeercert(binary_form=True) if isinstance(connection.sock, ssl.SSLSocket) else None
                    if not raw or hashlib.sha256(raw).hexdigest() != expected_fingerprint:
                        return False
            else:
                connection = http.client.HTTPConnection(host, int(port), timeout=2)
        connection.request('GET', '/')
        response = connection.getresponse()
        response.read()
        return response.status == 200
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        if connection is not None:
            connection.close()


def wait_manager(probe, timeout=60):
    deadline = time.monotonic() + timeout
    while not probe():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)
    return True


def manager_values(env_path, unit_path):
    values = env_values(env_path)
    if unit_path.exists():
        for key in ('GUNICORN_BIND', 'GUNICORN_USE_TLS'):
            match = re.search(r'^Environment=' + key + r'=([^\r\n]+)$', unit_path.read_text(), re.M)
            if match and key not in values:
                values[key] = match[1]
    return values


def service_text(release, mode, bind, port, preserve_binding=False, tls=False):
    # Existing HA requires legacy root-level Keepalived/sync operations. Preserve
    # that compatibility explicitly; standalone uses a dedicated account.
    account = '' if mode == 'ha' else 'User=proxypermit\nGroup=proxypermit\nSupplementaryGroups=adm systemd-journal\n'
    binding = '' if preserve_binding else f'Environment=GUNICORN_BIND={bind}:{port}\nEnvironment=GUNICORN_USE_TLS={str(tls).lower()}\n'
    return f'''[Unit]
Description=ProxyPermit Tinyproxy Manager
After=network.target tinyproxy.service
Wants=tinyproxy.service

[Service]
Type=simple
KillMode=mixed
TimeoutStopSec=20
{account}WorkingDirectory={release}
EnvironmentFile=/etc/proxypermit/.env
{binding}Environment=PYTHONDONTWRITEBYTECODE=1
RuntimeDirectory=proxypermit
RuntimeDirectoryMode=0755
StateDirectory=proxypermit
LogsDirectory=proxypermit
ExecStart={release}/.venv/bin/python -m app.runtime.server
Restart=on-failure
RestartSec=3
PrivateTmp=true
ProtectHome=true
ProtectSystem=full
ReadWritePaths=/etc/tinyproxy /var/lib/proxypermit /var/log/proxypermit /run/proxypermit
UMask=0027

[Install]
WantedBy=multi-user.target
'''


def require_linux_root(action: str) -> None:
    if sys.platform != 'linux' or os.geteuid() != 0:  # pylint: disable=no-member
        raise ValueError(f'{action} requires Linux and root privileges (sudo).')


def service_ownership() -> tuple[int, int, Callable[[Path | str, int, int], None]]:
    if sys.platform == 'linux':
        import pwd  # pylint: disable=import-error
        account = pwd.getpwnam('proxypermit')
        return account.pw_uid, account.pw_gid, os.chown  # pylint: disable=no-member
    raise ValueError('Installation requires Linux.')


def require_policy_recovered(config_path, filter_path, operation):
    for path in (config_path, filter_path):
        checkpoint = TinyproxyConfigStore(path).pending_path
        if checkpoint.exists() or checkpoint.is_symlink():
            raise ValueError(f'Recover the interrupted Tinyproxy policy before {operation}.')


def installed_policy_paths():
    values = env_values(Path('/etc/proxypermit/.env'))
    return (values.get('TINYPROXY_CONFIG_PATH') or '/etc/tinyproxy/tinyproxy.conf',
            values.get('WHITELIST_PATH') or '/etc/tinyproxy/whitelist.txt')


def apply(plan, args):
    require_linux_root('Installation')
    cluster_upgrade = bool(getattr(args, 'cluster_maintenance', False))
    if cluster_upgrade:
        validate_cluster_upgrade(plan, args)
    else:
        cluster.require_independent_control('installing or upgrading this node')
    os_release = Path('/etc/os-release').read_text(encoding='utf-8')
    if not re.search(r'^ID=(?:"?)(ubuntu|debian)(?:"?)$', os_release, re.M):
        raise ValueError('This installer supports Debian and Ubuntu with systemd.')
    if not Path('/run/systemd/system').exists():
        raise ValueError('A running systemd instance is required.')
    if plan['mode'] == 'standalone' and plan['action'] == 'upgrade':
        for unit in ('keepalived.service', 'whitelist-sync-v2.timer'):
            if subprocess.run(['systemctl', 'is-active', '--quiet', unit], check=False).returncode == 0:
                raise ValueError('An HA service is active in standalone mode. Check the topology first.')
    managed = plan['mode'] == 'standalone' or plan.get('ha') or plan.get('managedHa')
    upgrading = managed and plan['action'] == 'upgrade'
    if upgrading:
        require_policy_recovered(plan['configPath'], plan['whitelistPath'], 'changing the installed release')
    from scripts.deploy.cluster_maintenance import rolling
    with maintenance_lock(), (rolling(lambda: cluster_upgrade_health(plan['installDir']), plan['installDir']) if cluster_upgrade else nullcontext()):
        if (getattr(args, 'reviewed_action', None) or getattr(args, 'enable_ufw', False)
                or getattr(args, 'manager_tls', None)):
            existing = build_plan('/', args.install_dir, args.mode, args.server, plan.get('ha'))
            if existing['action'] != plan['action'] or existing['existingEnv'] != plan['existingEnv']:
                raise ValueError('The installation changed. Run the wizard again and review the new plan.')
        validate_ufw_activation(plan, args)
        if args.upgrade or cluster_upgrade:
            installed_deployment('/', plan['installDir'])
            verify_live_manager(plan['installDir'])
        source = SOURCE
        if cluster_upgrade and getattr(args, 'rollback', None):
            source = cluster_rollback_release(plan['installDir'], args.rollback)
            if str(source) != plan['rollbackRelease']:
                raise ValueError('The reviewed rollback release has changed.')
        directory = Path(plan['configPath']).parent
        if managed and directory.exists():
            from scripts.deploy.ha_setup import frozen_proxy_directory
            lock_path = Path(plan['whitelistPath'] + '.lock')
            original = directory.stat()
            try:
                with frozen_proxy_directory(directory, lock_path):
                    for path in (Path(plan['configPath']), Path(plan['whitelistPath'])):
                        if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_nlink != 1)):
                            raise ValueError('Managed proxy configuration requires regular, unlinked files.')
                    # If there is no lock yet, the frozen directory keeps the
                    # application user from creating it during maintenance.
                    with file_lock(lock_path) if upgrading and lock_path.exists() else nullcontext():
                        if upgrading:
                            require_policy_recovered(plan['configPath'], plan['whitelistPath'], 'changing the installed release')
                        _apply_locked(plan, args, frozen=True, source=source)
            except BaseException:
                if os.name != 'nt':
                    os.chown(directory, original.st_uid, original.st_gid)  # pylint: disable=no-member
                raise
            os.chmod(directory, 0o775)
        else:
            _apply_locked(plan, args, source=source)


def validate_cluster_upgrade(plan, args):
    if (not cluster.reserved() or not (args.upgrade or getattr(args, 'rollback', None)) or plan['action'] != 'upgrade'
            or plan['mode'] != 'ha' or not plan.get('managedHa') or plan.get('ha')):
        raise ValueError('Cluster maintenance requires an OS-protected managed HA installation.')


def cluster_upgrade_health(install_dir):
    from scripts.deploy import ha_resource
    verify_live_manager(install_dir)
    return (probe_manager(env_values(Path('/etc/proxypermit/.env')))
        and ha_resource.command('systemctl', 'is-active', '--quiet', 'tinyproxy.service').returncode == 0
        and ha_resource.command('runuser', '-u', 'proxypermit', '--', ha_resource.HEALTH).returncode == 0)


def _apply_locked(plan, args, frozen=False, source=None):
    from scripts.deploy import management_tls
    source = SOURCE if source is None else source
    cluster_upgrade = bool(getattr(args, 'cluster_maintenance', False))
    release_rollback = cluster_upgrade and bool(getattr(args, 'rollback', None))
    ha = plan.get('ha')
    managed = plan['mode'] == 'standalone' or ha or plan.get('managedHa')
    direct_tls = getattr(args, 'manager_tls', None) in ('local-ca', 'custom')
    tls_profile = fingerprint = None
    base = Path(plan['installDir'])
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    current = base / 'current'
    if current.exists() and not current.is_symlink():
        raise ValueError('The current path already exists and is not a release symlink. Move it manually first.')
    release = base / 'releases' / stamp
    env_path = Path('/etc/proxypermit/.env')
    accounts_path = Path('/var/lib/proxypermit/accounts.json')
    config_path, filter_path = Path(plan['configPath']), Path(plan['whitelistPath'])
    config_existed = config_path.exists()
    if release_rollback and not (config_existed and filter_path.is_file()):
        raise ValueError('Restore the missing active proxy files before rolling back a release.')
    if ha:
        check_ha_host(ha, config_path)
    if managed and (config_path.parent != Path('/etc/tinyproxy') or filter_path.parent != Path('/etc/tinyproxy')):
        raise ValueError('Custom configuration paths require manually configured permissions. Use /etc/tinyproxy with this installer.')
    if getattr(args, 'manager_tls', None) == 'custom':
        management_tls.protected(args.manager_cert, 128 * 1024)
        management_tls.protected(args.manager_key, 32 * 1024, private=True)
    if managed or HA_RETIRE_UNIT.is_file():
        prepare_release_parent(release.parent)
    transaction = Transaction(base / 'backups' / stamp)
    for path in (current, env_path, config_path, filter_path,
                 base / 'installed.json', Path('/etc/systemd/system/proxypermit.service'), Path('/etc/sudoers.d/proxypermit')):
        transaction.track(path)
    legacy_values = env_values(Path(plan['existingEnv'])) if plan['existingEnv'] else {}
    migrate_accounts = managed and legacy_values.get('AUTH_ACCOUNTS_FILE') == '/etc/proxypermit/accounts.json'
    if migrate_accounts:
        if accounts_path.exists() or accounts_path.is_symlink():
            raise ValueError('The account migration destination already exists.')
        transaction.track(accounts_path)
    if ha:
        transaction.track(KEEPALIVED_CONFIG)
        transaction.track(KEEPALIVED_HEALTH)
        if HA_FIREWALL_STATE.exists() or HA_FIREWALL_STATE.is_symlink():
            raise ValueError('An existing HA firewall record must be reviewed before a fresh HA installation.')
        transaction.track(HA_FIREWALL_STATE)
        for path in (HA_RETIRE_UNIT, HA_RETIRE_SUDOERS):
            if path.exists() or path.is_symlink():
                raise ValueError('An existing HA retirement helper must be reviewed before installation.')
            transaction.track(path)
    if plan['action'] == 'install':
        transaction.track(accounts_path)
    update_legacy_sync = not managed and LEGACY_SYNC_SCRIPT.is_file() and not LEGACY_SYNC_SCRIPT.is_symlink()
    if update_legacy_sync:
        transaction.track(LEGACY_SYNC_SCRIPT)
    if managed:
        for path in (HA_SETUP_UNIT, HA_SETUP_SUDOERS):
            transaction.track(path)
        for path in (Path('/etc/proxypermit'), Path('/var/lib/proxypermit'), Path('/var/log/proxypermit'),
                     config_path.parent, filter_path.parent / '.backups'):
            if path.exists():
                transaction.track(path)
        for key in ('WHITELIST_ENTRY_LOG_PATH', 'SESSION_STATE_FILE'):
            path = Path(legacy_values[key]) if legacy_values.get(key) else None
            if path and path.is_file():
                transaction.track(path)
    log_assets = logging_assets(release, {**legacy_values, 'DEPLOYMENT_MODE': plan['mode']}) if managed else {}
    if cluster_upgrade:
        # The cluster owns all Keepalived configuration, including drop-ins.
        log_assets = {path: content for path, content in log_assets.items() if 'keepalived.service.d' not in path.parts}
    cluster_assets = cluster_status_assets(current) if managed else {}
    if managed:
        recovery = policy_recovery.DROP_IN
        if recovery.is_symlink() or (recovery.exists() and not recovery.read_text(encoding='utf-8').startswith(policy_recovery.MARKER)):
            raise ValueError('An administrator-owned Tinyproxy policy recovery service requires review.')
        transaction.track(recovery)
        run('systemctl', 'cat', 'systemd-journald@.service', stdout=subprocess.DEVNULL)
        for path in log_assets:
            if path.is_symlink() or (path.is_file() and not path.read_text().startswith(LOGGING_MARKER)):
                raise ValueError(f'An administrator-owned logging policy requires review: {path}')
            transaction.track(path)
        for path in cluster_assets:
            if path.is_symlink() or (path.is_file() and not path.read_text().startswith(CLUSTER_STATUS_MARKER)):
                raise ValueError(f'An administrator-owned cluster status service requires review: {path}')
            transaction.track(path)
    previous = service_state('proxypermit.service')
    auxiliary_units = ['tinyproxy.service'] if managed else []
    if ha or plan.get('managedHa'):
        auxiliary_units.extend(('proxypermit-ha-tls.service', 'proxypermit-ha-policy.timer'))
        if not cluster_upgrade:
            auxiliary_units.append('keepalived.service')
    if managed:
        auxiliary_units.extend(('proxypermit-audit-retention.timer', 'proxypermit-journal-retention.timer',
                                'systemd-journald@proxypermit.service', 'systemd-journald@proxypermit-traffic.service',
                                'proxypermit-cluster-status.timer', 'proxypermit-cluster-status.service'))
    if getattr(args, 'manager_tls', None) == 'local-ca':
        auxiliary_units.extend((management_tls.RENEW + '.timer', management_tls.RECOVER))
    auxiliary = {unit: service_state(unit) for unit in auxiliary_units}
    service_snapshot: dict[str, Any] = dict(**previous, auxiliary=auxiliary, ufw=False)
    if cluster_upgrade:
        installed, _, _, _ = installed_deployment('/', plan['installDir'])
        service_snapshot['protectedUpgrade'] = {'before': installed['release'], 'after': str(release)}
    atomic_write(transaction.directory / 'service-state.json', json.dumps(service_snapshot))
    touched_service = False
    touched_ufw = False
    activate_ufw = False
    starting_ufw = False
    password = None
    if plan['action'] == 'install':
        if args.admin_password_file:
            password = Path(args.admin_password_file).read_text(encoding='utf-8').rstrip('\r\n')
        else:
            while True:
                password = getpass.getpass('New administrator password (at least 12 characters): ')
                if len(password) < 12:
                    issue = 'Use a password of at least 12 characters.'
                elif password != getpass.getpass('Repeat password: '):
                    issue = 'The passwords do not match.'
                else:
                    break
                if not sys.stdin.isatty():
                    raise ValueError(issue)
                print(f'  {issue} Try again, or press Ctrl+C to cancel.')
        if len(password) < 12:
            raise ValueError('Use a password of at least 12 characters.')
    try:
        if not cluster_upgrade:
            run('apt-get', 'update')
            packages = ['python3-venv', 'tinyproxy', 'sudo', 'logrotate', 'ufw', 'acl']
            if direct_tls:
                packages.append('openssl')
            if ha:
                packages.append('keepalived')
            run('apt-get', 'install', '-y', *packages)
        if getattr(args, 'enable_ufw', False):
            activate_ufw = prepare_ufw_activation(transaction)
        if ha:
            for path in UFW_RULE_FILES:
                if path.is_symlink() or not path.is_file():
                    raise ValueError(f'Expected a regular UFW rule file: {path}')
                transaction.track(path)
            service_snapshot['ufw'] = True
            atomic_write(transaction.directory / 'service-state.json', json.dumps(service_snapshot))
        release.parent.mkdir(parents=True, exist_ok=True)
        ignore = shutil.ignore_patterns(
            '.git', '.venv', 'venv', '__pycache__', '.demo', '.env', '.env.*', '.test-data',
            'accounts.json', 'totp-steps.json', 'login-attempts.json', 'tinyproxy-activation.json', 'cluster-maintenance.json', 'cluster-protection.json', 'enroll-*.json', 'node_modules',
            '*.key', '*.pem', '*.crt', '*.p12', '*.pfx', 'authkey', 'proxmox-fencing.json', 'cluster-plan.json', 'cluster-bootstrap.json',
            'cluster-plan-web-request.json', 'qdevice-ready.json', 'fencing-tested.json',
            'fencing-off-observed.json', 'preflight-current.json', 'nssdb', 'proxypermit-cluster-bootstrap',
            '*.log', '*.log.*', '*.jsonl', '*.journal*', 'ha-*.json',
            'session-state.json', 'nonce-store.json', 'nonces.json', 'whitelist_entry_log.json', 'releases', 'backups', 'current', 'dist', '.cache', '.agents', '.codex')
        if managed or HA_RETIRE_UNIT.is_file():
            for directory in (base, *base.parents, release.parent):
                require_trusted_path(directory, directory=True)
            with tempfile.TemporaryDirectory(prefix='.release-', dir=release.parent) as directory:
                staged = Path(directory) / release.name
                shutil.copytree(source, staged, ignore=ignore)
                prepare_release_permissions(staged)
                secure_ha_helper_source(base, staged)
                staged.rename(release)
        else:
            shutil.copytree(source, release, ignore=ignore)
            prepare_release_permissions(release)
        run(sys.executable, '-m', 'venv', str(release / '.venv'), umask=0o022)
        python = str(release / '.venv/bin/python')
        requirements = 'requirements/granian.txt' if plan['server'] == 'granian' else 'requirements/base.txt'
        run(python, '-m', 'pip', 'install', '-r', str(release / requirements), umask=0o022)
        test_env = dict(os.environ, FLASK_SECRET_KEY=secrets.token_hex(32), ENABLE_AUTH='false',
                        DEMO_MODE='true', DEPLOYMENT_MODE='standalone',
                        WHITELIST_PATH=str(release / '.test-data/filter'),
                        SESSION_STATE_FILE=str(release / '.test-data/session.json'),
                        AUDIT_LOG_PATH=str(release / '.test-data/audit.log'),
                        TINYPROXY_CONFIG_PATH=str(release / '.test-data/tinyproxy.conf'))
        (release / '.test-data').mkdir()
        run(python, 'scripts/dev/test.py', cwd=release, env=test_env)
        if subprocess.run(['id', '-u', 'proxypermit'], stdout=subprocess.DEVNULL, check=False).returncode:
            run('useradd', '--system', '--home-dir', '/var/lib/proxypermit', '--shell', '/usr/sbin/nologin', 'proxypermit')
        run('usermod', '-a', '-G', 'adm,systemd-journal', 'proxypermit')
        uid, gid, chown = service_ownership()
        for directory in ('/etc/proxypermit', '/var/lib/proxypermit', '/var/log/proxypermit'):
            path = Path(directory)
            path.mkdir(exist_ok=True, mode=0o750)
            if managed:
                chown(path, 0 if directory == '/etc/proxypermit' else uid, gid)
                os.chmod(path, 0o750)
        legacy = Path(plan['existingEnv']).read_text(encoding='utf-8') if plan['existingEnv'] else ''
        values = env_values(Path(plan['existingEnv'])) if plan['existingEnv'] else {}
        additions = {'DEPLOYMENT_MODE': plan['mode'], 'TINYPROXY_CONFIG_PATH': str(config_path), 'APP_SERVER': plan['server']}
        # Older installer versions kept the fresh-install binding only inside
        # systemd. Persist that setting before replacing the unit on upgrade.
        old_unit = Path('/etc/systemd/system/proxypermit.service')
        if old_unit.exists():
            for key in ('GUNICORN_BIND', 'GUNICORN_USE_TLS'):
                match = re.search(r'^Environment=' + key + r'=([^\r\n]+)$', old_unit.read_text(), re.M)
                if match and key not in values:
                    additions[key] = match[1]
        if not legacy:
            result = run(python, '-c', 'import sys; from werkzeug.security import generate_password_hash; print(generate_password_hash(sys.stdin.read()))',
                         input=password, text=True, capture_output=True)
            additions.update(ENABLE_AUTH='true', FLASK_SECRET_KEY=secrets.token_hex(32),
                             WHITELIST_PATH=str(filter_path), TINYPROXY_SERVICE='tinyproxy.service',
                             MANAGER_BIND=args.manager_bind, MANAGER_PORT=str(args.manager_port),
                             GUNICORN_BIND=f'{args.manager_bind}:{args.manager_port}', GUNICORN_USE_TLS='false',
                             AUDIT_LOG_PATH='/var/log/proxypermit/audit.log',
                             WHITELIST_ENTRY_LOG_PATH='/var/lib/proxypermit/entries.json',
                             SESSION_STATE_FILE='/var/lib/proxypermit/session-state.json',
                             SERVICE_CONTROL_SUDO='true')
            if not accounts_path.exists():
                account = {args.admin_user: {'password_hash': result.stdout.strip(), 'revision': secrets.token_hex(16)}}
                atomic_write(accounts_path, json.dumps(account), 0o640)
            chown(accounts_path, 0, gid)
            os.chmod(accounts_path, 0o640)
            additions['AUTH_ACCOUNTS_FILE'] = str(accounts_path)
            if direct_tls:
                updates, tls_profile, fingerprint = management_tls.prepare_installation(args, transaction, current, gid)
                additions.update(updates)
        elif managed:
            additions['SERVICE_CONTROL_SUDO'] = 'true'
            if migrate_accounts:
                atomic_write(accounts_path, env_path.with_name('accounts.json').read_text(encoding='utf-8'))
                chown(accounts_path, uid, gid)
                additions['AUTH_ACCOUNTS_FILE'] = str(accounts_path)
            if not values.get('AUDIT_LOG_PATH', '').startswith('/'):
                additions['AUDIT_LOG_PATH'] = '/var/log/proxypermit/audit.log'
                old_audit = Path(plan['existingEnv']).parent / values.get('AUDIT_LOG_PATH', 'audit.log')
                new_audit = Path(additions['AUDIT_LOG_PATH'])
                if old_audit.is_file() and not new_audit.exists():
                    transaction.track(new_audit)
                    shutil.copy2(old_audit, new_audit)
                    chown(new_audit, uid, gid)
                    os.chmod(new_audit, 0o640)
        if ha:
            additions.update(PROXYPERMIT_MANAGED_HA='true', NODE0_ADDRESS=ha['nodeA'],
                             NODE1_ADDRESS=ha['nodeB'], VIP_ADDRESS=ha['vip'].split('/')[0],
                             PROXYPERMIT_HA_NODE=ha['node'], PROXYPERMIT_HA_INTERFACE=ha['interface'],
                             HA_CLIENT_CIDR=ha['clients'], HA_VRID=str(ha['vrid']),
                             HA_VIP_PREFIX=ha['vip'].split('/')[1])
        elif plan.get('managedHa') and HA_FIREWALL_STATE.is_file():
            topology = json.loads(HA_FIREWALL_STATE.read_text())['topology']
            additions.update(PROXYPERMIT_HA_INTERFACE=topology['interface'],
                             HA_CLIENT_CIDR=topology['clients'], HA_VRID=str(topology['vrid']),
                             HA_VIP_PREFIX=topology['vip'].split('/')[1])
        if managed:
            for key, default in (('AUDIT_LOG_RETENTION_DAYS', '30'), ('LOG_RETENTION_DAYS', '30'),
                                 ('PROXY_TRAFFIC_RETENTION_DAYS', '7'), ('LOG_IP_MODE', 'masked'),
                                 ('AUDIT_ENTRY_MODE', 'hash'), ('HTTP_ACCESS_LOGS', 'false')):
                if key not in values:
                    additions[key] = default
            additions['PROXYPERMIT_MANAGED_LOGGING'] = 'true'
            audit_path = Path(additions.get('AUDIT_LOG_PATH', values.get('AUDIT_LOG_PATH', '/var/log/proxypermit/audit.log')))
            if audit_path.parent != Path('/var/log/proxypermit'):
                raise ValueError('Managed audit retention requires AUDIT_LOG_PATH under /var/log/proxypermit.')
        # Preserve every legacy line and append only explicit overrides.
        env_content = legacy.rstrip() + '\n\n# ProxyPermit installer migration\n'
        for key, value in additions.items():
            env_content += f'{key}={value}\n'
        atomic_write(env_path, env_content)
        if managed:
            chown(env_path, 0, gid)
            os.chmod(env_path, 0o640)
        if not config_existed:
            atomic_write(config_path, tinyproxy_text(filter_path, ha), 0o640)
        if not filter_path.exists():
            atomic_write(filter_path, '# Add allowed domains in ProxyPermit.\n', 0o644)
        if managed and not release_rollback:
            from scripts.deploy import access_safety
            if access_safety.requested(args) or access_safety.PROFILE.exists():
                touched_service = True
                if plan['action'] == 'upgrade':
                    run('systemctl', 'stop', 'proxypermit.service')
                if plan.get('managedHa') and auxiliary.get('proxypermit-ha-policy.timer', {}).get('active'):
                    run('systemctl', 'stop', 'proxypermit-ha-policy.timer')
                touched_ufw = True
                if access_safety.configure(SimpleNamespace(run=run, require_trusted_path=require_trusted_path, chown=chown, UFW_RULE_FILES=UFW_RULE_FILES), transaction, config_path, filter_path, args, values, gid):
                    service_snapshot['ufw'] = True
                    atomic_write(transaction.directory / 'service-state.json', json.dumps(service_snapshot))
        if managed:
            # Parent write permission is needed for atomic replacement. Do not
            # recursively change arbitrary administrator-owned subdirectories.
            chown(config_path.parent, 0, gid)
            os.chmod(config_path.parent, 0o755 if frozen else 0o775)
            chown(config_path, 0, gid)
            os.chmod(config_path, 0o640)
            if values.get('TINYPROXY_RELOAD_ENABLED', 'false').lower() == 'true':
                configure_reload_access(config_path, filter_path)
            backup_dir = filter_path.parent / '.backups'
            prepare_filter_backups(backup_dir, transaction, uid, gid, chown)
            native_backups = config_path.parent / '.proxypermit-config-backups'
            validate_native_backups(native_backups, uid)
            for locked_path in (Path(str(config_path) + '.lock'), Path(str(filter_path) + '.lock')):
                if locked_path.is_file():
                    transaction.track(locked_path)
                    chown(locked_path, uid, gid)
            for filename in (values.get('WHITELIST_ENTRY_LOG_PATH'), values.get('SESSION_STATE_FILE')):
                if filename and Path(filename).is_file():
                    chown(filename, uid, gid)
            service = values.get('TINYPROXY_SERVICE', 'tinyproxy.service')
            if not re.fullmatch(r'tinyproxy(?:\.service)?', service):
                raise ValueError('A custom TINYPROXY_SERVICE requires a dedicated sudoers rule.')
            atomic_write('/etc/sudoers.d/proxypermit', f'proxypermit ALL=(root) NOPASSWD: /usr/bin/systemctl restart {service}, /usr/bin/systemctl kill --kill-whom=main --signal=SIGUSR1 tinyproxy.service\n', 0o440)
            run('visudo', '-cf', '/etc/sudoers.d/proxypermit')
        if ha:
            atomic_write(KEEPALIVED_HEALTH, health_helper_text(config_path), 0o755)
            atomic_write(KEEPALIVED_CONFIG, keepalived_text(ha), 0o640)
            run('keepalived', '--config-test', '-f', str(KEEPALIVED_CONFIG))
            atomic_write(HA_RETIRE_UNIT, ha_retire_unit_text(str(base)), 0o644)
            atomic_write(HA_RETIRE_SUDOERS, HA_RETIRE_RULE, 0o440)
            run('visudo', '-cf', str(HA_RETIRE_SUDOERS))
        elif plan['action'] == 'upgrade' and plan.get('managedHa') and not cluster_upgrade:
            topology = ha_topology(legacy_values['PROXYPERMIT_HA_NODE'], legacy_values['PROXYPERMIT_HA_INTERFACE'],
                legacy_values['NODE0_ADDRESS'], legacy_values['NODE1_ADDRESS'],
                legacy_values['VIP_ADDRESS'] + '/' + legacy_values['HA_VIP_PREFIX'],
                legacy_values['HA_CLIENT_CIDR'], int(legacy_values['HA_VRID']))
            require_trusted_path(KEEPALIVED_CONFIG, directory=False)
            current_health_config = KEEPALIVED_CONFIG.read_text(encoding='utf-8')
            expected_health_config = keepalived_text(topology).replace('    user proxypermit\n', '').replace('    timeout 2\n', '    timeout 1\n')
            if current_health_config == expected_health_config:
                transaction.track(KEEPALIVED_CONFIG)
                if upgrade_health_helper(transaction, content=health_helper_text(config_path)):
                    atomic_write(KEEPALIVED_CONFIG, keepalived_text(topology), KEEPALIVED_CONFIG.stat().st_mode & 0o777)
                    run('keepalived', '--config-test', '-f', str(KEEPALIVED_CONFIG))
        if managed:
            atomic_write(HA_SETUP_UNIT, ha_setup_unit_text(str(base)), 0o644)
            atomic_write(HA_SETUP_SUDOERS, HA_SETUP_RULE, 0o440)
            run('visudo', '-cf', str(HA_SETUP_SUDOERS))
            from scripts.deploy import cluster_web, management_tls_web
            helpers = [management_tls_web]
            if (source / 'scripts/deploy/cluster_web.py').is_file():
                helpers.append(cluster_web)
            for helper in helpers:
                for path, content in helper.assets(base).items():
                    transaction.track(path)
                    if path.is_symlink() or (path.exists() and path.read_text(encoding='utf-8') != content
                            and not path.read_text(encoding='utf-8').startswith(helper.MARKER)):
                        raise ValueError(f'An existing web control asset requires OS review: {path}')
                    atomic_write(path, content, 0o440 if path == helper.SUDOERS else 0o644)
                run('visudo', '-cf', str(helper.SUDOERS))
        # Load all migrated values via the same dotenv parser as the application,
        # and import as the service user before changing the running service.
        smoke = 'from dotenv import load_dotenv; load_dotenv("/etc/proxypermit/.env", override=True); import wsgi; import os; from app.runtime.server import build_command; build_command(os.getenv("APP_SERVER", "gunicorn"), os.environ)'
        smoke_command = [python, '-c', smoke]
        if managed:
            smoke_command = ['runuser', '-u', 'proxypermit', '--'] + smoke_command
        run(*smoke_command, cwd=release)
        if cluster_upgrade and not release_rollback:
            from scripts.deploy.ha_resource import AGENT_PATH
            agent_source = 'scripts/deploy/ha_resource.py'
            upgrade_cluster_agent(transaction, AGENT_PATH, current / agent_source, release / agent_source)
        atomic_write('/etc/systemd/system/proxypermit.service',
                     service_text(current, 'standalone' if managed else 'ha', args.manager_bind, args.manager_port,
                                  preserve_binding=plan['action'] == 'upgrade', tls=direct_tls), 0o644)
        temporary_link = base / '.current-new'
        if temporary_link.is_symlink():
            temporary_link.unlink()
        temporary_link.symlink_to(release, target_is_directory=True)
        os.replace(temporary_link, current)
        for path, content in log_assets.items():
            write_logging_asset(path, content)
        for path, content in cluster_assets.items():
            atomic_write(path, content, 0o644)
        if managed:
            write_logging_asset(policy_recovery.DROP_IN, policy_recovery.drop_in(base))
        run('systemctl', 'daemon-reload')
        if managed:
            for namespace in ('proxypermit', 'proxypermit-traffic'):
                run('systemctl', 'restart', 'systemd-journald@' + namespace + '.service')
        touched_service = True
        run('systemctl', 'enable', 'proxypermit.service')
        run('systemctl', 'restart', 'proxypermit.service')
        effective_values = env_values(env_path)
        def check_manager():
            if tls_profile:
                return management_tls.probe(effective_values, tls_profile)
            return probe_manager(effective_values, fingerprint) if fingerprint else probe_manager(effective_values)
        if not wait_manager(check_manager):
            raise RuntimeError('The web service did not become available. Restoring the previous configuration.')
        if tls_profile:
            run('systemctl', 'enable', '--now', 'proxypermit-management-tls-renew.timer')
        if ha:
            touched_ufw = True
            owned_rules = apply_ha_firewall(plan['firewallRules'])
            atomic_write(HA_FIREWALL_STATE, json.dumps({'topology': ha, 'rules': owned_rules}))
            service_snapshot['ufwRuleHashes'] = ufw_rule_fingerprints()
            atomic_write(transaction.directory / 'service-state.json', json.dumps(service_snapshot))
        if managed:
            for timer in ('proxypermit-audit-retention.timer', 'proxypermit-journal-retention.timer', 'proxypermit-cluster-status.timer'):
                run('systemctl', 'enable', '--now', timer)
            run('systemctl', 'start', 'proxypermit-audit-retention.service', 'proxypermit-journal-retention.service')
            # Restart locally to attach the selected journal namespace. On HA,
            # Keepalived lets the peer serve traffic while Tinyproxy recovers.
            if plan['action'] == 'upgrade':
                run('systemctl', 'restart', 'tinyproxy.service')
                if plan.get('managedHa'):
                    if not cluster_upgrade:
                        run('systemctl', 'restart', 'keepalived.service')
                    if auxiliary['proxypermit-ha-tls.service']['active']:
                        run('systemctl', 'restart', 'proxypermit-ha-tls.service')
        # Fresh deployments also need the package service enabled.
        if plan['action'] == 'install':
            run('systemctl', 'enable', 'tinyproxy.service')
            # apt may already have started the package default configuration.
            run('systemctl', 'restart', 'tinyproxy.service')
            run('systemctl', 'is-active', '--quiet', 'tinyproxy.service')
        if plan.get('managedHa') and auxiliary.get('proxypermit-ha-policy.timer', {}).get('active'):
            run('systemctl', 'start', 'proxypermit-ha-policy.timer')
        if ha:
            start_ha()
        if update_legacy_sync:
            atomic_write(LEGACY_SYNC_SCRIPT, (release / 'legacy/sync_whitelist_v2.py').read_text(encoding='utf-8'), 0o755)
        if activate_ufw:
            if ufw_enabled():
                raise ValueError('UFW was enabled while installation was in progress. Review the firewall before retrying.')
            starting_ufw = True
            run('ufw', '--force', 'enable')
            if not ufw_enabled():
                raise RuntimeError('UFW could not be enabled.')
            service_snapshot['enabledUfwConfigHash'] = hashlib.sha256(UFW_CONFIG.read_bytes()).hexdigest()
        atomic_write(base / 'installed.json', json.dumps(dict(release=str(release), mode=plan['mode'], backup=str(transaction.directory)), indent=2))
        if touched_ufw:
            service_snapshot['ufw'] = True
            service_snapshot['ufwRuleHashes'] = ufw_rule_fingerprints()
            atomic_write(transaction.directory / 'service-state.json', json.dumps(service_snapshot))
        if release_rollback:
            print('Previous application release restored. The existing management listener was preserved.')
        elif plan['action'] == 'upgrade':
            print('Upgrade complete. The existing management listener was preserved.')
        else:
            scheme = 'https' if direct_tls else 'http'
            print(f'Installation complete. Management console listener: {scheme}://127.0.0.1:{args.manager_port}/')
            if tls_profile:
                print(f'Trust {management_tls.FOLDER / "ca.pem"} on each client after verifying its SHA-256 fingerprint: {tls_profile["caFingerprint"]}')
            elif direct_tls:
                print('Connect using a hostname or IP listed on the server certificate.')
        if ha:
            print(f"HA node {ha['node'].upper()} is configured. Shared proxy address: {ha['vip'].split('/')[0]}:3128.")
        print(f'Backup and recovery manifest: {transaction.directory}')
    except (Exception, KeyboardInterrupt):
        try:
            if starting_ufw:
                run('ufw', '--force', 'disable')
        finally:
            restore_failed_install(transaction, auxiliary, previous, touched_service, touched_ufw)
        raise


def restore_failed_install(transaction, auxiliary, previous, touched_service, touched_ufw):
    if touched_service:
        subprocess.run(['systemctl', 'stop', 'proxypermit.service'], check=False)
    for unit in auxiliary:
        subprocess.run(['systemctl', 'stop', unit], check=False)
    transaction.restore()
    if touched_ufw:
        reload_ufw()
    if touched_service:
        run('systemctl', 'daemon-reload')
        subprocess.run(['systemctl', 'restart' if previous['active'] else 'stop', 'proxypermit.service'], check=False)
        if not previous['enabled']:
            subprocess.run(['systemctl', 'disable', 'proxypermit.service'], check=False)
    restore_auxiliary_services(auxiliary)


def installed_deployment(root, install_dir):
    root = Path(root).resolve()
    base = root / install_dir.lstrip('/')
    marker = base / 'installed.json'
    current = base / 'current'
    unit = root / 'etc/systemd/system/proxypermit.service'
    if base.resolve() != base or (base / 'backups').is_symlink():
        raise ValueError('The installation and backup paths must not pass through symlinks.')
    if marker.is_symlink() or unit.is_symlink() or not marker.is_file() or not unit.is_file() or not current.is_symlink():
        raise ValueError('No installer-managed ProxyPermit service found at the selected installation path.')
    installed = json.loads(marker.read_text(encoding='utf-8'))
    release = installed.get('release') if isinstance(installed, dict) else None
    release_root = Path(install_dir) / 'releases'
    if (not isinstance(release, str) or Path(release).parent != release_root
            or os.readlink(current) != release
            or f'WorkingDirectory={Path(install_dir).as_posix()}/current' not in unit.read_text(encoding='utf-8').splitlines()):
        raise ValueError('The installed release, current link, and systemd unit do not match.')
    return installed, unit, current, marker


def build_uninstall_plan(root, install_dir, *, purge=False):
    installed, unit, current, marker = installed_deployment(root, install_dir)
    paths = [unit, current, marker]
    recovery = Path(root).resolve() / policy_recovery.DROP_IN.as_posix().lstrip('/')
    if not recovery.is_symlink() and recovery.is_file() and recovery.read_text(encoding='utf-8') == policy_recovery.drop_in(Path(install_dir)):
        paths.append(recovery)
    sudoers = Path(root).resolve() / 'etc/sudoers.d/proxypermit'
    if not sudoers.is_symlink() and sudoers.is_file() and sudoers.read_text(encoding='utf-8') == (
        'proxypermit ALL=(root) NOPASSWD: /usr/bin/systemctl restart tinyproxy.service, /usr/bin/systemctl kill --kill-whom=main --signal=SIGUSR1 tinyproxy.service\n'
    ):
        paths.append(sudoers)
    for path, content in ((HA_RETIRE_UNIT, ha_retire_unit_text(install_dir)), (HA_RETIRE_SUDOERS, HA_RETIRE_RULE),
                          (HA_SETUP_UNIT, ha_setup_unit_text(install_dir)), (HA_SETUP_SUDOERS, HA_SETUP_RULE),
                          (HA_CERT_RECOVERY_UNIT, ha_certificate_recovery_unit_text(install_dir))):
        target = Path(root).resolve() / path.as_posix().lstrip('/')
        if not target.is_symlink() and target.is_file() and target.read_text(encoding='utf-8') == content:
            paths.append(target)
    for path, content in cluster_status_assets(Path(install_dir) / 'current').items():
        target = Path(root).resolve() / path.as_posix().lstrip('/')
        if not target.is_symlink() and target.is_file() and target.read_text(encoding='utf-8') == content:
            paths.append(target)
    from scripts.deploy.management_tls import assets as management_tls_assets
    for path, content in management_tls_assets(Path(install_dir) / 'current').items():
        target = Path(root).resolve() / path.as_posix().lstrip('/')
        if not target.is_symlink() and target.is_file() and target.read_text(encoding='utf-8') == content:
            paths.append(target)
    from scripts.deploy import cluster_web, management_tls_web
    for helper in (management_tls_web, cluster_web):
        for path, content in helper.assets(Path(install_dir)).items():
            target = Path(root).resolve() / path.as_posix().lstrip('/')
            if not target.is_symlink() and target.is_file() and target.read_text(encoding='utf-8') == content:
                paths.append(target)
    plan = {
        'action': 'uninstall', 'mode': installed.get('mode'), 'installDir': install_dir,
        'service': 'proxypermit.service', 'paths': [str(path) for path in paths],
        'preserved': ['/etc/proxypermit', '/var/lib/proxypermit', '/var/log/proxypermit',
                      f'{install_dir}/releases', f'{install_dir}/backups',
                      'SSH configuration and firewall rules',
                      'Tinyproxy, Keepalived, fencing agents, legacy sync, system packages, and the service account'],
    }
    if purge:
        root = Path(root).resolve()
        for path in logging_assets(Path(install_dir) / 'current', {'DEPLOYMENT_MODE': 'ha'}):
            target = root / path.as_posix().lstrip('/')
            if not target.is_symlink() and target.is_file() and target.read_text(encoding='utf-8').startswith(LOGGING_MARKER):
                paths.append(target)
        config = root / 'etc/proxypermit'
        if installed.get('mode') == 'ha':
            from scripts.deploy import ha_setup
            for path in (ha_setup.TLS_SERVICE, ha_setup.POLICY_SERVICE, ha_setup.POLICY_TIMER):
                unit_path = root / path.as_posix().lstrip('/')
                if unit_path.is_symlink() or not unit_path.is_file():
                    continue
                if path == ha_setup.TLS_SERVICE:
                    try:
                        address = str(ipaddress.IPv4Address(env_values(config / '.env')['NODE0_ADDRESS']))
                    except (KeyError, ValueError):
                        continue
                    expected = ha_setup.tls_unit(Path(install_dir), address)
                else:
                    expected = (SOURCE / 'deploy/systemd' / path.name).read_text(encoding='utf-8')
                    expected = expected.replace('/opt/proxypermit/', f'{install_dir}/')
                if unit_path.read_text(encoding='utf-8') == expected:
                    paths.append(unit_path)
        local_config = [config / name for name in ('.env', 'accounts.json', 'management-tls', 'ha-tls',
                        'management-tls-web.json', 'ha-certificate-transition.json', 'ha-retired.json', 'sync-secret.key')]
        data = [*local_config, root / 'var/lib/proxypermit', root / 'var/log/proxypermit',
                marker.parent / 'releases', marker.parent / 'backups']
        machine_id = root / 'etc/machine-id'
        if machine_id.is_file() and not machine_id.is_symlink():
            value = machine_id.read_text(encoding='ascii').strip()
            if re.fullmatch(r'[0-9a-f]{32}', value):
                data.extend(root / store / f'{value}.{namespace}' for store in ('var/log/journal', 'run/log/journal')
                            for namespace in ('proxypermit', 'proxypermit-traffic'))
        plan.update(paths=list(map(str, paths)), purgePaths=list(map(str, data)), preserved=[
            'Pinned management access, SSH and UFW configuration',
            'Tinyproxy rules and configuration, OS packages, service account and fencing setup',
            'Operator-managed files and external backups or logs',
        ])
    return plan


def verify_live_manager(install_dir):
    properties = run('systemctl', 'show', '--property=FragmentPath', '--property=WorkingDirectory',
                     'proxypermit.service', capture_output=True, text=True).stdout.splitlines()
    if ('FragmentPath=/etc/systemd/system/proxypermit.service' not in properties
            or f'WorkingDirectory={Path(install_dir).as_posix()}/current' not in properties):
        raise ValueError('systemd is using a different unit file or working directory for ProxyPermit.')


def uninstall(install_dir, *, detached=False, purge=False):
    require_linux_root('Uninstall')
    with maintenance_lock():
        if detached:
            require_retired_cluster()
        else:
            cluster.require_independent_control('uninstalling this node')
        plan = build_uninstall_plan('/', install_dir, purge=True) if purge else build_uninstall_plan('/', install_dir)
        def verify_policy_recovered():
            if str(policy_recovery.DROP_IN) in plan['paths']:
                require_policy_recovered(*installed_policy_paths(), 'uninstalling ProxyPermit')

        verify_policy_recovered()
        if purge and plan['mode'] == 'ha':
            require_ha_purge_ready()
        purge_paths = validate_purge_paths(plan['purgePaths']) if purge else []
        unit = plan['service']
        verify_live_manager(install_dir)
        previous: dict[str, Any] = service_state(unit)
        if str(HA_CERT_RECOVERY_UNIT) in plan['paths']:
            previous['auxiliary'] = {HA_CERT_RECOVERY_UNIT.name: service_state(HA_CERT_RECOVERY_UNIT.name)}
        for name in ('proxypermit-cluster-status.timer', 'proxypermit-cluster-status.service', cluster_setup.WEB_UNIT):
            if str(Path('/etc/systemd/system') / name) in plan['paths']:
                previous.setdefault('auxiliary', {})[name] = service_state(name)
        for name in ('proxypermit-management-tls-renew.timer', 'proxypermit-management-tls-renew.service',
                     'proxypermit-management-tls-recover.service', 'proxypermit-management-tls-web.service'):
            if str(Path('/etc/systemd/system') / name) in plan['paths']:
                previous.setdefault('auxiliary', {})[name] = service_state(name)
        if purge:
            for kind in ('audit', 'journal'):
                for suffix in ('timer', 'service'):
                    name = f'proxypermit-{kind}-retention.{suffix}'
                    if str(Path('/etc/systemd/system') / name) in plan['paths']:
                        previous.setdefault('auxiliary', {})[name] = service_state(name)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        transaction = Transaction(Path(install_dir) / 'backups' / stamp)
        for path in plan['paths']:
            transaction.track(path)
        atomic_write(transaction.directory / 'service-state.json', json.dumps(previous))
        print(f'Recovery backup: {stamp}', flush=True)
        try:
            for auxiliary in previous.get('auxiliary', {}):
                run('systemctl', 'disable', '--now', auxiliary)
            run('systemctl', 'disable', '--now', unit)
            # Stopping the web service can interrupt a publication that began
            # after the first check. Keep the recovery hook in that case.
            verify_policy_recovered()
            for path in plan['paths']:
                Path(path).unlink()
            run('systemctl', 'daemon-reload')
        except (Exception, KeyboardInterrupt):
            transaction.restore()
            run('systemctl', 'daemon-reload')
            restore_auxiliary_services({unit: previous, **previous.get('auxiliary', {})})
            raise
        print('ProxyPermit manager removed.')
        if purge:
            purge_data(purge_paths, plan)


def require_ha_purge_ready():
    units = ('keepalived.service', 'proxypermit-ha-policy.timer', 'proxypermit-ha-tls.service', 'whitelist-sync-v2.timer')
    jobs = ('proxypermit-ha-policy.service', 'whitelist-sync-v2.service')
    if (HA_FIREWALL_STATE.exists() or HA_FIREWALL_STATE.is_symlink()
            or any(any(service_state(unit).values()) for unit in units)
            or any(service_state(unit)['active'] for unit in jobs)):
        raise ValueError('Retire this HA node and stop its synchronization before purging local data.')


def validate_purge_paths(values):
    paths = [Path(value) for value in values]
    mount_points = {Path(re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), row.split(' ')[4]))
                    for row in Path('/proc/self/mountinfo').read_text(encoding='utf-8').splitlines()}
    for path in paths:
        if path.resolve() != path or any(path == mount or path in mount.parents for mount in mount_points):
            raise ValueError(f'Review symbolic links and mounted data before purging {path}.')
    return paths


def purge_data(paths, plan):
    try:
        # Restart only active external services that used the removed namespace.
        for service in ('tinyproxy', 'keepalived'):
            dropin = f'/etc/systemd/system/{service}.service.d/20-proxypermit-logging.conf'
            if dropin in plan['paths'] and service_state(service + '.service')['active']:
                run('systemctl', 'restart', service + '.service')
        for namespace in ('proxypermit', 'proxypermit-traffic'):
            run('systemctl', 'stop', f'systemd-journald@{namespace}.service', f'systemd-journald@{namespace}.socket')
        for path in paths:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        remaining = ', '.join(str(path) for path in paths if path.exists())
        raise ValueError(f'The manager was removed, but data cleanup stopped: {exc}. Review these paths: {remaining}') from exc
    print('ProxyPermit local data and recovery backups were removed.')


def detached_cluster():
    """Check persistent local isolation after the OS operator removes the member."""
    from scripts.deploy import ha_resource
    checkpoint = cluster_setup.protected(cluster.SETUP_CHECKPOINT, private=True)
    if (not isinstance(checkpoint, dict) or type(checkpoint.get('version')) is not int
            or checkpoint['version'] != 1 or checkpoint.get('phase') != 'active'
            or checkpoint.get('name') != socket.gethostname().split('.')[0]
            or not isinstance(checkpoint.get('planHash'), str) or not re.fullmatch(r'[0-9a-f]{64}', checkpoint['planHash'])):
        raise ValueError('Detached cleanup requires the active local native HA checkpoint.')
    for unit in ('pacemaker.service', 'corosync.service', 'corosync-qdevice.service', 'keepalived.service'):
        value = run('systemctl', 'show', unit, '--property=ActiveState,UnitFileState',
                    capture_output=True, text=True).stdout.splitlines()
        if 'ActiveState=inactive' not in value or 'UnitFileState=masked' not in value:
            raise ValueError('Detach the node through OS cluster administration and permanently mask its cluster services first.')
    profile = ha_resource.profile()
    if profile['address'] in local_ipv4_addresses():
        raise ValueError('The detached node still has the protected virtual IP.')
    return checkpoint, profile


def require_retirement_receipt(checkpoint):
    try:
        retired = cluster_setup.protected(HA_RETIRED_STATE, private=True)
    except FileNotFoundError as exc:
        raise ValueError('Complete detached HA retirement before uninstalling this node.') from exc
    if retired != {'version': 1, 'planHash': checkpoint['planHash']}:
        raise ValueError('Complete detached HA retirement before uninstalling this node.')


def require_retired_cluster(detachment=None):
    checkpoint, _ = detached_cluster() if detachment is None else detachment
    require_retirement_receipt(checkpoint)
    unit = run('systemctl', 'show', 'tinyproxy.service', '--property=ActiveState,UnitFileState',
               capture_output=True, text=True).stdout.splitlines()
    if (HA_FIREWALL_STATE.exists() or HA_FIREWALL_STATE.is_symlink() or 'ActiveState=inactive' not in unit
            or not {'UnitFileState=disabled', 'UnitFileState=masked', 'UnitFileState=static'}.intersection(unit)):
        raise ValueError('Complete detached HA retirement before uninstalling this node.')


def build_ha_retirement_plan(install_dir, *, detached=False) -> dict[str, Any]:
    detachment = detached_cluster() if detached else None
    if detachment is None:
        cluster.require_independent_control('retiring this node')
    installed, _, _, _ = installed_deployment('/', install_dir)
    if (detachment is not None and installed.get('mode') == 'ha'
            and not HA_FIREWALL_STATE.exists() and not HA_FIREWALL_STATE.is_symlink()):
        require_retired_cluster(detachment)
        return dict(action='retire-detached-ha-node', alreadyRetired=True,
                    planHash=detachment[0]['planHash'], services=[], ownedFirewallRules=[])
    if installed.get('mode') != 'ha' or HA_FIREWALL_STATE.is_symlink() or not HA_FIREWALL_STATE.is_file():
        raise ValueError('Retirement requires a managed HA node with a recorded firewall configuration.')
    require_trusted_path(HA_FIREWALL_STATE.parent, directory=True)
    require_trusted_path(HA_FIREWALL_STATE, directory=False)
    state = json.loads(HA_FIREWALL_STATE.read_text(encoding='utf-8'))
    ha = state['topology']
    topology = ha_topology(ha['node'], ha['interface'], ha['nodeA'], ha['nodeB'], ha['vip'], ha['clients'], ha['vrid'])
    if (KEEPALIVED_CONFIG.is_symlink() or not KEEPALIVED_CONFIG.is_file()
            or KEEPALIVED_CONFIG.read_text(encoding='utf-8') != keepalived_text(topology)):
        raise ValueError('Keepalived configuration differs from the managed HA configuration.')
    rules = state['rules']
    if not isinstance(rules, list) or any(not isinstance(rule, str) for rule in rules):
        raise ValueError('The managed HA firewall rule record is invalid.')
    services = ['keepalived.service', 'tinyproxy.service']
    for name in ('proxypermit-ha-tls.service', 'proxypermit-ha-policy.timer', HA_CERT_RECOVERY_UNIT.name):
        if Path('/etc/systemd/system', name).is_file():
            services.append(name)
    plan = {'action': 'retire-ha-node', 'vip': topology['vip'].split('/', 1)[0], 'peer': topology['peer'],
            'services': services, 'ownedFirewallRules': rules}
    if detachment is not None:
        checkpoint, profile = detachment
        if HA_RETIRED_STATE.exists() or HA_RETIRED_STATE.is_symlink():
            require_retirement_receipt(checkpoint)
        native_rules = checkpoint.get('ownedRules')
        if (profile['vip'] != topology['vip'] or profile['interface'] != topology['interface']
                or not isinstance(native_rules, list) or any(not isinstance(rule, str) for rule in native_rules)):
            raise ValueError('The native topology or recorded cluster firewall rules could not be matched.')
        plan.update(action='retire-detached-ha-node', planHash=checkpoint['planHash'],
                    services=services[1:], ownedFirewallRules=rules + native_rules)
    return plan


def local_ipv4_addresses():
    interfaces = json.loads(run('ip', '-j', '-4', 'address', 'show', capture_output=True, text=True).stdout)
    return {address['local'] for interface in interfaces for address in interface.get('addr_info', [])}


def verify_remote_proxy(vip, peer):
    local_addresses = local_ipv4_addresses()
    if vip in local_addresses or peer in local_addresses:
        raise ValueError('Move the virtual IP to the peer before retiring this node.')
    try:
        with socket.create_connection((vip, 3128), timeout=3):
            pass
    except OSError as exc:
        raise ValueError(f'The proxy listener on {vip}:3128 is unreachable. Retirement was stopped.') from exc


def retire_ha_node(install_dir, *, detached=False):
    require_linux_root('HA retirement')
    with maintenance_lock():
        plan = build_ha_retirement_plan(install_dir, detached=detached)
        if plan.get('alreadyRetired'):
            print('HA retirement was already completed on this node.')
            return
        verify_live_manager(install_dir)
        values = (manager_values(Path('/etc/proxypermit/.env'), Path('/etc/systemd/system/proxypermit.service'))
                  if detached else None)
        if values is not None and not probe_manager(values):
            raise ValueError('Verify direct access to the local management service before retiring the node.')
        verify_remote_proxy(plan['vip'], plan['peer'])
        base = Path(install_dir)
        for directory in (base, *base.parents, base / 'backups', UFW_RULE_FILES[0].parent):
            require_trusted_path(directory, directory=True)
        for path in UFW_RULE_FILES:
            require_trusted_path(path, directory=False)
        ufw_rule_fingerprints()
        previous = {unit: service_state(unit) for unit in plan['services']}
        snapshot: dict[str, Any] = dict(**service_state('proxypermit.service'), auxiliary=previous, ufw=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        transaction = Transaction(base / 'backups' / stamp)
        for path in (*UFW_RULE_FILES, HA_FIREWALL_STATE, *HA_PEER_STATE):
            transaction.track(path)
        if detached:
            transaction.track(HA_RETIRED_STATE)
        atomic_write(transaction.directory / 'service-state.json', json.dumps(snapshot))
        print(f'Recovery backup: {stamp}', flush=True)
        try:
            for unit in plan['services']:
                run('systemctl', 'disable', '--now', unit)
            verify_remote_proxy(plan['vip'], plan['peer'])
            remove_owned_ha_firewall(plan['ownedFirewallRules'], include_cluster=detached)
            verify_remote_proxy(plan['vip'], plan['peer'])
            if values is not None and not probe_manager(values):
                raise ValueError('The local management service could not be verified after retirement.')
            for path in HA_PEER_STATE:
                path.unlink(missing_ok=True)
            if HA_PEER_STATE[0].parent.is_dir():
                sync_directory(HA_PEER_STATE[0].parent)
            snapshot['ufwRuleHashes'] = ufw_rule_fingerprints()
            atomic_write(transaction.directory / 'service-state.json', json.dumps(snapshot))
            if detached:
                for path in UFW_RULE_FILES:
                    with path.open('r+b') as rules:
                        os.fsync(rules.fileno())
                sync_directory(UFW_RULE_FILES[0].parent)
                atomic_write(HA_RETIRED_STATE, json.dumps(dict(version=1, planHash=plan['planHash'])), 0o600)
            # The rule record remains available for a safe retry until all cleanup is durable.
            HA_FIREWALL_STATE.unlink()
            sync_directory(HA_FIREWALL_STATE.parent)
        except (Exception, KeyboardInterrupt):
            transaction.restore()
            reload_ufw()
            restore_auxiliary_services(previous)
            raise
        print('HA node retired. The management console remains available.')


def rollback_install(base, stamp, execute=False):
    if not re.fullmatch(r'[0-9]{8}T[0-9]{12}Z', stamp):
        raise ValueError('Use the timestamped backup name shown after installation.')
    directory = Path(base) / 'backups' / stamp
    entries = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    state = json.loads((directory / 'service-state.json').read_text(encoding='utf-8'))
    print(json.dumps(dict(action='rollback', backup=str(directory), paths=[item['path'] for item in entries]), indent=2))
    if not execute:
        return
    require_linux_root('Recovery')
    with maintenance_lock():
        cluster.require_independent_control('restoring an installation backup')
        policy = installed_policy_paths()
        operation = 'restoring an installation backup'
        require_policy_recovered(*policy, operation)
        from scripts.deploy.management_tls import rollback_values, rewrite_environment, write as tls_write, ENVIRONMENT as tls_environment
        tls_values = rollback_values(entries)
        if state.get('ufw') and state.get('ufwRuleHashes') != ufw_rule_fingerprints():
            raise ValueError('UFW rules changed since installation. Review the firewall backup before restoring.')
        enabled_ufw = state.get('enabledUfwConfigHash')
        if enabled_ufw and (not UFW_CONFIG.is_file() or UFW_CONFIG.is_symlink()
                            or enabled_ufw != hashlib.sha256(UFW_CONFIG.read_bytes()).hexdigest()):
            raise ValueError('UFW activation changed since installation. Review the firewall backup before restoring.')
        writers = ('proxypermit-ha-policy.timer', 'proxypermit-ha-policy.service', 'proxypermit.service')
        stopped = []
        try:
            for unit in writers:
                if subprocess.run(['systemctl', 'cat', unit], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, check=False).returncode == 0:
                    active = subprocess.run(['systemctl', 'is-active', unit], capture_output=True, text=True, check=False)
                    if active.stdout.strip() in ('active', 'activating', 'reloading', 'refreshing'):
                        stopped.append(unit)
                    run('systemctl', 'stop', unit)
            # A publisher interrupted during shutdown can leave a new checkpoint.
            require_policy_recovered(*policy, operation)
        except (Exception, KeyboardInterrupt):
            if stopped and subprocess.run(['systemctl', 'start', '--no-block', *reversed(stopped)], check=False).returncode:
                print('Could not requeue all previously running policy services. Check systemd status.', file=sys.stderr)
            raise
        auxiliary = state.get('auxiliary', {})
        for unit in auxiliary:
            if unit not in writers and subprocess.run(['systemctl', 'cat', unit], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, check=False).returncode == 0:
                run('systemctl', 'stop', unit)
        transaction = object.__new__(Transaction)
        transaction.directory, transaction.entries = directory, entries
        if enabled_ufw:
            run('ufw', '--force', 'disable')
        transaction.restore()
        if tls_values:
            tls_write(tls_environment, rewrite_environment(tls_environment.read_text(encoding='utf-8'), tls_values),
                      0o640, tls_environment.stat().st_gid)
        run('systemctl', 'daemon-reload')
        if state.get('ufw'):
            reload_ufw()
        restore_auxiliary_services(auxiliary)
        if state['active']:
            run('systemctl', 'start', 'proxypermit.service')
            values = manager_values(Path('/etc/proxypermit/.env'), Path('/etc/systemd/system/proxypermit.service'))
            if not wait_manager(lambda: probe_manager(values)):
                raise RuntimeError('Recovery finished, but the web service is unreachable at the restored address.')
        subprocess.run(['systemctl', 'enable' if state['enabled'] else 'disable', 'proxypermit.service'], check=False)
        print('Restored the previous configuration and release link. Installed packages were retained.')


def cluster_rollback_release(install_dir, stamp):
    if not re.fullmatch(r'[0-9]{8}T[0-9]{12}Z', stamp):
        raise ValueError('Use the timestamped backup name shown after the protected upgrade.')
    installed, _, _, marker = installed_deployment('/', install_dir)
    backup = marker.parent / 'backups' / stamp
    if installed.get('mode') != 'ha' or installed.get('backup') != str(backup):
        raise ValueError('Choose the backup for the currently installed protected upgrade.')
    for directory in (backup, backup.parent, marker.parent, *marker.parent.parents):
        require_trusted_path(directory, directory=True)
    try:
        record = cluster_setup.protected(backup / 'service-state.json', private=True)['protectedUpgrade']
        previous = Path(record['before'])
        successor = record['after']
    except (KeyError, TypeError) as exc:
        raise ValueError('This backup has no protected-upgrade record. Keep the node reserved for OS recovery.') from exc
    if (successor != installed['release'] or str(previous) == successor
            or previous.parent != marker.parent / 'releases'
            or not re.fullmatch(r'[0-9]{8}T[0-9]{12}Z', previous.name)):
        raise ValueError('The recorded protected rollback release does not match this deployment.')
    for directory in (previous.parent, previous, previous / 'scripts', previous / 'scripts/deploy'):
        require_trusted_path(directory, directory=True)
    require_trusted_path(previous / 'scripts/deploy/cluster_maintenance.py', directory=False)
    return previous


def wizard_ha(ui, args):
    node = ('a', 'b')[ui.choose('Which node are you installing?', ('Node A · preferred', 'Node B · standby'),
                                default=int(args.ha_node == 'b'))]
    try:
        inventory = [row for row in discover_network() if not row['dynamic']]
    except (OSError, ValueError, subprocess.SubprocessError):
        inventory = []
    suggestion = None
    if not args.ha_interface and inventory:
        options = [f"{row['interface']} · {row['address']}/{row['prefix']}" for row in inventory]
        choice = ui.choose('Use a detected local IPv4 address?', (*options, 'Enter network settings manually'))
        if choice < len(inventory):
            suggestion = inventory[choice]

    def interface_name(value):
        if not re.fullmatch(r'[A-Za-z0-9_.][A-Za-z0-9_.-]{0,14}', value):
            raise ValueError('Enter a Linux interface name such as ens18 or eth0.')
        return value

    interface = ui.ask('Network interface', args.ha_interface or (suggestion and suggestion['interface']), interface_name)
    previous = {'a': args.ha_node_a, 'b': args.ha_node_b}
    peer = 'b' if node == 'a' else 'a'

    def usable_address(value):
        address = ipaddress.IPv4Address(value)
        if address.is_loopback or address.is_multicast or address.is_unspecified or address.is_link_local:
            raise ValueError('Enter a usable static IPv4 address.')
        return str(address)

    local_ip = ui.ask(f'IPv4 address of this host (node {node.upper()})',
                      previous.get(node) or (suggestion and suggestion['address']), usable_address,
                      help_text='Use a static address on the interface selected above.')

    def peer_address(value):
        address = usable_address(value)
        if address == local_ip:
            raise ValueError('The other node must have a different IPv4 address.')
        return address

    peer_ip = ui.ask(f'IPv4 address of the other host (node {peer.upper()})', previous.get(peer), peer_address)
    addresses = {node: local_ip, peer: peer_ip}

    def virtual_address(value):
        if '/' not in value:
            raise ValueError('Include the subnet prefix, for example 192.0.2.10/24.')
        vip = ipaddress.IPv4Interface(value)
        ha_topology(node, interface, addresses['a'], addresses['b'], str(vip), str(vip.network), 51)
        if (suggestion and interface == suggestion['interface'] and local_ip == suggestion['address']
                and vip.network != ipaddress.IPv4Network(suggestion['network'])):
            raise ValueError(f"The virtual IP must use the selected interface's subnet: {suggestion['network']}.")
        return str(vip)

    vip = ui.ask('Virtual IPv4 address and prefix', args.ha_vip, virtual_address,
                 help_text='Use a free address on the same subnet as both nodes. Reserve it outside DHCP.')

    def client_network(value):
        if '/' not in value:
            raise ValueError('Include the prefix, for example 192.0.2.0/24 or 192.0.2.15/32.')
        allowed = ipaddress.IPv4Network(value)
        if not allowed.prefixlen:
            raise ValueError('Choose a specific client network.')
        return str(allowed)

    clients = ui.ask('Allowed IPv4 client network', args.ha_client_cidr, client_network,
                     help_text='Enter the network that may use the proxy on the virtual IP.')

    def router_id(value):
        if not value.isascii() or not value.isdecimal() or len(value) > 3 or not 1 <= int(value) <= 255:
            raise ValueError('Enter a VRRP ID between 1 and 255.')
        return int(value)

    vrid = ui.ask('VRRP ID', str(args.ha_vrid or 51), router_id,
                  help_text='Use the same ID on both nodes and an ID unused by other clusters on this network.')
    args.ha_node, args.ha_interface = node, interface
    args.ha_node_a, args.ha_node_b, args.ha_vip = addresses['a'], addresses['b'], vip
    args.ha_client_cidr, args.ha_vrid = clients, vrid
    return ha_topology(node, interface, addresses['a'], addresses['b'], vip, clients, vrid)


def wizard_management(ui, args):
    from scripts.deploy.access_safety import network, port
    ssh = os.getenv('SSH_CONNECTION', '').split()
    previous_port = args.manager_port
    args.admin_user = ui.ask('Administrator username', args.admin_user, admin_username)
    previous_scheme = args.manager_tls in ('local-ca', 'custom')
    wizard_transport(ui, args)
    https = args.manager_tls in ('local-ca', 'custom')
    preferred = default_manager_port(args.manager_tls) if previous_scheme != https else previous_port
    suggested_port = next((candidate for candidate in range(preferred, min(preferred + 10, 65536))
                           if (not https or candidate != 8443) and console_port_available(candidate)), None)
    def check_port(value):
        number = available_console_port(value)
        if https and number == 8443:
            raise ValueError('Port 8443 is reserved for HA synchronization. Try port 8444.')
        return number

    args.manager_port = ui.ask('Local console port', str(suggested_port) if suggested_port else None, check_port,
                               help_text='The web console listens on 127.0.0.1. Use a tunnel or reverse proxy for remote access.')
    trusted = (args.management_cidr or [ssh[0] if len(ssh) == 4 else None])[0]
    args.management_cidr = [ui.ask('Trusted management IP or CIDR', trusted, network,
                                  help_text='Use the administrator IP as seen by this host, or a dedicated management subnet.')]
    ssh_port = (args.management_ssh_port or [ssh[3] if len(ssh) == 4 else 22])[0]
    args.management_ssh_port = [ui.ask('Host SSH port', str(ssh_port), port,
                                      help_text='Enter the port SSH actually listens on, even when using a tunnel.')]
    web = (args.management_web_port or [previous_port])[0]
    if web == previous_port:
        web = args.manager_port
    args.management_web_port = [ui.ask('Host port for web access', str(web), port,
                                      help_text='For a reverse proxy, enter its host-side port. For a tunnel, keep the local console port.')]


def default_manager_port(mode):
    return 8444 if mode in ('local-ca', 'custom') else 8080


def certificate_path(value):
    if not value.startswith('/') or '/' == value or any(ord(ch) < 32 for ch in value):
        raise ValueError('Enter an absolute path on the Linux host, starting with /.')
    return value


def wizard_transport(ui, args):
    https = ui.choose('Management console protocol', ('HTTP · for an SSH tunnel or reverse proxy',
                      'HTTPS · direct TLS on the local listener'), default=int(args.manager_tls != 'http'))
    if not https:
        args.manager_tls = 'http'
        args.manager_cert = args.manager_key = None
        args.manager_name = []
        return
    mode = ui.choose('HTTPS certificate', ('Local CA · self-signed trust root with automatic renewal',
                     'Own certificate · PEM chain and private key'), default=int(args.manager_tls == 'custom'))
    args.manager_tls = ('local-ca', 'custom')[mode]
    if mode == 0:
        from scripts.deploy.management_tls import installation_names

        def validate_hosts(value):
            hosts = value.split()
            installation_names(hosts)
            return hosts

        args.manager_name = ui.ask('Additional DNS names or IP addresses (space-separated)',
                                   ' '.join(args.manager_name) or 'localhost', validate_hosts,
                                   help_text='Localhost and loopback IPs are always included. Add the names clients actually use.')
        args.manager_cert = args.manager_key = None
    else:
        args.manager_name = []
        args.manager_cert = ui.ask('Certificate chain file (PEM)', args.manager_cert, certificate_path,
                                   help_text='Use an absolute path to a root-owned file. Put the server certificate first.')
        args.manager_key = ui.ask('Private key file (PEM)', args.manager_key, certificate_path,
                                  help_text='Use an unencrypted, root-owned file with permissions 0600.')


def plan_management(plan, args):
    mode = getattr(args, 'manager_tls', None)
    if plan['action'] != 'install':
        if mode or args.manager_cert or args.manager_key or args.manager_name:
            raise ValueError('Use the management HTTPS controls for an existing installation. Upgrades preserve its listener.')
        return
    if mode == 'custom':
        if not args.manager_cert or not args.manager_key:
            raise ValueError('Custom HTTPS requires --manager-cert and --manager-key.')
        certificate_path(args.manager_cert)
        certificate_path(args.manager_key)
    elif args.manager_cert or args.manager_key:
        raise ValueError('Certificate and key paths require --manager-tls custom.')
    if args.manager_name and mode != 'local-ca':
        raise ValueError('Additional certificate names require --manager-tls local-ca.')
    direct_tls = mode in ('local-ca', 'custom')
    if direct_tls and args.manager_port == 8443:
        raise ValueError('Port 8443 is reserved for HA synchronization. Use a different management port, such as 8444.')
    plan['management'] = {'scheme': 'https' if direct_tls else 'http', 'bind': args.manager_bind,
                          'port': args.manager_port, 'certificate': mode if direct_tls else None}
    if mode == 'local-ca':
        from scripts.deploy.management_tls import installation_names
        plan['management']['certificateNames'] = installation_names(args.manager_name)
        plan['steps'].append('Create a local CA and set up automatic management certificate renewal.')
        plan['notes'].append('Verify the printed CA fingerprint and trust that CA on each client. The leaf renews automatically.')
    elif mode == 'custom':
        plan['steps'].append('Validate and install a protected copy of the supplied certificate and key.')
        plan['notes'].append('Use a URL covered by your certificate and manage its renewal separately.')


def validate_ufw_activation(plan, args):
    if not getattr(args, 'enable_ufw', False):
        return
    if plan['action'] != 'install' or not args.management_cidr:
        raise ValueError('--enable-ufw requires a fresh installation and an explicit trusted management IP or CIDR.')
    ssh = os.getenv('SSH_CONNECTION', '').split()
    if len(ssh) == 4:
        source = ipaddress.ip_address(ssh[0])
        if (not any(source in ipaddress.ip_network(cidr) for cidr in args.management_cidr)
                or (args.management_ssh_port and int(ssh[3]) not in args.management_ssh_port)):
            raise ValueError('The trusted management network and SSH port must include your current SSH connection to enable UFW.')


def wizard_sudo(*arguments):
    if not shutil.which('sudo'):
        raise ValueError('sudo is unavailable. Sign in as root and run the wizard.')
    return subprocess.run(['sudo', '--', 'bash', str(SOURCE / 'install.sh'), *arguments], check=False).returncode


def run_wizard(args):
    ui = InstallTerminal()
    ui.heading('ProxyPermit / Installation')
    print('  Press Enter to accept a default. Press Ctrl+C to cancel.')
    try:
        detected = build_plan('/', args.install_dir)
    except PermissionError:
        if sys.platform != 'linux' or os.geteuid() == 0:  # pylint: disable=no-member
            raise
        print('  Administrator access is needed to read the existing installation.')
        return wizard_sudo()
    fresh = detected['action'] == 'install'
    ha = None
    if fresh:
        args.mode = ('standalone', 'ha')[ui.choose('Deployment topology', (
            'Standalone node · one server', 'Two-node HA · shared proxy address through Keepalived'))]
        if args.mode == 'ha':
            ha = wizard_ha(ui, args)
    else:
        args.mode = detected['mode']
        print(f'  Existing {args.mode} installation found. This wizard prepares an upgrade.')

    servers = ('gunicorn', 'granian')
    server_options = ('Gunicorn · default', 'Granian · Rust-based web server')
    args.server = servers[ui.choose('Web server', server_options, default=servers.index(detected['server']))]
    if fresh:
        wizard_management(ui, args)
        wizard_firewall(ui, args)
    options = ['Exit without applying', 'Apply plan', 'Change web server']
    if fresh:
        options.extend(('Edit management and firewall settings', 'Edit HA network settings' if ha else 'Change to two-node HA'))
    args.reviewed_action = detected['action']
    while True:
        plan = build_plan('/', args.install_dir, args.mode, args.server, ha)
        if plan['action'] != args.reviewed_action:
            raise ValueError('The installation changed. Run the wizard again and review the new plan.')
        plan_management(plan, args)
        ui.review(plan, args)
        action = ui.choose('What would you like to do?', options)
        if action == 0:
            print('  Cancelled before applying the plan.')
            return 0
        if action == 1:
            break
        if action == 2:
            args.server = servers[ui.choose('Web server', server_options, default=servers.index(args.server))]
        elif action == 3:
            wizard_management(ui, args)
            wizard_firewall(ui, args)
        else:
            args.mode = ('standalone', 'ha')[ui.choose('Deployment topology', ('Standalone node', 'Two-node HA'),
                                                      default=int(args.mode == 'ha'))]
            ha = wizard_ha(ui, args) if args.mode == 'ha' else None
            options[4] = 'Edit HA network settings' if ha else 'Change to two-node HA'

    if sys.platform != 'linux':
        raise ValueError('Applying the plan requires Linux. Run the wizard on Debian or Ubuntu.')
    validate_ufw_activation(plan, args)
    if os.geteuid() == 0:  # pylint: disable=no-member
        apply(plan, args)
        return 0
    command = ['--mode', args.mode, '--server', args.server, '--install-dir', args.install_dir,
               '--reviewed-action', args.reviewed_action, '--apply']
    if fresh:
        command.extend([f'--admin-user={args.admin_user}', '--manager-port', str(args.manager_port), '--manager-tls', args.manager_tls])
        if args.manager_tls == 'custom':
            command.extend(['--manager-cert', args.manager_cert, '--manager-key', args.manager_key])
        for name in args.manager_name:
            command.append(f'--manager-name={name}')
    for name in ('management_cidr', 'management_ssh_port', 'management_web_port'):
        for value in getattr(args, name, None) or []:
            command.extend(['--' + name.replace('_', '-'), str(value)])
    if args.enable_ufw:
        command.append('--enable-ufw')
    if ha:
        for name in ('node', 'interface', 'node_a', 'node_b', 'vip', 'client_cidr', 'vrid'):
            command.extend(['--ha-' + name.replace('_', '-'), str(getattr(args, 'ha_' + name))])
    print('  Requesting administrator access to apply the reviewed plan.', flush=True)
    return wizard_sudo(*command)


def wizard_firewall(ui, args):
    print('  Keep a console or recovery session available. Review existing firewall rules and any other services on this host.')
    while True:
        args.enable_ufw = ui.choose('UFW firewall', (
            'Keep current status · add ProxyPermit rules', 'Enable UFW after adding ProxyPermit rules'),
            default=int(args.enable_ufw)) == 1
        try:
            validate_ufw_activation({'action': 'install'}, args)
            return
        except ValueError as exc:
            print(f'  {exc}')
            print('  You can edit the management settings from the review screen.')
            args.enable_ufw = False


def admin_username(value):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', value):
        raise ValueError('Use 1–64 letters, digits, dots, underscores, or hyphens.')
    return value


def manager_port(value):
    if not value.isascii() or not value.isdecimal() or len(value) > 5 or not 1024 <= int(value) <= 65535:
        raise ValueError('Choose a management port between 1024 and 65535.')
    return int(value)


def console_port_available(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.bind(('127.0.0.1', port))
        except OSError:
            return False
    return True


def available_console_port(value):
    port = manager_port(value)
    if not console_port_available(port):
        raise ValueError(f'Port {port} is in use or unavailable on 127.0.0.1. Choose another console port.')
    return port


def main(argv=None):
    parser = argparse.ArgumentParser(prog='install.sh', description=__doc__)
    execution = parser.add_mutually_exclusive_group()
    execution.add_argument('--apply', action='store_true', help='Apply the installation plan.')
    execution.add_argument('--plan', action='store_true', help='Print the plan as JSON without interactive prompts.')
    parser.add_argument('--mode', choices=['standalone', 'ha'], default=None)
    parser.add_argument('--server', choices=['gunicorn', 'granian'], help='Choose a web server. Upgrades preserve APP_SERVER by default.')
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument('--rollback', metavar='BACKUP', help='Show the recovery plan for a backup. Add --apply to restore it.')
    operation.add_argument('--upgrade', action='store_true', help='Plan an upgrade of an installer-managed deployment from this source tree.')
    operation.add_argument('--uninstall', action='store_true', help='Plan removal of the ProxyPermit manager. Add --apply to remove it.')
    operation.add_argument('--retire-ha', action='store_true', help='Plan retirement of a standby HA node and its managed firewall rules.')
    operation.add_argument('--configure-ha', action='store_true', help='Apply the approved local HA setup request through the privileged helper.')
    parser.add_argument('--cluster-maintenance', action='store_true', help='Upgrade or roll back an OS-protected HA node under a cluster-wide reservation.')
    parser.add_argument('--detached-ha', action='store_true', help='Clean up a native HA node already detached and persistently masked by the OS administrator.')
    parser.add_argument('--root', default='/', help='Plan only: inspect an alternative system root.')
    parser.add_argument('--install-dir', default='/opt/proxypermit')
    parser.add_argument('--enable-ufw', action='store_true',
                        help='Enable UFW on a fresh installation after adding scoped rules. Requires --management-cidr.')
    parser.add_argument('--purge', action='store_true', help='With --uninstall: remove local ProxyPermit data and recovery backups.')
    parser.add_argument('--reviewed-action', choices=('install', 'upgrade'), help=argparse.SUPPRESS)
    parser.add_argument('--management-cidr', action='append', help='Trusted management network for root-pinned web, SSH and proxy anti-lockout rules; repeat for multiple networks.')
    parser.add_argument('--management-ssh-port', action='append', type=int, help='Actual host SSH port to protect; defaults to the installing SSH connection or 22.')
    parser.add_argument('--management-web-port', action='append', type=int, help='Actual host web-management port to protect; specify reverse-proxy or relay ports explicitly.')
    parser.add_argument('--admin-user', default='admin')
    parser.add_argument('--admin-password-file')
    parser.add_argument('--manager-bind', default='127.0.0.1', choices=['127.0.0.1'])
    parser.add_argument('--manager-port', type=int)
    parser.add_argument('--manager-tls', choices=('http', 'local-ca', 'custom'), help='Web console transport for a fresh install. Defaults to HTTP.')
    parser.add_argument('--manager-name', action='append', default=[], help='Additional DNS name or IP for the local CA certificate; repeat as needed.')
    parser.add_argument('--manager-cert', help='Absolute path to your root-owned PEM certificate chain.')
    parser.add_argument('--manager-key', help='Absolute path to your unencrypted root-only PEM private key.')
    cluster = parser.add_argument_group('New IPv4 HA cluster')
    cluster.add_argument('--ha-node', choices=['a', 'b'], help='Local node. Node A has the highest VRRP priority.')
    cluster.add_argument('--ha-interface', help='Local network interface for the virtual IP.')
    cluster.add_argument('--ha-node-a', help='Static IPv4 address of node A.')
    cluster.add_argument('--ha-node-b', help='Static IPv4 address of node B.')
    cluster.add_argument('--ha-vip', help='Available virtual IPv4 address with its subnet prefix.')
    cluster.add_argument('--ha-client-cidr', help='IPv4 client network allowed to use Tinyproxy.')
    cluster.add_argument('--ha-vrid', type=int, help='VRRP router ID (1–255), identical on both nodes.')
    argv = sys.argv[1:] if argv is None else argv
    args = parser.parse_args(argv)
    try:
        if args.manager_port is None:
            args.manager_port = default_manager_port(args.manager_tls)
        if ((args.manager_tls or args.manager_name or args.manager_cert or args.manager_key)
                and (args.upgrade or args.rollback or args.uninstall or args.retire_ha or args.configure_ha)):
            raise ValueError('Management TLS installation options require a fresh installation.')
        if args.purge and not args.uninstall:
            raise ValueError('--purge requires --uninstall.')
        if args.enable_ufw and (args.upgrade or args.rollback or args.uninstall or args.retire_ha or args.configure_ha):
            raise ValueError('--enable-ufw is only available for a fresh installation.')
        if args.cluster_maintenance and not (args.upgrade or args.rollback):
            raise ValueError('--cluster-maintenance requires --upgrade or --rollback.')
        if args.detached_ha and (not (args.uninstall or args.retire_ha) or args.root != '/'):
            raise ValueError('--detached-ha requires local --retire-ha or --uninstall.')
        if sys.version_info < (3, 10):
            raise ValueError('Python 3.10 or later is required.')
        if not argv:
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                raise ValueError('The interactive wizard requires a terminal. Use --plan or --apply for automation.')
            return run_wizard(args)
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+', args.install_dir) or '..' in Path(args.install_dir).parts or args.install_dir in ('/', '/etc', '/usr', '/var', '/opt'):
            raise ValueError('Use a dedicated absolute installation path without spaces.')
        admin_username(args.admin_user)
        manager_port(str(args.manager_port))
        from scripts.deploy.access_safety import network
        if args.management_cidr:
            args.management_cidr = [network(value) for value in args.management_cidr]
        for ports in (args.management_ssh_port, args.management_web_port):
            if ports and any(not 1 <= value <= 65535 for value in ports):
                raise ValueError('Management ports must be between 1 and 65535.')
        ha_args = (args.ha_node, args.ha_interface, args.ha_node_a, args.ha_node_b,
                   args.ha_vip, args.ha_client_cidr, args.ha_vrid)
        if args.apply and args.root != '/':
            raise ValueError('--root is available only when planning.')
        if args.configure_ha:
            if not args.apply or args.root != '/' or args.mode or args.server or any(value is not None for value in ha_args):
                raise ValueError('HA setup requires --apply and the fixed local request file.')
            from scripts.deploy.ha_setup import configure
            configure(args.install_dir)
            return 0
        if args.uninstall or args.retire_ha:
            if args.mode or args.server or any(value is not None for value in ha_args):
                raise ValueError('Removal does not take deployment or server options.')
            if args.retire_ha:
                if args.root != '/':
                    raise ValueError('--root cannot be used with --retire-ha.')
                print(json.dumps(build_ha_retirement_plan(args.install_dir, detached=args.detached_ha), indent=2))
                if args.apply:
                    retire_ha_node(args.install_dir, detached=args.detached_ha)
            else:
                if args.detached_ha:
                    require_retired_cluster()
                print(json.dumps(build_uninstall_plan(args.root, args.install_dir, purge=args.purge), indent=2))
                if args.apply:
                    if args.purge and SOURCE.is_relative_to(Path(args.install_dir)):
                        raise ValueError('Run purge from source outside the installation directory.')
                    uninstall(args.install_dir, detached=args.detached_ha, purge=args.purge)
            return 0
        ha = None
        if any(value is not None for value in ha_args):
            if args.mode != 'ha' or args.rollback or any(value is None for value in ha_args):
                raise ValueError('Use all --ha options together with --mode ha for a new deployment.')
            ha = ha_topology(*ha_args)
        if args.rollback:
            if args.root != '/':
                raise ValueError('--root cannot be used with --rollback.')
            if args.cluster_maintenance:
                if (args.mode or args.server or any(value is not None for value in ha_args)
                        or args.management_cidr or args.management_ssh_port or args.management_web_port):
                    raise ValueError('Protected rollback uses the installed deployment, server and management access profile.')
                plan = build_plan('/', args.install_dir)
                validate_cluster_upgrade(plan, args)
                plan['rollbackRelease'] = str(cluster_rollback_release(args.install_dir, args.rollback))
                print(json.dumps(dict(action='protected-release-rollback', release=plan['rollbackRelease'],
                                      preserves=['live configuration and policy', 'accounts', 'OS cluster and firewall']), indent=2))
                if args.apply:
                    apply(plan, args)
                return 0
            rollback_install(args.install_dir, args.rollback, args.apply)
            return 0
        if args.upgrade:
            installed_deployment(args.root, args.install_dir)
        plan = build_plan(args.root, args.install_dir, args.mode, args.server, ha)
        if args.reviewed_action and plan['action'] != args.reviewed_action:
            raise ValueError('The installation changed. Run the wizard again and review the new plan.')
        plan_management(plan, args)
        validate_ufw_activation(plan, args)
        plan['accessSafety'] = {'managementNetworks': args.management_cidr or 'Existing profile or installing SSH peer',
                                'sshPorts': args.management_ssh_port, 'webPorts': args.management_web_port,
                                'firewall': 'enable-after-rules' if args.enable_ufw else 'preserve-activation'}
        if args.enable_ufw:
            plan['steps'].append('Enable UFW after installing and checking the scoped management and HA rules.')
        if args.upgrade and plan['action'] != 'upgrade':
            raise ValueError('Existing ProxyPermit configuration is required for an upgrade.')
        if args.cluster_maintenance:
            if plan['mode'] != 'ha' or not plan.get('managedHa') or plan.get('ha'):
                raise ValueError('Cluster maintenance requires an existing managed HA installation.')
            plan['steps'] = [step for step in plan['steps'] if not step.startswith(('Install Python venv', 'Upgrade the recognized empty-connect'))]
            plan['steps'].insert(0, 'Require protected quorum and fencing; reserve cluster-wide maintenance and verify VIP takeover before local changes.')
            plan['steps'].append('Update the native VIP agent if it matches the previous release; keep customized agents.')
            plan['steps'].append('Verify manager and native proxy health, then atomically restore the original standby setting and release maintenance.')
            plan['notes'] = [note for note in plan['notes'] if not note.startswith('Logging activation restarts')]
            plan['notes'].append('OS packages, Keepalived and cluster configuration are preserved. Failure leaves the node in standby with a root recovery checkpoint; maintenance never expires automatically.')
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        if args.apply:
            apply(plan, args)
        return 0
    except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'Installation stopped: {exc}', file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print('\nInstallation cancelled.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
