"""Recover an interrupted managed Tinyproxy publication before service start."""
import json
from pathlib import Path
import sys

from app.core.files import file_lock
from app.core.tinyproxy import TinyproxyConfigStore


DROP_IN = Path('/etc/systemd/system/tinyproxy.service.d/30-proxypermit-policy-recovery.conf')
MARKER = '# Managed by ProxyPermit policy recovery\n'


def drop_in(base):
    return MARKER + (
        '[Service]\n'
        f'ExecStartPre=+/usr/sbin/runuser -u proxypermit -- /usr/bin/env -C {base}/current '
        f'{base}/current/.venv/bin/python -B -m app.runtime.policy_recovery\n'
    )


def recover(config_path, filter_path):
    config = TinyproxyConfigStore(config_path)
    filters = TinyproxyConfigStore(filter_path)
    if not (config.pending_path.exists() or filters.pending_path.exists()):
        return False
    try:
        with file_lock(config.activation_lock, timeout=0):
            pass
    except TimeoutError:
        # The publication worker holds this lock only after writing the entire
        # policy and while waiting for its authorized service operation.
        return False
    with file_lock(str(filters.path) + '.lock', timeout=2), file_lock(str(config.path) + '.lock', timeout=2):
        if config.pending_path.exists():
            filters.restore_pending()
            config.restore_pending()
            return True
        # The durable removal of the configuration checkpoint commits the pair.
        # A remaining filter checkpoint only needs its cleanup.
        if filters.pending_path.exists():
            baseline = json.loads(filters.pending_path.read_text(encoding='utf-8'))
            if not isinstance(baseline, dict) or baseline.get('checkpointVersion') != 2:
                raise ValueError('An older filter checkpoint requires operator review.')
        filters.finish_activation()
        return False


def main():
    from dotenv import dotenv_values

    values = dotenv_values('/etc/proxypermit/.env', interpolate=False)
    config = Path(values.get('TINYPROXY_CONFIG_PATH') or '/etc/tinyproxy/tinyproxy.conf')
    filters = Path(values.get('WHITELIST_PATH') or '/etc/tinyproxy/whitelist.txt')
    if config.parent != Path('/etc/tinyproxy') or filters.parent != config.parent or config == filters:
        print('Proxy policy recovery refused unexpected file paths.', file=sys.stderr)
        return 1
    try:
        if recover(config, filters):
            print('Restored the previous Tinyproxy policy after an interrupted publication.')
    except (OSError, ValueError, KeyError):
        print('Proxy policy recovery failed. Preserve the checkpoints and check permissions and disk space.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
