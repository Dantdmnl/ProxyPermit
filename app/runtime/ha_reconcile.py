"""Run one standby policy reconciliation from a systemd timer."""
import sys

from ..web.manager import app


def main():
    try:
        with app.app_context():
            app.extensions['ha_policy_reconcile']()
    except (OSError, ValueError, KeyError, EOFError, TimeoutError):
        app.logger.warning('HA policy reconciliation failed; the timer will retry.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
