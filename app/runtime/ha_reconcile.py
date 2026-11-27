"""Run one standby policy reconciliation from a systemd timer."""
import sys

from ..core.tinyproxy import PolicyCheckpointError
from ..web.manager import app


def main():
    try:
        with app.app_context():
            app.extensions['ha_policy_reconcile']()
    except PolicyCheckpointError:
        app.logger.warning('HA policy reconciliation is waiting for local policy recovery; the timer will retry.')
        return 1
    except (OSError, ValueError, KeyError, EOFError, TimeoutError):
        app.logger.warning('HA policy reconciliation failed; the timer will retry.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
