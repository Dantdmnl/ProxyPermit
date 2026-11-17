"""Installer-owned journal namespaces and independent retention timers."""
from pathlib import Path
import os
import stat

from app.core.tinyproxy import atomic_write

MARKER = '# Managed by ProxyPermit logging policy\n'


def write_asset(path, content):
    """Public systemd metadata stays traversable even under the helper's 0077 umask."""
    path = Path(path)
    missing = []
    parent = path.parent
    while not parent.exists():
        if parent.is_symlink():
            raise ValueError('Logging policy directories must not be symlinks.')
        missing.append(parent)
        parent = parent.parent
    if parent.is_symlink():
        raise ValueError('Logging policy directories must not be symlinks.')
    for parent in reversed(missing):
        parent.mkdir(mode=0o755)
        parent.chmod(0o755)
    parent = path.parent
    if parent.is_symlink():
        raise ValueError('Logging policy directories must not be symlinks.')
    info = parent.lstat()
    # Repair only the exact public, exclusively installer-owned directory made
    # by older GUI conversions. Never widen an administrator's private folder.
    if (getattr(os, 'geteuid', lambda: -1)() == 0
            and parent.name == 'keepalived.service.d' and stat.S_IMODE(info.st_mode) == 0o700
            and info.st_uid == 0 and path.name == '20-proxypermit-logging.conf'
            and content == MARKER + '[Service]\nLogNamespace=proxypermit\n'
            and list(parent.iterdir()) == [path] and not path.is_symlink()):
        file_info = path.lstat()
        if (stat.S_ISREG(file_info.st_mode) and file_info.st_uid == 0 and file_info.st_nlink == 1
                and stat.S_IMODE(file_info.st_mode) == 0o644 and path.read_text() == content):
            parent.chmod(0o755)
    atomic_write(path, content, 0o644)


def days(values, key, default):
    try:
        value = int(values.get(key, default))
    except (TypeError, ValueError):
        raise ValueError(f'{key} must be an integer between 1 and 3650.') from None
    if not 1 <= value <= 3650:
        raise ValueError(f'{key} must be between 1 and 3650.')
    return value


def assets(release, values):
    management = days(values, 'LOG_RETENTION_DAYS', 30)
    traffic = days(values, 'PROXY_TRAFFIC_RETENTION_DAYS', 7)
    result = {}
    for name, age, budget in (('proxypermit', management, 100), ('proxypermit-traffic', traffic, 32)):
        result[Path(f'/etc/systemd/journald@{name}.conf.d/20-proxypermit.conf')] = MARKER + (
            f'[Journal]\nStorage=persistent\nCompress=yes\nSystemMaxUse={budget}M\nRuntimeMaxUse=16M\n'
            f'SystemMaxFileSize=8M\nMaxFileSec=1h\nMaxRetentionSec={age}day\n'
            'ForwardToSyslog=no\nForwardToKMsg=no\nForwardToConsole=no\nForwardToWall=no\n')
    units = ('proxypermit', 'proxypermit-ha-policy', 'proxypermit-ha-tls', 'proxypermit-ha-setup',
             'proxypermit-ha-retire', 'proxypermit-ha-cert-recover', 'keepalived', 'tinyproxy')
    for name in units:
        if name == 'keepalived' and values.get('DEPLOYMENT_MODE') != 'ha':
            continue
        namespace = 'proxypermit-traffic' if name == 'tinyproxy' else 'proxypermit'
        result[Path(f'/etc/systemd/system/{name}.service.d/20-proxypermit-logging.conf')] = MARKER + f'[Service]\nLogNamespace={namespace}\n'
    result[Path('/etc/systemd/system/proxypermit-audit-retention.service')] = MARKER + (
        '[Unit]\nDescription=Expire ProxyPermit audit records\n'
        '[Service]\nType=oneshot\nUser=proxypermit\nGroup=proxypermit\n'
        'EnvironmentFile=/etc/proxypermit/.env\n'
        f'WorkingDirectory={release}\nExecStart={release}/.venv/bin/python -m app.runtime.log_retention\n'
        'LogNamespace=proxypermit\nUMask=0027\nPrivateTmp=true\nProtectHome=true\nProtectSystem=strict\n'
        'ReadWritePaths=/var/log/proxypermit /var/lib/proxypermit\nNoNewPrivileges=true\nTimeoutStartSec=120\n')
    result[Path('/etc/systemd/system/proxypermit-journal-retention.service')] = MARKER + (
        '[Unit]\nDescription=Expire only ProxyPermit journal namespaces\n'
        '[Service]\nType=oneshot\nLogNamespace=proxypermit\n'
        f'ExecStart=/usr/bin/journalctl --namespace=proxypermit --rotate --vacuum-time={management}d --vacuum-size=100M\n'
        f'ExecStart=/usr/bin/journalctl --namespace=proxypermit-traffic --rotate --vacuum-time={traffic}d --vacuum-size=32M\n'
        'PrivateTmp=true\nProtectHome=true\nTimeoutStartSec=120\n')
    for name in ('audit', 'journal'):
        result[Path(f'/etc/systemd/system/proxypermit-{name}-retention.timer')] = MARKER + (
            f'[Unit]\nDescription=Hourly ProxyPermit {name} retention\n'
            '[Timer]\nOnCalendar=hourly\nPersistent=true\nRandomizedDelaySec=60\n'
            '[Install]\nWantedBy=timers.target\n')
    return result
