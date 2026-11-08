"""Manage the local ProxyPermit administrator account file."""
import argparse
import getpass
import json
import os
import re
import secrets
import shutil
import sys
from pathlib import Path

import pyotp
from werkzeug.security import generate_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.core.accounts import load_accounts, matching_totp_step
from app.core.files import file_lock
from app.core.tinyproxy import atomic_write

DEFAULT = Path('/var/lib/proxypermit/accounts.json')


def read(path):
    try:
        return load_accounts(path)
    except FileNotFoundError:
        return {}


def enroll(username):
    secret = pyotp.random_base32()
    print('Add this key to your authenticator app and choose time-based codes (TOTP).')
    print(f'Account: {username}\nSetup key: {secret}')
    if matching_totp_step(secret, input('Authenticator code: ').strip()) is None:
        raise ValueError('Invalid authenticator code.')
    return secret


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path, default=DEFAULT)
    actions = parser.add_subparsers(dest='action', required=True)
    edit = actions.add_parser('set', help='Create an account or change its password.')
    edit.add_argument('username')
    factor = edit.add_mutually_exclusive_group()
    factor.add_argument('--totp', action='store_true', help='Enroll a new authenticator.')
    factor.add_argument('--no-totp', action='store_true', help='Disable 2FA for this account.')
    actions.add_parser('list', help='List accounts and their 2FA status.')
    actions.add_parser('remove', help='Remove an account.').add_argument('username')
    args = parser.parse_args()
    path = args.file
    try:
        accounts = read(path)
        if args.action == 'list':
            for username, account in sorted(accounts.items()):
                print(f"{username}\t2FA {'enabled' if 'totp_secret' in account else 'disabled'}")
            return 0
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', args.username):
            raise ValueError('Enter a valid administrator username.')
        if args.action == 'set' and (not sys.stdin.isatty() or not sys.stdout.isatty()):
            raise ValueError('Use a private terminal to configure an account.')
        previous = accounts.get(args.username)
        if args.action == 'set':
            password = getpass.getpass('New password: ')
            if not 12 <= len(password) <= 256 or password != getpass.getpass('Repeat password: '):
                raise ValueError('Enter matching passwords of 12–256 characters.')
            updated = dict(password_hash=generate_password_hash(password), revision=secrets.token_hex(16))
            if args.totp:
                updated['totp_secret'] = enroll(args.username)
            elif not args.no_totp and previous and 'totp_secret' in previous:
                updated['totp_secret'] = previous['totp_secret']
        lock = str(path) + '.lock'
        with file_lock(lock):
            accounts = read(path)
            if accounts.get(args.username) != previous:
                raise ValueError('This account changed. Try again.')
            if args.action == 'remove':
                if args.username not in accounts or len(accounts) == 1:
                    raise ValueError('The account is missing or is the last administrator.')
                del accounts[args.username]
            else:
                accounts[args.username] = updated
            # The service reads the default file as group proxypermit. Only
            # root can write it, and explicit test paths remain private.
            if path == DEFAULT and os.name == 'posix':
                shutil.chown(lock, user='proxypermit', group='proxypermit')
                if not path.exists():
                    atomic_write(path, json.dumps(accounts))
                shutil.chown(path, user='root', group='proxypermit')
                os.chmod(path, 0o640)
                atomic_write(path, json.dumps(accounts, indent=2), 0o640)
            else:
                atomic_write(path, json.dumps(accounts, indent=2))
        print('Account file updated.')
        return 0
    except (OSError, ValueError, EOFError, KeyboardInterrupt) as exc:
        print(f'Account update cancelled: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
