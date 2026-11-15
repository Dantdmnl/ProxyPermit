"""Conservative Tinyproxy activation planning and acknowledged, non-disruptive reloads."""
import json
import os
import re
import subprocess
import time
from pathlib import Path
from .tinyproxy import ConfigError, parse, validate
from .proxy_health import proxy_responsive
from .native_filter import validate_filter

FILTER_SETTINGS = frozenset({'filterdefaultdeny'})


def activation_action(previous, proposed):
    """Only filter contents/options are currently proven safe to reload."""
    def fixed(content):
        return [(row['name'].lower(), row['args']) for row in parse(content)
                if row['name'].lower() not in FILTER_SETTINGS]
    return 'reload' if fixed(previous) == fixed(proposed) else 'restart'


def service_activation_action(previous, proposed):
    """Enable reload only after the host's journal/sudo integration is verified."""
    if os.getenv('TINYPROXY_RELOAD_ENABLED', 'false').lower() != 'true':
        return 'restart'
    return activation_action(previous, proposed)


def _run(*command):
    return subprocess.run(list(command), capture_output=True, text=True, timeout=5, check=True)


def _identity(service):
    output = _run('/usr/bin/systemctl', 'show', service, '--property=MainPID,InvocationID').stdout
    values = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    if not values.get('MainPID', '').isdigit() or int(values['MainPID']) <= 0 or not values.get('InvocationID'):
        raise ValueError('Tinyproxy is not running.')
    return values


def acknowledged_reload(configuration, service='tinyproxy.service', timeout=5):
    """Use SIGUSR1, require a fresh completion record and stable service identity.

    No fallback to restart. The caller owns the shared policy lock and rollback.
    Only the private traffic journal is read, never the host journal.
    """
    if service not in ('tinyproxy', 'tinyproxy.service'):
        return False, 'Reload requires the managed Tinyproxy service.'
    service = 'tinyproxy.service'
    try:
        text = Path(configuration).read_text(encoding='utf-8-sig')
        if not validate(text)['valid']:
            return False, 'Validate the configuration before reloading Tinyproxy.'
        values = {row['name'].lower(): row['args'] for row in parse(text)}
        if values.get('loglevel', ['Info'])[0].lower() not in ('info', 'notice', 'connect'):
            return False, 'Reload confirmation requires Tinyproxy notice logging. Use an explicit restart.'
        if values.get('syslog', ['no'])[0].lower() not in ('yes', 'on'):
            return False, 'Reload confirmation requires managed journal logging. Use an explicit restart.'
        if 'filter' in values:
            filters = Path(values['filter'][0]).read_text(encoding='utf-8-sig')
            kind = values.get('filtertype', ['ere' if values.get('filterextended', ['no'])[0].lower() == 'yes' else 'bre'])[0].lower()
            if not validate_filter(filters, kind)['valid']:
                return False, 'Validate the filter rules before reloading Tinyproxy.'
        binary_version = _run('/usr/bin/tinyproxy', '-v').stdout
        if not re.search(r'\btinyproxy\s+1\.11\.(?:1|3)\b', binary_version, re.I):
            return False, 'Connection-preserving reload is currently verified for Tinyproxy 1.11.1 and 1.11.3 only.'
        before = _identity(service)
        journal = ('/usr/bin/journalctl', '--namespace=proxypermit-traffic', '--unit=' + service,
                   '--quiet', '--no-pager', '--output=json')
        initial = _run(*journal, '--lines=1').stdout.splitlines()
        cursor = json.loads(initial[-1]).get('__CURSOR') if initial else None
        if not isinstance(cursor, str) or not cursor:
            return False, 'Reload confirmation is unavailable. Use an explicit restart.'
        command = ['/usr/bin/systemctl', 'kill', '--kill-whom=main', '--signal=SIGUSR1', service]
        if os.getenv('SERVICE_CONTROL_SUDO', 'false').lower() == 'true':
            command = ['/usr/bin/sudo', '-n', *command]
        sent_after = time.monotonic_ns() // 1000
        _run(*command)
        deadline = time.monotonic() + timeout
        started = False
        while time.monotonic() < deadline:
            if _identity(service) != before:
                return False, 'Tinyproxy changed process during reload. Verify the service before continuing.'
            # --grep combined with --lines implicitly reverses journalctl's
            # traversal. Read forward from the captured cursor instead.
            output = _run(*journal, '--after-cursor=' + cursor, '--grep=Reloading config file').stdout
            for line in output.splitlines():
                record = json.loads(line)
                if (str(record.get('_PID')) != before['MainPID']
                        or record.get('_SYSTEMD_INVOCATION_ID') != before['InvocationID']
                        or int(record.get('__MONOTONIC_TIMESTAMP', '0')) <= sent_after):
                    continue
                message = record.get('MESSAGE', '')
                if re.fullmatch(r'Reloading config file(?: \(.*\))?', message):
                    started = True
                if 'Reloading config file failed' in message:
                    return False, 'Tinyproxy rejected the reload. No restart was attempted.'
                if started and 'Reloading config file finished' in message:
                    if proxy_responsive(configuration) and _identity(service) == before:
                        return True, None
                    return False, 'Tinyproxy did not pass the post-reload health check.'
            time.sleep(.1)
        return False, 'Reload was not acknowledged. Verify the service; no restart was attempted.'
    except (OSError, ValueError, ConfigError, subprocess.SubprocessError):
        return False, 'Reload could not be verified. Check the private Tinyproxy journal; no restart was attempted.'
