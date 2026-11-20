"""Small cross-process lock shared by HTTP workers and local maintenance."""
import os
import errno
import time
from contextlib import contextmanager
from pathlib import Path
from contextvars import ContextVar

_held = ContextVar('held_file_locks', default=())


class DurabilityError(OSError):
    """A replacement is visible, but its directory entry was not confirmed durable."""


def sync_directory(path):
    """Persist Linux rename/unlink metadata before acknowledging a state change."""
    if os.name == 'nt':
        return  # Windows is a development platform, not a supported HA host.
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def access_acl(path) -> bytes | None:
    """Read Linux access ACLs, without following a symlink or copying capabilities."""
    get = getattr(os, 'getxattr', None)
    if not callable(get):
        return None
    try:
        value = get(path, 'system.posix_acl_access', follow_symlinks=False)  # pylint: disable=not-callable
        if not isinstance(value, bytes):
            raise OSError('Invalid POSIX ACL metadata.')
        return value
    except OSError as exc:
        if exc.errno in (errno.ENODATA, errno.ENOTSUP, errno.ENOENT):
            return None
        raise


def restore_access_acl(path, value: bytes | None) -> None:
    """Restore precisely the original access ACL, including its absence."""
    setter, remover = getattr(os, 'setxattr', None), getattr(os, 'removexattr', None)
    if not callable(setter) or not callable(remover):
        if value is not None:
            raise OSError('POSIX ACL support is unavailable.')
        return
    if value is not None:
        setter(path, 'system.posix_acl_access', value, follow_symlinks=False)  # pylint: disable=not-callable
        return
    try:
        remover(path, 'system.posix_acl_access', follow_symlinks=False)  # pylint: disable=not-callable
    except OSError as exc:
        if exc.errno not in (errno.ENODATA, errno.ENOTSUP):
            raise


@contextmanager
def file_lock(path, timeout=15):
    path = Path(path)
    key = str(path.resolve())
    if key in _held.get():
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt  # pylint: disable=import-error
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl  # pylint: disable=import-error
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('File is in use; try again.')
                time.sleep(0.05)
        token = _held.set(_held.get() + (key,))
        try:
            yield
        finally:
            _held.reset(token)
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt  # pylint: disable=import-error
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl  # pylint: disable=import-error
                fcntl.flock(handle, fcntl.LOCK_UN)
