"""The per-issue lock of the live CLI: one live run per issue at a time.

The lock file is created with `O_CREAT | O_EXCL` and holds its owner's PID. The
owner also keeps an exclusive `flock` on it for as long as it runs; the kernel
drops that lock when the process ends. A file whose `flock` can be taken and whose
PID is not alive is stale and is replaced. Replacing is exact: only a process that
holds the `flock` of the file currently at the path may remove it, so two processes
cannot both replace one stale lock.

Standard library only (no `app` imports), so a test can load this module in
subprocesses that race for one lock.
"""

import fcntl
import os
from pathlib import Path

_ATTEMPTS = 10


class LockHeld(Exception):
    """Another live run holds the lock (or the lock file holds no PID)."""


def _pid_in(fd: int) -> int | None:
    """The PID written in the lock file; None when it holds no PID."""
    text = os.pread(fd, 32, 0).decode("ascii", errors="replace").strip()
    return int(text) if text.isdigit() and int(text) > 0 else None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # it exists, under another user
        return True
    return True


def _is_at(fd: int, path: Path) -> bool:
    """True when `path` still names the file open as `fd`."""
    try:
        here = os.stat(path)
    except FileNotFoundError:
        return False
    there = os.fstat(fd)
    return (here.st_dev, here.st_ino) == (there.st_dev, there.st_ino)


class IssueLock:
    """A held lock. `release()` removes the file and drops the `flock`."""

    def __init__(self, path: Path, fd: int) -> None:
        self.path = path
        self._fd: int | None = fd

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            if _is_at(self._fd, self.path):
                os.unlink(self.path)
        except FileNotFoundError:
            pass
        finally:
            os.close(self._fd)  # drops the flock, after the file is gone
            self._fd = None


def _remove_if_stale(path: Path) -> None:
    """Remove the lock file at `path` when it is stale. Raises LockHeld when its
    owner is alive, or when the file holds no PID; returns when the file is gone
    or was replaced meanwhile (the caller tries again)."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LockHeld(str(path)) from None
        if not _is_at(fd, path):
            return  # released or replaced since it was opened
        pid = _pid_in(fd)
        if pid is None or _alive(pid):
            raise LockHeld(str(path))
        # This process holds the flock of the file at `path`: nobody else can
        # remove or replace it before this unlink.
        os.unlink(path)
    finally:
        os.close(fd)


def acquire(path: Path) -> IssueLock:
    """Take the lock at `path`, replacing a stale one. Raises LockHeld."""
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(_ATTEMPTS):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        except FileExistsError:
            _remove_if_stale(path)
            continue
        try:
            # Blocking: a process checking this new file holds its flock briefly,
            # sees no PID yet and lets go.
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, f"{os.getpid()}\n".encode())
        except BaseException:
            os.unlink(path)
            os.close(fd)
            raise
        return IssueLock(path, fd)
    raise LockHeld(str(path))
