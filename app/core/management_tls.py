"""Read only root-authored certificate metadata; never load a management key."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import time

FOLDER = Path('/etc/proxypermit/management-tls')


def status(values=None, now=None):
    values = os.environ if values is None else values
    cert = Path(values.get('MANAGER_CERT', ''))
    if cert.parent.parent != FOLDER or cert.name != 'cert.pem' or not re.fullmatch(r'[0-9a-f]{32}', cert.parent.name):
        return None
    unknown = dict(state='unknown')
    if values.get('MANAGER_SCHEME') != 'https' or values.get('GUNICORN_USE_TLS', '').lower() in ('false', '0', 'no'):
        return unknown
    try:
        descriptor = os.open(cert.with_name('info.json'), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                             | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(descriptor, encoding='utf-8') as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                    or info.st_nlink != 1 or info.st_size > 16384):
                return unknown
            value = json.load(source)
        if (not isinstance(value, dict) or value.get('version') != 1 or value.get('generation') != cert.parent.name
                or type(value.get('expires')) is not int or type(value.get('caExpires')) is not int
                or any(not re.fullmatch(r'[0-9a-f]{64}', str(value.get(key, ''))) for key in ('fingerprint', 'caFingerprint'))):
            return unknown
        now = time.time() if now is None else now
        remaining = min(value['expires'], value['caExpires']) - now
        state = 'expired' if remaining <= 0 else 'due' if remaining <= 30 * 86400 or value['caExpires'] - now <= 91 * 86400 else 'valid'
        return dict(state=state, expires=datetime.fromtimestamp(value['expires'], timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
                    fingerprint=value['fingerprint'], caFingerprint=value['caFingerprint'])
    except (OSError, ValueError, OverflowError):
        return unknown
