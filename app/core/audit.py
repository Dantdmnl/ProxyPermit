"""Private, process-safe audit rotation and age-based retention."""
import json
import logging
import os
import stat
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .files import file_lock
from .tinyproxy import atomic_write


def bounded_setting(name, default, minimum=1, maximum=3650):
    try:
        value = int(os.getenv(name, str(default)))
        return value if minimum <= value <= maximum else default
    except ValueError:
        return default


def record_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc).timestamp() if parsed.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


@contextmanager
def audit_lock(path):
    path = Path(str(path) + '.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        with file_lock(path):
            yield
        return
    import fcntl  # pylint: disable=import-error
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o640)  # pylint: disable=no-member
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Invalid audit lock file.')
        if os.geteuid() == 0:  # pylint: disable=no-member
            parent = path.parent.stat()
            os.fchown(descriptor, parent.st_uid, parent.st_gid)  # pylint: disable=no-member
        deadline = time.monotonic() + 15
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Audit log is in use.') from None
                time.sleep(0.02)
        yield
    finally:
        os.close(descriptor)


def audit_paths(path):
    path = Path(path)
    backups = sorted((item for item in path.parent.glob(path.name + '.*')
                      if item.name[len(path.name) + 1:].isdigit()),
                     key=lambda item: int(item.name[len(path.name) + 1:]))
    if len(backups) > 100:
        raise ValueError('Too many audit archives; review the log directory.')
    return [path, *backups]


@contextmanager
def open_audit(path):
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Invalid audit file.')
        yield handle


def prune_audit(path, days=None, now=None):
    """Remove expired records even from quiet active files, under the writer lock."""
    days = days or bounded_setting('AUDIT_LOG_RETENTION_DAYS', 30)
    cutoff = (time.time() if now is None else now) - days * 86400
    removed = 0
    max_bytes = bounded_setting('AUDIT_LOG_MAX_BYTES', 2097152, 65536, 104857600)
    backups = bounded_setting('AUDIT_LOG_BACKUP_COUNT', 5, 1, 50)
    with audit_lock(path):
        for source in audit_paths(path):
            if source != Path(path) and int(source.suffix[1:]) > backups:
                source.unlink(missing_ok=True)
                continue
            try:
                with open_audit(source) as handle:
                    if os.fstat(handle.fileno()).st_size > 128 * 1024 * 1024:
                        raise ValueError('Audit file exceeds the maintenance safety limit.')
                    keep = deque()
                    size = 0
                    for line in handle:
                        try:
                            item = json.loads(line)
                            timestamp = record_time(item.get('timestamp')) if isinstance(item, dict) else None
                        except (ValueError, UnicodeDecodeError, RecursionError):
                            timestamp = None
                        if timestamp is not None and timestamp >= cutoff:
                            keep.append(line.decode('utf-8'))
                            size += len(line)
                            while size > max_bytes and keep:
                                size -= len(keep.popleft().encode('utf-8'))
                                removed += 1
                        else:
                            removed += 1
                if keep:
                    atomic_write(source, ''.join(keep), 0o640)
                elif source != Path(path):
                    source.unlink()
                else:
                    atomic_write(source, '', 0o640)
            except FileNotFoundError:
                continue
    return removed


class SharedAuditHandler(RotatingFileHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_cleanup = 0

    def handleError(self, record):
        # Logging's default error handler prints the failed record to stderr,
        # which would copy personal data into a different retention boundary.
        logging.getLogger('proxypermit.audit').error('Audit write failed. Check audit storage availability and permissions.')

    def _open(self):
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        descriptor = os.open(self.baseFilename, flags, 0o640)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('Invalid audit output file.')
            if os.name != 'nt':
                os.fchmod(descriptor, 0o640)  # pylint: disable=no-member
                if os.geteuid() == 0:  # pylint: disable=no-member
                    parent = Path(self.baseFilename).parent.stat()
                    os.fchown(descriptor, parent.st_uid, parent.st_gid)  # pylint: disable=no-member
            return os.fdopen(descriptor, 'a', encoding='utf-8')
        except Exception:
            os.close(descriptor)
            raise

    def emit(self, record):
        try:
            if time.time() - self.last_cleanup >= 3600:
                prune_audit(self.baseFilename)
                self.last_cleanup = time.time()
            with audit_lock(self.baseFilename):
                try:
                    super().emit(record)
                finally:
                    if self.stream:
                        self.stream.close()
                        self.stream = None
        except (OSError, TimeoutError, ValueError):
            self.handleError(record)
