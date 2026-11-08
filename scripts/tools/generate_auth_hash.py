#!/usr/bin/env python3
"""Generate a password hash for AUTH_PASSWORD_HASH."""

import argparse
import getpass
import sys
from werkzeug.security import generate_password_hash


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--password", help="Password. This option exposes it in shell history.")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.password:
        password = args.password
    else:
        password = getpass.getpass("Enter password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("The passwords do not match.", file=sys.stderr)
            return 1

    if len(password) < 8:
        print("Use a password of at least 8 characters.", file=sys.stderr)
        return 1

    password_hash = generate_password_hash(password)
    print("\nAdd these settings to .env:")
    print(f"AUTH_PASSWORD_HASH={password_hash}")
    print("AUTH_PASSWORD=")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
