"""Expire audit records without loading Flask or account/session state."""
import os
import json
import time
from pathlib import Path
from datetime import datetime
from ..core.audit import bounded_setting, open_audit
from ..core.files import file_lock
from ..core.tinyproxy import atomic_write
from ..core.audit import prune_audit


def prune_legacy_entry_history():
    path = Path(os.getenv('WHITELIST_ENTRY_LOG_PATH', '/var/lib/proxypermit/whitelist_entry_log.json'))
    if path.parent != Path('/var/lib/proxypermit') or not path.exists():
        return
    cutoff = time.time() - bounded_setting('AUDIT_LOG_RETENTION_DAYS', 30) * 86400
    with file_lock(str(path) + '.lock'):
        with open_audit(path) as handle:
            if os.fstat(handle.fileno()).st_size > 32 * 1024 * 1024:
                raise ValueError('Legacy entry history exceeds the maintenance limit.')
            entries = json.load(handle)
        if not isinstance(entries, list):
            raise ValueError('Invalid legacy entry history.')
        kept = []
        for item in entries:
            try:
                stamp = datetime.fromisoformat(item['timestamp']).timestamp()
            except (ValueError, KeyError, TypeError):
                continue
            if stamp >= cutoff:
                kept.append(item)
        atomic_write(path, json.dumps(kept), 0o640)


def main():
    removed = prune_audit(os.getenv('AUDIT_LOG_PATH', '/var/log/proxypermit/audit.log'))
    prune_legacy_entry_history()
    print(f'Audit retention completed; removed {removed} expired or malformed records.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
