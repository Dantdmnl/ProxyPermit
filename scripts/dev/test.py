#!/usr/bin/env python3
"""Run isolated unit tests; do not read or write production state."""
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    original = dict(os.environ)
    try:
        with tempfile.TemporaryDirectory(prefix='proxypermit-tests-') as directory:
            state = Path(directory)
            # The app loads .env without overriding these isolated settings.
            os.environ.update(FLASK_SECRET_KEY='isolated-unit-tests-only', ENABLE_AUTH='false', ENABLE_RADIUS='false',
                              AUTH_ACCOUNTS_FILE='',
                              PROXYPERMIT_ACCESS_SAFETY_FILE=str(state / 'access-safety.json'),
                              DEPLOYMENT_MODE='standalone', DEMO_MODE='true',
                              WHITELIST_PATH=str(state / 'filter.txt'),
                              TINYPROXY_CONFIG_PATH=str(state / 'tinyproxy.conf'),
                              AUDIT_LOG_PATH=str(state / 'audit.log'),
                              SESSION_STATE_FILE=str(state / 'session.json'),
                              SYNC_STATE_FILE=str(state / 'sync.json'),
                              SYNC_SECRET_FILE=str(state / 'sync.key'),
                              NONCE_STORE_FILE=str(state / 'nonces.json'),
                              MANUAL_SYNC_STATE_FILE=str(state / 'manual-sync.json'),
                              WHITELIST_ENTRY_LOG_PATH=str(state / 'entries.json'))
            logging.disable(logging.CRITICAL)
            try:
                result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(str(ROOT / 'tests/python')))
                return 0 if result.wasSuccessful() else 1
            finally:
                logging.shutdown()  # Close Windows file handles before cleanup.
    finally:
        os.environ.clear()
        os.environ.update(original)


if __name__ == '__main__':
    raise SystemExit(main())
