"""Local administrator accounts and one-time authenticator code verification."""
import hmac
import json
import re
import time
from pathlib import Path

import pyotp
from werkzeug.security import check_password_hash

from .files import file_lock
from .tinyproxy import atomic_write

ROLES = frozenset({'admin', 'manager', 'monitor'})
ROLE_PERMISSIONS = {
    'admin': frozenset({'read', 'proxy', 'admin', 'self'}),
    'manager': frozenset({'read', 'proxy', 'self'}),
    'monitor': frozenset({'read', 'self'}),
}


def account_role(account):
    """Existing accounts retain admin rights; malformed explicit roles fail closed."""
    role = account.get('role', 'admin') if isinstance(account, dict) else None
    return role if isinstance(role, str) and role in ROLES else None


def permits(role, permission):
    return permission in ROLE_PERMISSIONS.get(role, ())


def admin_count(accounts):
    return sum(account_role(account) == 'admin' and isinstance(account.get('revision'), str)
               and bool(account['revision']) and isinstance(account.get('password_hash'), str)
               and bool(account['password_hash']) for account in accounts.values())


def load_accounts(path):
    with Path(path).open(encoding='utf-8') as handle:
        accounts = json.load(handle)
    if not isinstance(accounts, dict):
        raise ValueError('Invalid account file.')
    return accounts


def account_for(path, username):
    if not isinstance(username, str):
        return None
    account = load_accounts(path).get(username)
    if (isinstance(account, dict) and isinstance(account.get('revision'), str)
            and account['revision'] and isinstance(account.get('password_hash'), str) and account_role(account)):
        return account
    return None


def verify_password(account, password):
    return bool(account and check_password_hash(account['password_hash'], password))


def matching_totp_step(secret, code, now=None):
    if not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}', code):
        return None
    if not isinstance(secret, str) or not re.fullmatch(r'[A-Z2-7]{32}', secret):
        return None
    totp = pyotp.TOTP(secret)
    current = int(time.time() if now is None else now) // totp.interval
    for step in (current, current - 1, current + 1):
        if hmac.compare_digest(totp.generate_otp(step), code):
            return step
    return None


def consume_totp(path, username, revision, step):
    if step is None:
        return False
    path = Path(path)
    with file_lock(str(path) + '.lock'):
        try:
            entries = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            entries = {}
        key = f'{username}:{revision}'
        if step <= entries.get(key, -1):
            return False
        entries[key] = step
        atomic_write(path, json.dumps(entries))
        return True
