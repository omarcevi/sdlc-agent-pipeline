"""Host-side git never starts automatic housekeeping.

Git 2.50 on GitHub's runners can run maintenance in the background after a commit.
It writes `.git/objects/maintenance.lock` while our code copies or deletes the
repository, which failed a CI run on 2026-10-02 (`shutil.Error` on the lock).
"""

import subprocess
from pathlib import Path

from app.nodes import finish
from bench import demo, probes

NO_AUTO = {"maintenance.auto": "false", "gc.auto": "0"}


def _capture(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def fake_run(cmd, **kwargs):
        calls.append({"cmd": list(cmd), "env": dict(kwargs.get("env") or {})})
        return subprocess.CompletedProcess(
            cmd, 0, stdout="" if kwargs.get("text") else b""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def _settings_from_args(cmd: list[str]) -> dict[str, str]:
    pairs = [cmd[i + 1] for i, a in enumerate(cmd[:-1]) if a == "-c"]
    return dict(p.split("=", 1) for p in pairs)


def _settings_from_env(env: dict[str, str]) -> dict[str, str]:
    n = int(env.get("GIT_CONFIG_COUNT", "0"))
    return {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(n)}


def test_delivery_git_turns_off_automatic_maintenance(monkeypatch, tmp_path: Path):
    calls = _capture(monkeypatch)
    finish._git(["status"], cwd=tmp_path, home=tmp_path)
    settings = _settings_from_env(calls[0]["env"])
    assert NO_AUTO.items() <= settings.items()
    assert settings["safe.bareRepository"] == "explicit"


def test_demo_git_turns_off_automatic_maintenance(monkeypatch, tmp_path: Path):
    calls = _capture(monkeypatch)
    demo._git("status", cwd=tmp_path)
    assert NO_AUTO.items() <= _settings_from_args(calls[0]["cmd"]).items()


def test_probe_git_turns_off_automatic_maintenance(monkeypatch, tmp_path: Path):
    calls = _capture(monkeypatch)
    probes._git(tmp_path, "status")
    assert NO_AUTO.items() <= _settings_from_args(calls[0]["cmd"]).items()
