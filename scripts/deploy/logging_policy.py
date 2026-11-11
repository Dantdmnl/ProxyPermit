"""Installer-owned journal namespaces and independent retention timers."""
from pathlib import Path

MARKER = '# Managed by ProxyPermit logging policy\n'


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
             'proxypermit-ha-retire', 'keepalived', 'tinyproxy')
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
