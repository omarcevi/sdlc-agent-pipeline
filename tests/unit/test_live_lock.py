"""The live CLI's per-issue lock (app/live_lock.py): exclusive creation with the
owner's PID, an flock held while the owner lives, exact replacement of a stale
lock, also when several processes race for it."""

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from app import live_lock
from app.live_lock import LockHeld, acquire


def _dead_pid() -> int:
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    return gone.pid


def test_the_lock_holds_the_pid_and_is_removed_on_release(tmp_path):
    path = tmp_path / "locks" / "o__r__7.lock"
    lock = acquire(path)
    assert path.read_text() == f"{os.getpid()}\n"
    assert path.stat().st_mode & 0o777 == 0o600
    lock.release()
    assert not path.exists()
    lock.release()  # twice is harmless


def test_a_held_lock_is_refused_until_it_is_released(tmp_path):
    path = tmp_path / "o__r__7.lock"
    first = acquire(path)
    # Same PID, other open file: the flock decides, not the PID.
    with pytest.raises(LockHeld):
        acquire(path)
    first.release()
    acquire(path).release()


def test_a_stale_lock_is_replaced(tmp_path):
    path = tmp_path / "o__r__7.lock"
    path.write_text(f"{_dead_pid()}\n")
    lock = acquire(path)
    assert path.read_text() == f"{os.getpid()}\n"
    lock.release()


@pytest.mark.parametrize("content", ["", "not a pid\n", "0\n"])
def test_a_lock_without_a_pid_counts_as_held(tmp_path, content):
    path = tmp_path / "o__r__7.lock"
    path.write_text(content)
    with pytest.raises(LockHeld):
        acquire(path)
    assert path.read_text() == content


def test_a_lock_whose_pid_is_alive_counts_as_held_without_its_flock(tmp_path):
    path = tmp_path / "o__r__7.lock"
    path.write_text(f"{os.getpid()}\n")  # a live PID, no flock held
    with pytest.raises(LockHeld):
        acquire(path)


def test_a_contender_that_saw_the_stale_lock_cannot_remove_its_replacement(
    tmp_path, monkeypatch
):
    # The interleaving that a check-then-unlink gets wrong: B opens the stale lock,
    # A replaces it, then B goes on with the file it opened.
    path = tmp_path / "o__r__7.lock"
    path.write_text(f"{_dead_pid()}\n")
    seen_by_b = os.open(path, os.O_RDONLY)
    a = acquire(path)
    real_open = os.open
    handed: list[int] = []

    def open_as_b(target, flags, *args):
        if Path(target) == path and flags == os.O_RDONLY and not handed:
            handed.append(seen_by_b)
            return seen_by_b
        return real_open(target, flags, *args)

    monkeypatch.setattr(live_lock.os, "open", open_as_b)
    with pytest.raises(LockHeld):
        acquire(path)
    assert handed == [seen_by_b]
    assert path.read_text() == f"{os.getpid()}\n"  # A's lock is still there
    a.release()


def test_a_lock_left_by_a_killed_process_is_replaced(tmp_path):
    path = tmp_path / "o__r__7.lock"
    holder = subprocess.Popen(
        [sys.executable, "-c", _script(path, hold_s=60)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "won"
        with pytest.raises(LockHeld):
            acquire(path)
    finally:
        holder.kill()
        holder.wait()
    lock = acquire(path)  # the killed holder's flock is gone, and its PID
    lock.release()


def _script(path: Path, *, hold_s: float, start_at: float = 0.0) -> str:
    """A process that loads app/live_lock.py on its own (no `app` import), waits
    until `start_at`, races for the lock, prints won or held, and holds it."""
    module = Path(live_lock.__file__).resolve()
    return textwrap.dedent(
        f"""
        import importlib.util, sys, time
        spec = importlib.util.spec_from_file_location("live_lock", {str(module)!r})
        lock_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lock_module)
        while time.time() < {start_at!r}:
            time.sleep(0.001)
        try:
            lock = lock_module.acquire(__import__("pathlib").Path({str(path)!r}))
        except lock_module.LockHeld:
            print("held", flush=True)
            sys.exit(0)
        print("won", flush=True)
        time.sleep({hold_s!r})
        lock.release()
        """
    )


@pytest.mark.parametrize("round_", range(3))
def test_racing_processes_replace_a_stale_lock_once(tmp_path, round_):
    path = tmp_path / "o__r__7.lock"
    path.write_text(f"{_dead_pid()}\n")
    start_at = time.time() + 0.8  # all of them start together
    racers = [
        subprocess.Popen(
            [sys.executable, "-c", _script(path, hold_s=0.5, start_at=start_at)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(6)
    ]
    results = [racer.communicate(timeout=30) for racer in racers]
    assert all(racer.returncode == 0 for racer in racers), results
    outcomes = sorted(out.strip() for out, _err in results)
    assert outcomes == ["held"] * 5 + ["won"]
    assert not path.exists()  # the winner released it
