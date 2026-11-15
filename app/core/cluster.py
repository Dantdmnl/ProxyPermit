"""Read-only cluster evidence and the boundary around independent service control."""
import json
import os
from pathlib import Path
import stat
import time

from .tinyproxy import ConfigError

PROFILE = Path('/etc/proxypermit/cluster-protection.json')
SERVICE_RESERVATION = Path('/etc/systemd/system/keepalived.service.d/proxypermit-cluster.conf')
STATUS = Path('/run/proxypermit-cluster/status.json')
MAX_STATUS_AGE = 20


def reserved(profile=None):
    path = PROFILE if profile is None else Path(profile)
    return (path.exists() or path.is_symlink()
            or SERVICE_RESERVATION.exists() or SERVICE_RESERVATION.is_symlink())


def require_independent_control(action, profile=None):
    if reserved(profile):
        raise ConfigError('This node is reserved for cluster-managed HA. Arrange OS-level cluster maintenance before ' + action + '.')


def read_status(now=None):
    """Never retain a healthy claim when protected root evidence is unavailable."""
    unknown = dict(state='unknown')
    configured = reserved()
    try:
        descriptor = os.open(STATUS, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    except FileNotFoundError:
        return unknown if configured else dict(state='not-configured')
    except OSError:
        return unknown
    try:
        with os.fdopen(descriptor, encoding='utf-8') as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                    or info.st_nlink != 1 or info.st_size > 8192):
                return unknown
            value = json.load(source)
        if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1:
            return unknown
        checked = value.get('checked')
        now = time.time() if now is None else now
        if type(checked) is not int or not -5 <= now - checked <= MAX_STATUS_AGE:
            return unknown
        state = value.get('state')
        if not configured:
            return dict(state='not-configured') if state == 'not-configured' else unknown
        if state not in ('protected', 'degraded', 'stopped', 'unsafe', 'unknown'):
            return unknown
        fields = ('quorate', 'witnessConnected', 'fencingConfigured', 'fencingReady', 'policySafe')
        if any(type(value.get(key)) is not bool for key in fields):
            return unknown
        owners = value.get('owners')
        if type(owners) is not int or not 0 <= owners <= 2:
            return unknown
        if state == 'protected' and (owners != 1 or not all(value[key] for key in fields)):
            return unknown
        valid_for = min(MAX_STATUS_AGE, max(0, int(checked + MAX_STATUS_AGE - now)))
        return dict(state=state, checked=checked, validFor=valid_for, owners=owners, **{key: value[key] for key in fields})
    except (OSError, ValueError, RecursionError):
        return unknown
