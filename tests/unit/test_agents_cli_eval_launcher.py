"""The agents-cli eval launcher (scripts/agents_cli_eval.py): it raises agents-cli's
per-read timeout on /run_sse before the tool's main() runs. No agents-cli process and
no model: the tool's two modules are stand-ins."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "agents_cli_eval.py"


def load_launcher():
    spec = importlib.util.spec_from_file_location("agents_cli_eval", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tool(monkeypatch):
    """Stand-ins for google.agents.cli._adk_client and google.agents.cli.main."""
    seen: dict = {}
    client = types.ModuleType("google.agents.cli._adk_client")
    client._RUN_SSE_TIMEOUT = 120
    cli = types.ModuleType("google.agents.cli.main")

    def fake_main():
        seen["timeout"] = client._RUN_SSE_TIMEOUT
        seen["argv"] = list(sys.argv)
        return 0

    cli.main = fake_main
    for name in ("google.agents", "google.agents.cli"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, client.__name__, client)
    monkeypatch.setitem(sys.modules, cli.__name__, cli)
    return seen


def test_the_timeout_is_set_before_main_runs(tool, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["unchanged"])
    launcher = load_launcher()
    assert launcher.main(["eval", "run", "--concurrency", "2"]) == 0
    assert tool["timeout"] == (30, 1800)  # connect, per-read
    assert tool["argv"] == ["agents-cli", "eval", "run", "--concurrency", "2"]


def test_without_the_tool_it_runs_the_tools_own_interpreter(monkeypatch):
    # Not importable here (a bare interpreter): the launcher starts the interpreter
    # named by agents-cli's shebang with itself, and never returns.
    monkeypatch.setitem(sys.modules, "google.agents.cli._adk_client", None)
    launcher = load_launcher()
    launched = []
    monkeypatch.setattr(launcher, "_tool_python", lambda: "/tool/python3")
    monkeypatch.setattr(launcher.os, "execv", lambda *a: launched.append(a))
    launcher.main(["eval", "run"])
    [(program, argv)] = launched
    assert program == "/tool/python3"
    assert argv == ["/tool/python3", str(SCRIPT), "eval", "run"]


def test_the_tool_interpreter_comes_from_the_shebang(tmp_path, monkeypatch):
    launcher = load_launcher()
    script = tmp_path / "agents-cli"
    script.write_text("#!/opt/tool/bin/python3\nimport sys\n")
    monkeypatch.setattr(launcher.shutil, "which", lambda name: str(script))
    assert launcher._tool_python() == "/opt/tool/bin/python3"
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit):
        launcher._tool_python()


def test_a_second_import_failure_is_raised_not_re_executed(monkeypatch):
    # Under the tool's own interpreter the import still fails (a broken tool
    # environment): re-executing again would loop forever, so the error surfaces.
    monkeypatch.setitem(sys.modules, "google.agents.cli._adk_client", None)
    monkeypatch.setenv("ISSUE_TO_PR_AGENTS_CLI_REEXEC", "1")
    launcher = load_launcher()
    launched = []
    monkeypatch.setattr(launcher, "_tool_python", lambda: "/tool/python3")
    monkeypatch.setattr(launcher.os, "execv", lambda *a: launched.append(a))
    with pytest.raises(ImportError):
        launcher.main(["eval", "run"])
    assert launched == []


def test_the_first_re_exec_marks_itself(monkeypatch):
    monkeypatch.setitem(sys.modules, "google.agents.cli._adk_client", None)
    monkeypatch.delenv("ISSUE_TO_PR_AGENTS_CLI_REEXEC", raising=False)
    launcher = load_launcher()
    seen = []
    monkeypatch.setattr(launcher, "_tool_python", lambda: "/tool/python3")
    monkeypatch.setattr(
        launcher.os,
        "execv",
        lambda *a: seen.append(
            launcher.os.environ.get("ISSUE_TO_PR_AGENTS_CLI_REEXEC")
        ),
    )
    launcher.main(["eval", "run"])
    assert seen == ["1"]
