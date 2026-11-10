#!/usr/bin/env python3
"""Install ProxyPermit on Debian or Ubuntu. Run without options to open the wizard."""
import argparse
import getpass
import hashlib
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
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE))
from app.core.tinyproxy import atomic_write
from app.core.files import file_lock
from app.core.ha import ha_firewall_rules, ha_topology
from scripts.deploy.install_terminal import InstallTerminal

KEEPALIVED_CONFIG = Path('/etc/keepalived/keepalived.conf')
KEEPALIVED_HEALTH = Path('/etc/keepalived/proxypermit-health')
UFW_RULE_FILES = (Path('/etc/ufw/user.rules'), Path('/etc/ufw/user6.rules'))
HA_FIREWALL_STATE = Path('/etc/proxypermit/ha-firewall.json')
HA_RETIRE_UNIT = Path('/etc/systemd/system/proxypermit-ha-retire.service')
HA_RETIRE_SUDOERS = Path('/etc/sudoers.d/proxypermit-ha-retire')
HA_RETIRE_RULE = 'proxypermit ALL=(root) NOPASSWD: /usr/bin/systemctl start proxypermit-ha-retire.service\n'
MAINTENANCE_DIRECTORY = Path('/run/proxypermit-maintenance')
LEGACY_SYNC_SCRIPT = Path('/usr/local/bin/sync_whitelist_v2.py')
KEEPALIVED_HEALTH_TEXT = (
    '#!/usr/bin/python3\nimport socket\n'
    'with socket.create_connection(("127.0.0.1", 3128), timeout=0.5):\n    pass\n'
)


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
    interval 2
    timeout 1
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
LogLevel Connect
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
            raise ValueError('Cannot access the existing configuration directory. Run the plan with sudo to detect the existing installation.')
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
        'Install Python venv, Tinyproxy, sudo, and logrotate with apt',
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
    if mode == 'standalone':
        notes.append('Standalone mode omits Keepalived and the sync timer.')
    if ha:
        plan['ha'] = ha
        plan['firewallRules'] = ha_firewall_rules(ha)
        steps.append('Configure UFW rules for the proxy VIP and bidirectional VRRP. Keep the existing UFW activation state.')
        steps.append('Install and configure Keepalived, check the local Tinyproxy port, and start VRRP.')
        notes.append('Repeat on the other host with the same cluster settings. Publish policy on each node.')
    if mode == 'ha' and values.get('PROXYPERMIT_MANAGED_HA') == 'true':
        plan['managedHa'] = True
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
            if path.is_dir():
                item['directory'] = True
            else:
                name = str(len(self.entries))
                shutil.copy2(path, self.directory / name)
                os.chmod(self.directory / name, 0o600)
                item['backup'] = name
        self.entries.append(item)
        atomic_write(self.directory / 'manifest.json', json.dumps(self.entries, indent=2))

    @staticmethod
    def _restore_permissions(path: Path, item: SnapshotEntry) -> None:
        mode, uid, gid = item.get('mode'), item.get('uid'), item.get('gid')
        if mode is None or uid is None or gid is None:
            raise ValueError(f'Missing file metadata in the recovery manifest: {path}')
        os.chmod(path, mode)
        if os.name != 'nt':
            os.chown(path, uid, gid)  # pylint: disable=no-member

    def restore(self):
        for item in reversed(self.entries):
            path = Path(item['path'])
            if item.get('directory'):
                self._restore_permissions(path, item)
                continue
            if path.is_symlink() or path.is_file():
                path.unlink()
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
        raise ValueError('Tinyproxy of Keepalived draait al op deze host.')
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


def secure_ha_helper_source(base, release):
    for directory in (base, *base.parents, release.parent, release):
        require_trusted_path(directory, directory=True)
    for directory in (release / 'app', release / 'scripts'):
        for path in (directory, *directory.rglob('*')):
            if path.is_symlink() or path.stat().st_uid != 0:
                raise ValueError(f'The HA helper requires root-owned release files: {path}')
            path.chmod(path.stat().st_mode & ~0o022)


def remove_owned_ha_firewall(owned):
    existing = ufw_added_rules()
    present = set(owned) & existing
    missing = set(owned) - existing
    existing_comments = {shlex.split(line)[-1] for line in existing}
    if any(shlex.split(line)[-1] in existing_comments for line in missing):
        raise ValueError('A managed HA firewall rule was edited. Review it before retiring this node.')
    for line in sorted(present):
        argv = shlex.split(line)
        if argv[:2] != ['ufw', 'allow'] or argv[-2] != 'comment' or not argv[-1].startswith('ProxyPermit HA '):
            raise ValueError('The managed HA firewall rule record is invalid.')
        run('ufw', '--force', 'delete', *argv[1:])
    if ufw_added_rules() != existing - present:
        raise ValueError('The firewall changed unexpectedly while removing the HA rules.')


def reload_ufw():
    config = Path('/etc/ufw/ufw.conf')
    if config.is_file() and re.search(r'^ENABLED=yes\s*$', config.read_text(encoding='utf-8'), re.M):
        run('ufw', 'reload')


def ufw_rule_fingerprints():
    if any(path.is_symlink() or not path.is_file() for path in UFW_RULE_FILES):
        raise ValueError('UFW rule files are missing or have changed location.')
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in UFW_RULE_FILES}


def probe_manager(values):
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


def manager_values(env_path, unit_path):
    values = env_values(env_path)
    if unit_path.exists():
        for key in ('GUNICORN_BIND', 'GUNICORN_USE_TLS'):
            match = re.search(r'^Environment=' + key + r'=([^\r\n]+)$', unit_path.read_text(), re.M)
            if match and key not in values:
                values[key] = match[1]
    return values


def service_text(release, mode, bind, port, preserve_binding=False):
    # Existing HA requires legacy root-level Keepalived/sync operations. Preserve
    # that compatibility explicitly; standalone uses a dedicated account.
    account = '' if mode == 'ha' else 'User=proxypermit\nGroup=proxypermit\nSupplementaryGroups=adm systemd-journal\n'
    binding = '' if preserve_binding else f'Environment=GUNICORN_BIND={bind}:{port}\nEnvironment=GUNICORN_USE_TLS=false\n'
    return f'''[Unit]
Description=ProxyPermit Tinyproxy Manager
After=network.target tinyproxy.service
Wants=tinyproxy.service

[Service]
Type=simple
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


def apply(plan, args):
    require_linux_root('Installation')
    os_release = Path('/etc/os-release').read_text(encoding='utf-8')
    if not re.search(r'^ID=(?:"?)(ubuntu|debian)(?:"?)$', os_release, re.M):
        raise ValueError('This installer supports Debian and Ubuntu with systemd.')
    if not Path('/run/systemd/system').exists():
        raise ValueError('A running systemd instance is required.')
    if plan['mode'] == 'standalone' and plan['action'] == 'upgrade':
        for unit in ('keepalived.service', 'whitelist-sync-v2.timer'):
            if subprocess.run(['systemctl', 'is-active', '--quiet', unit], check=False).returncode == 0:
                raise ValueError('An HA service is active in standalone mode. Check the topology first.')
    with maintenance_lock():
        if args.upgrade:
            installed_deployment('/', plan['installDir'])
            verify_live_manager(plan['installDir'])
        _apply_locked(plan, args)


def _apply_locked(plan, args):
    ha = plan.get('ha')
    managed = plan['mode'] == 'standalone' or ha or plan.get('managedHa')
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
    if ha:
        check_ha_host(ha, config_path)
    if managed and (config_path.parent != Path('/etc/tinyproxy') or filter_path.parent != Path('/etc/tinyproxy')):
        raise ValueError('Custom configuration paths require manually configured permissions. Use /etc/tinyproxy with this installer.')
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
        for path in (Path('/etc/proxypermit'), Path('/var/lib/proxypermit'), Path('/var/log/proxypermit'),
                     config_path.parent, filter_path.parent / '.backups'):
            if path.exists():
                transaction.track(path)
        for key in ('WHITELIST_ENTRY_LOG_PATH', 'SESSION_STATE_FILE'):
            path = Path(legacy_values[key]) if legacy_values.get(key) else None
            if path and path.is_file():
                transaction.track(path)
    previous = service_state('proxypermit.service')
    auxiliary = {unit: service_state(unit) for unit in ('tinyproxy.service', 'keepalived.service')} if ha else {}
    service_snapshot: dict[str, Any] = dict(**previous, auxiliary=auxiliary, ufw=False)
    atomic_write(transaction.directory / 'service-state.json', json.dumps(service_snapshot))
    touched_service = False
    touched_ufw = False
    password = None
    if plan['action'] == 'install':
        if args.admin_password_file:
            password = Path(args.admin_password_file).read_text(encoding='utf-8').rstrip('\r\n')
        else:
            password = getpass.getpass('New administrator password (at least 12 characters): ')
            if password != getpass.getpass('Repeat password: '):
                raise ValueError('The passwords do not match.')
        if len(password) < 12:
            raise ValueError('Use a password of at least 12 characters.')
    try:
        run('apt-get', 'update')
        packages = ['python3-venv', 'tinyproxy', 'sudo', 'logrotate']
        if ha:
            packages.extend(('keepalived', 'ufw'))
        run('apt-get', 'install', '-y', *packages)
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
            'accounts.json', 'totp-steps.json', 'login-attempts.json', 'enroll-*.json',
            '*.key', '*.pem', '*.crt', '*.log', '*.log.*', 'releases', 'backups', 'current', 'dist', '.cache', '.agents', '.codex')
        if ha or HA_RETIRE_UNIT.is_file():
            for directory in (base, *base.parents, release.parent):
                require_trusted_path(directory, directory=True)
            with tempfile.TemporaryDirectory(prefix='.release-', dir=release.parent) as directory:
                staged = Path(directory) / release.name
                shutil.copytree(SOURCE, staged, ignore=ignore)
                staged.chmod(staged.stat().st_mode & ~0o022)
                secure_ha_helper_source(base, staged)
                staged.rename(release)
        else:
            shutil.copytree(SOURCE, release, ignore=ignore)
        run(sys.executable, '-m', 'venv', str(release / '.venv'))
        python = str(release / '.venv/bin/python')
        requirements = 'requirements/granian.txt' if plan['server'] == 'granian' else 'requirements/base.txt'
        run(python, '-m', 'pip', 'install', '-r', str(release / requirements))
        test_env = dict(os.environ, FLASK_SECRET_KEY=secrets.token_hex(32), ENABLE_AUTH='false',
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
                             PROXYPERMIT_HA_NODE=ha['node'])
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
        if managed:
            # Parent write permission is needed for atomic replacement. Do not
            # recursively change arbitrary administrator-owned subdirectories.
            chown(config_path.parent, 0, gid)
            os.chmod(config_path.parent, 0o775)
            chown(config_path, 0, gid)
            os.chmod(config_path, 0o640)
            backup_dir = filter_path.parent / '.backups'
            if backup_dir.exists():
                chown(backup_dir, uid, gid)
                os.chmod(backup_dir, 0o750)
            native_backups = config_path.parent / '.proxypermit-config-backups'
            if native_backups.exists():
                transaction.track(native_backups)
                chown(native_backups, uid, gid)
                os.chmod(native_backups, 0o700)
                for backup_file in native_backups.iterdir():
                    if backup_file.is_file() and not backup_file.is_symlink():
                        transaction.track(backup_file)
                        chown(backup_file, uid, gid)
                        os.chmod(backup_file, 0o600)
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
            atomic_write('/etc/sudoers.d/proxypermit', f'proxypermit ALL=(root) NOPASSWD: /usr/bin/systemctl restart {service}\n', 0o440)
            run('visudo', '-cf', '/etc/sudoers.d/proxypermit')
        if ha:
            atomic_write(KEEPALIVED_HEALTH, KEEPALIVED_HEALTH_TEXT, 0o755)
            atomic_write(KEEPALIVED_CONFIG, keepalived_text(ha), 0o640)
            run('keepalived', '--config-test', '-f', str(KEEPALIVED_CONFIG))
            atomic_write(HA_RETIRE_UNIT, ha_retire_unit_text(str(base)), 0o644)
            atomic_write(HA_RETIRE_SUDOERS, HA_RETIRE_RULE, 0o440)
            run('visudo', '-cf', str(HA_RETIRE_SUDOERS))
        # Load all migrated values via the same dotenv parser as the application,
        # and import as the service user before changing the running service.
        smoke = 'from dotenv import load_dotenv; load_dotenv("/etc/proxypermit/.env", override=True); import wsgi; import os; from app.runtime.server import build_command; build_command(os.getenv("APP_SERVER", "gunicorn"), os.environ)'
        smoke_command = [python, '-c', smoke]
        if managed:
            smoke_command = ['runuser', '-u', 'proxypermit', '--'] + smoke_command
        run(*smoke_command, cwd=release)
        atomic_write('/etc/systemd/system/proxypermit.service',
                     service_text(current, 'standalone' if managed else 'ha', args.manager_bind, args.manager_port,
                                  preserve_binding=plan['action'] == 'upgrade'), 0o644)
        temporary_link = base / '.current-new'
        if temporary_link.is_symlink():
            temporary_link.unlink()
        temporary_link.symlink_to(release, target_is_directory=True)
        os.replace(temporary_link, current)
        run('systemctl', 'daemon-reload')
        touched_service = True
        run('systemctl', 'enable', 'proxypermit.service')
        run('systemctl', 'restart', 'proxypermit.service')
        # An HTTP response verifies Gunicorn booted, rather than merely spawned.
        ready = False
        effective_values = env_values(env_path)
        for _ in range(20):
            ready = probe_manager(effective_values)
            if ready:
                break
            time.sleep(0.5)
        if not ready:
            raise RuntimeError('The web service did not become available. Restoring the previous configuration.')
        if ha:
            touched_ufw = True
            owned_rules = apply_ha_firewall(plan['firewallRules'])
            atomic_write(HA_FIREWALL_STATE, json.dumps({'topology': ha, 'rules': owned_rules}))
            service_snapshot['ufwRuleHashes'] = ufw_rule_fingerprints()
            atomic_write(transaction.directory / 'service-state.json', json.dumps(service_snapshot))
        # Existing Tinyproxy is deliberately not restarted during an upgrade.
        if plan['action'] == 'install':
            run('systemctl', 'enable', 'tinyproxy.service')
            # apt may already have started the package default configuration.
            run('systemctl', 'restart', 'tinyproxy.service')
            run('systemctl', 'is-active', '--quiet', 'tinyproxy.service')
        if ha:
            start_ha()
        if update_legacy_sync:
            atomic_write(LEGACY_SYNC_SCRIPT, (release / 'legacy/sync_whitelist_v2.py').read_text(encoding='utf-8'), 0o755)
        atomic_write(base / 'installed.json', json.dumps(dict(release=str(release), mode=plan['mode'], backup=str(transaction.directory)), indent=2))
        print('Upgrade complete. The existing management listener was preserved.' if plan['action'] == 'upgrade'
              else f'Installation complete. Management console: http://127.0.0.1:{args.manager_port}/')
        if ha:
            print(f"HA node {ha['node'].upper()} is configured. Shared proxy address: {ha['vip'].split('/')[0]}:3128.")
        print(f'Backup and recovery manifest: {transaction.directory}')
    except (Exception, KeyboardInterrupt):
        if auxiliary:
            subprocess.run(['systemctl', 'stop', 'keepalived.service', 'tinyproxy.service'], check=False)
        transaction.restore()
        if touched_ufw:
            reload_ufw()
        if touched_service:
            run('systemctl', 'daemon-reload')
            subprocess.run(['systemctl', 'restart' if previous['active'] else 'stop', 'proxypermit.service'], check=False)
            if not previous['enabled']:
                subprocess.run(['systemctl', 'disable', 'proxypermit.service'], check=False)
        restore_auxiliary_services(auxiliary)
        raise


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


def build_uninstall_plan(root, install_dir):
    installed, unit, current, marker = installed_deployment(root, install_dir)
    paths = [unit, current, marker]
    sudoers = Path(root).resolve() / 'etc/sudoers.d/proxypermit'
    if not sudoers.is_symlink() and sudoers.is_file() and sudoers.read_text(encoding='utf-8') == (
        'proxypermit ALL=(root) NOPASSWD: /usr/bin/systemctl restart tinyproxy.service\n'
    ):
        paths.append(sudoers)
    for path, content in ((HA_RETIRE_UNIT, ha_retire_unit_text(install_dir)), (HA_RETIRE_SUDOERS, HA_RETIRE_RULE)):
        target = Path(root).resolve() / path.as_posix().lstrip('/')
        if not target.is_symlink() and target.is_file() and target.read_text(encoding='utf-8') == content:
            paths.append(target)
    return {
        'action': 'uninstall', 'mode': installed.get('mode'), 'installDir': install_dir,
        'service': 'proxypermit.service', 'paths': [str(path) for path in paths],
        'preserved': ['/etc/proxypermit', '/var/lib/proxypermit', '/var/log/proxypermit',
                      f'{install_dir}/releases', f'{install_dir}/backups',
                      'Tinyproxy, Keepalived, legacy sync, system packages, and the service account'],
    }


def verify_live_manager(install_dir):
    properties = run('systemctl', 'show', '--property=FragmentPath', '--property=WorkingDirectory',
                     'proxypermit.service', capture_output=True, text=True).stdout.splitlines()
    if ('FragmentPath=/etc/systemd/system/proxypermit.service' not in properties
            or f'WorkingDirectory={Path(install_dir).as_posix()}/current' not in properties):
        raise ValueError('systemd is using a different unit file or working directory for ProxyPermit.')


def uninstall(install_dir):
    require_linux_root('Uninstall')
    with maintenance_lock():
        plan = build_uninstall_plan('/', install_dir)
        unit = plan['service']
        verify_live_manager(install_dir)
        previous = service_state(unit)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        transaction = Transaction(Path(install_dir) / 'backups' / stamp)
        for path in plan['paths']:
            transaction.track(path)
        atomic_write(transaction.directory / 'service-state.json', json.dumps(previous))
        print(f'Recovery backup: {stamp}', flush=True)
        try:
            run('systemctl', 'disable', '--now', unit)
            for path in plan['paths']:
                Path(path).unlink()
            run('systemctl', 'daemon-reload')
        except (Exception, KeyboardInterrupt):
            transaction.restore()
            run('systemctl', 'daemon-reload')
            restore_auxiliary_services({unit: previous})
            raise
        print('ProxyPermit manager removed.')


def build_ha_retirement_plan(install_dir):
    installed, _, _, _ = installed_deployment('/', install_dir)
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
    return {'action': 'retire-ha-node', 'vip': topology['vip'].split('/', 1)[0], 'peer': topology['peer'],
            'services': ['keepalived.service', 'tinyproxy.service'], 'ownedFirewallRules': rules}


def verify_remote_proxy(vip, peer):
    interfaces = json.loads(run('ip', '-j', '-4', 'address', 'show', capture_output=True, text=True).stdout)
    local_addresses = {address['local'] for interface in interfaces for address in interface.get('addr_info', [])}
    if vip in local_addresses or peer in local_addresses:
        raise ValueError('Move the virtual IP to the peer before retiring this node.')
    try:
        with socket.create_connection((vip, 3128), timeout=3):
            pass
    except OSError as exc:
        raise ValueError(f'The proxy listener on {vip}:3128 is unreachable. Retirement was stopped.') from exc


def retire_ha_node(install_dir):
    require_linux_root('HA retirement')
    with maintenance_lock():
        plan = build_ha_retirement_plan(install_dir)
        verify_live_manager(install_dir)
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
        for path in (*UFW_RULE_FILES, HA_FIREWALL_STATE):
            transaction.track(path)
        atomic_write(transaction.directory / 'service-state.json', json.dumps(snapshot))
        print(f'Recovery backup: {stamp}', flush=True)
        try:
            for unit in plan['services']:
                run('systemctl', 'disable', '--now', unit)
            verify_remote_proxy(plan['vip'], plan['peer'])
            remove_owned_ha_firewall(plan['ownedFirewallRules'])
            verify_remote_proxy(plan['vip'], plan['peer'])
            HA_FIREWALL_STATE.unlink()
            snapshot['ufwRuleHashes'] = ufw_rule_fingerprints()
            atomic_write(transaction.directory / 'service-state.json', json.dumps(snapshot))
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
        if state.get('ufw') and state.get('ufwRuleHashes') != ufw_rule_fingerprints():
            raise ValueError('UFW rules changed since installation. Review the firewall backup before restoring.')
        subprocess.run(['systemctl', 'stop', 'proxypermit.service'], check=False)
        auxiliary = state.get('auxiliary', {})
        if auxiliary:
            run('systemctl', 'stop', 'keepalived.service', 'tinyproxy.service')
        transaction = object.__new__(Transaction)
        transaction.directory, transaction.entries = directory, entries
        transaction.restore()
        run('systemctl', 'daemon-reload')
        if state.get('ufw'):
            reload_ufw()
        restore_auxiliary_services(auxiliary)
        if state['active']:
            run('systemctl', 'start', 'proxypermit.service')
            values = manager_values(Path('/etc/proxypermit/.env'), Path('/etc/systemd/system/proxypermit.service'))
            for _ in range(20):
                if probe_manager(values):
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError('Recovery finished, but the web service is unreachable at the restored address.')
        subprocess.run(['systemctl', 'enable' if state['enabled'] else 'disable', 'proxypermit.service'], check=False)
        print('Restored the previous configuration and release link. Installed packages were retained.')


def wizard_ha(ui, args):
    node = ('a', 'b')[ui.choose('Which node are you installing?', ('Node A · preferred', 'Node B · standby'))]
    fields = (
        ('Network interface on this host', None),
        ('Static IPv4 address of node A', None),
        ('Static IPv4 address of node B', None),
        ('Virtual IPv4 address with subnet prefix, for example 192.0.2.10/24', None),
        ('Allowed client network with subnet prefix, for example 192.0.2.0/24', None),
        ('Available VRRP ID on this network (1–255)', '51'),
    )
    defaults = [default for _, default in fields]
    while True:
        defaults = [ui.ask(label, default) for (label, _), default in zip(fields, defaults)]
        try:
            interface, a, b, vip, clients, vrid = defaults
            ha = ha_topology(node, interface, a, b, vip, clients, int(vrid))
        except ValueError as exc:
            print(f'  {exc} Check the values. Press Enter to keep your previous input.')
            continue
        args.ha_node, args.ha_interface = node, interface
        args.ha_node_a, args.ha_node_b, args.ha_vip = a, b, vip
        args.ha_client_cidr, args.ha_vrid = clients, int(vrid)
        return ha


def run_wizard(args):
    ui = InstallTerminal()
    ui.heading('ProxyPermit / Installation')
    print('  Configure Tinyproxy in three steps. Press Ctrl+C to cancel.')
    print('  Applying the plan requires Linux and root privileges.')
    detected = build_plan('/', args.install_dir)
    ui.heading('01  Choose the deployment topology')
    if detected['action'] == 'upgrade':
        args.mode = detected['mode']
        print(f'  Existing installation found ({args.mode}). The wizard will prepare an upgrade plan.')
    else:
        modes = ('Standalone node · one server', 'Two-node HA · shared proxy address through Keepalived')
        args.mode = ('standalone', 'ha')[ui.choose('How do you want to deploy ProxyPermit?', modes)]
    ha = wizard_ha(ui, args) if args.mode == 'ha' and detected['action'] == 'install' else None
    ui.heading('02  Configure the management console')
    servers = ('gunicorn', 'granian')
    args.server = servers[ui.choose('Which web server do you want to use?', ('Gunicorn', 'Granian'),
                                    default=servers.index(detected['server']))]
    if detected['action'] == 'install':
        args.admin_user = ui.ask('Administrator username', args.admin_user, admin_username)
        args.manager_port = ui.ask('Local management port', str(args.manager_port), manager_port)
    plan = build_plan('/', args.install_dir, args.mode, args.server, ha)
    ui.review(plan, args)
    action = ui.choose('What would you like to do?', ('Exit without installing', 'Apply plan'))
    if action == 0:
        print('  Installation cancelled before applying the plan.')
        return 0
    if sys.platform != 'linux':
        raise ValueError('Applying the plan requires Linux. Run the wizard on Debian or Ubuntu.')
    if os.geteuid() != 0:  # pylint: disable=no-member
        command = ['sudo', 'bash', 'install.sh', '--mode', args.mode, '--server', args.server, '--apply']
        if plan['action'] == 'install':
            command.extend(['--admin-user', args.admin_user, '--manager-port', str(args.manager_port)])
        if ha:
            for name in ('node', 'interface', 'node_a', 'node_b', 'vip', 'client_cidr', 'vrid'):
                command.extend(['--ha-' + name.replace('_', '-'), str(getattr(args, 'ha_' + name))])
        print('  Apply the confirmed plan from the repository root with root privileges.')
        print('  ' + shlex.join(command))
        return 0
    apply(plan, args)
    return 0


def admin_username(value):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', value):
        raise ValueError('Use 1–64 letters, digits, dots, underscores, or hyphens.')
    return value


def manager_port(value):
    if not value.isascii() or not value.isdecimal() or len(value) > 5 or not 1024 <= int(value) <= 65535:
        raise ValueError('Choose a management port between 1024 and 65535.')
    return int(value)


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
    parser.add_argument('--root', default='/', help='Plan only: inspect an alternative system root.')
    parser.add_argument('--install-dir', default='/opt/proxypermit')
    parser.add_argument('--admin-user', default='admin')
    parser.add_argument('--admin-password-file')
    parser.add_argument('--manager-bind', default='127.0.0.1', choices=['127.0.0.1'])
    parser.add_argument('--manager-port', type=int, default=8080)
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
        ha_args = (args.ha_node, args.ha_interface, args.ha_node_a, args.ha_node_b,
                   args.ha_vip, args.ha_client_cidr, args.ha_vrid)
        if args.apply and args.root != '/':
            raise ValueError('--root is available only when planning.')
        if args.uninstall or args.retire_ha:
            if args.mode or args.server or any(value is not None for value in ha_args):
                raise ValueError('Removal does not take deployment or server options.')
            if args.retire_ha:
                if args.root != '/':
                    raise ValueError('--root cannot be used with --retire-ha.')
                print(json.dumps(build_ha_retirement_plan(args.install_dir), indent=2))
                if args.apply:
                    retire_ha_node(args.install_dir)
            else:
                print(json.dumps(build_uninstall_plan(args.root, args.install_dir), indent=2))
                if args.apply:
                    uninstall(args.install_dir)
            return 0
        ha = None
        if any(value is not None for value in ha_args):
            if args.mode != 'ha' or args.rollback or any(value is None for value in ha_args):
                raise ValueError('Use all --ha options together with --mode ha for a new deployment.')
            ha = ha_topology(*ha_args)
        if args.rollback:
            if args.root != '/':
                raise ValueError('--root cannot be used with --rollback.')
            rollback_install(args.install_dir, args.rollback, args.apply)
            return 0
        if args.upgrade:
            installed_deployment(args.root, args.install_dir)
        plan = build_plan(args.root, args.install_dir, args.mode, args.server, ha)
        if args.upgrade and plan['action'] != 'upgrade':
            raise ValueError('Existing ProxyPermit configuration is required for an upgrade.')
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
