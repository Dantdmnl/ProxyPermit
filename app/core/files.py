"""Small cross-process lock shared by HTTP workers and local maintenance."""
import os
import time
from contextlib import contextmanager
from pathlib import Path
from contextvars import ContextVar

_held = ContextVar('held_file_locks', default=())


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
