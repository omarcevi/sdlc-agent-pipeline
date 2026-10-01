"""Static checks on the Makefile: no cloud action runs by accident.

The Makefile is parsed as text. The behaviour tests at the end run `make` with stub
tools that fail, so a guard that stops working shows up as a wrong exit code and never
reaches a real cloud tool.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Targets that create, change or destroy cloud resources or spend money, or that
# read the cloud. Each must check the project first.
CLOUD_TARGETS = {
    "infra-plan",
    "infra-apply",
    "budget-plan",
    "budget-apply",
    "budget-guard-test",
    "sandbox-cloud",
    "test-cloud",
    "sweep-sandboxes",
    "deploy",
    "smoke-deployed",
    "teardown-dry-run",
    "teardown",
    "teardown-budget",
}
# Recipe lines that reach the cloud, matched after variable expansion.
CLOUD_COMMAND = re.compile(
    r"terraform|gcloud|agents-cli (infra|deploy)|sandbox_infra\.py|budget_guard_check\.py"
    r"|stage_deploy\.py (deploy|smoke)|ITP_CLOUD_TESTS"
)
# Local, free forms of those commands.
LOCAL_FORMS = re.compile(r"terraform .* fmt |stage_deploy\.py stage")


def _logical_lines() -> list[str]:
    lines: list[str] = []
    pending = ""
    for raw in (ROOT / "Makefile").read_text().splitlines():
        if raw.endswith("\\"):
            pending += raw[:-1]
            continue
        lines.append(pending + raw)
        pending = ""
    return lines


def _parse() -> tuple[
    dict[str, str], dict[str, tuple[list[str], list[str]]], list[str]
]:
    variables: dict[str, str] = {}
    targets: dict[str, tuple[list[str], list[str]]] = {}
    order: list[str] = []
    current: list[str] | None = None
    in_define = False
    for line in _logical_lines():
        if in_define:
            in_define = line.strip() != "endef"
            continue
        if line.startswith("define "):
            in_define = True
            current = None
            continue
        if line.startswith("\t"):
            if current is not None:
                current.append(line[1:])
            continue
        var = re.match(r"([A-Za-z_.][\w.]*)\s*(?:\?=|:=|=)\s*(.*)$", line)
        if var:
            variables[var.group(1)] = var.group(2).strip()
            current = None
            continue
        rule = re.match(r"([A-Za-z0-9_.\- ]+?)\s*:(?!=)\s*([^#]*)", line)
        if rule and not line.startswith("#"):
            prereqs = rule.group(2).split()
            recipe: list[str] = []
            for name in rule.group(1).split():
                targets[name] = (prereqs, recipe)
                order.append(name)
            current = recipe
            continue
        current = None
    return variables, targets, order


VARIABLES, TARGETS, ORDER = _parse()


def _expand(text: str) -> str:
    for _ in range(10):
        new = re.sub(
            r"\$\((\w+)\)",
            lambda m: VARIABLES.get(m.group(1), m.group(0)),
            text,
        )
        if new == text:
            break
        text = new
    return text.replace("$$", "$")


def _recipe(target: str) -> list[str]:
    return [_expand(line.lstrip("@-+")) for line in TARGETS[target][1]]


def _tf_lines(target: str) -> list[str]:
    return [line for line in _recipe(target) if line.startswith("terraform")]


def test_every_cloud_target_exists_and_checks_the_project_first():
    for name in CLOUD_TARGETS:
        assert name in TARGETS, name
        assert TARGETS[name][0][0] == "require-project", name
    body = " ".join(TARGETS["require-project"][1])
    assert "GOOGLE_CLOUD_PROJECT is not set" in body and "exit 2" in body


def test_the_default_target_is_not_a_cloud_target():
    goal = VARIABLES.get(".DEFAULT_GOAL") or ORDER[0]
    assert goal not in CLOUD_TARGETS
    prereqs, _ = TARGETS[goal]
    assert not set(prereqs) & CLOUD_TARGETS
    for line in _recipe(goal):
        assert not CLOUD_COMMAND.search(line), goal


def test_only_cloud_targets_run_cloud_commands():
    for name in TARGETS:
        for line in _recipe(name):
            if CLOUD_COMMAND.search(line) and not LOCAL_FORMS.search(line):
                assert name in CLOUD_TARGETS, f"{name} runs a cloud command"


def test_every_cloud_target_prints_what_it_will_do():
    for name in CLOUD_TARGETS:
        assert TARGETS[name][1][0].startswith('@echo "will: '), name


def test_terraform_apply_and_destroy_auto_approve_and_init_plan_take_no_input():
    seen = set()
    for name in TARGETS:
        for line in _tf_lines(name):
            words = line.split()
            sub = next(w for w in words[1:] if not w.startswith("-"))
            seen.add(sub)
            if sub in ("apply", "destroy"):
                assert "-auto-approve" in words, line
                assert "-input=false" not in words, line
            if sub in ("init", "plan"):
                assert "-input=false" in words, line
    assert {"init", "plan", "apply", "destroy"} <= seen


def test_budget_targets_require_the_lira_amount_and_pass_it():
    for name in ("budget-plan", "budget-apply"):
        prereqs, _ = TARGETS[name]
        assert "require-budget-try" in prereqs
        assert any(
            "-var budget_amount_try=$(BUDGET_TRY)" in line for line in _tf_lines(name)
        )
    guard = " ".join(TARGETS["require-budget-try"][1])
    assert "BUDGET_TRY" in guard and "exit 2" in guard


def test_destructive_targets_need_an_explicit_confirmation():
    for name in ("teardown", "teardown-budget"):
        assert "require-confirm" in TARGETS[name][0]
    assert "CONFIRM" in " ".join(TARGETS["require-confirm"][1])


def test_agents_cli_deploy_never_runs_outside_build_deploy():
    for name in TARGETS:
        for line in _recipe(name):
            if "agents-cli deploy" in line:
                assert "build/deploy" in line, name


def test_test_cloud_sets_the_opt_in_variable_and_shows_passed_output():
    line = _recipe("test-cloud")[-1]
    assert "ITP_CLOUD_TESTS=1" in line
    assert " -rP" in line
    assert '-m "cloud or cloud_slow"' in line


def test_teardown_runs_its_steps_in_order_and_waits_for_deletes():
    text = "\n".join(_recipe("teardown"))
    steps = [
        "sweep --all",
        "prune-templates --all",
        "delete-engine --name",
        "destroy -auto-approve",
        "gcloud storage rm -r",
        "budget root kept",
    ]
    positions = [text.index(s) for s in steps]
    assert positions == sorted(positions)
    assert text.count("$(call wait-clear,") == 2


# ---- behaviour: the guards stop before any tool runs ----


@pytest.fixture
def stub_env(tmp_path):
    for tool in ("terraform", "gcloud", "agents-cli", "uv", "docker"):
        path = tmp_path / tool
        path.write_text("#!/bin/sh\nexit 97\n")
        path.chmod(0o755)
    return {"PATH": f"{tmp_path}:/usr/bin:/bin"}


def _make(env, *args):
    return subprocess.run(
        [shutil.which("make") or "make", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["infra-plan", "GOOGLE_CLOUD_PROJECT="], "GOOGLE_CLOUD_PROJECT is not set"),
        (["deploy", "GOOGLE_CLOUD_PROJECT="], "GOOGLE_CLOUD_PROJECT is not set"),
        (
            ["budget-plan", "GOOGLE_CLOUD_PROJECT=p"],
            "BUDGET_TRY must be a whole number",
        ),
        (
            ["budget-apply", "GOOGLE_CLOUD_PROJECT=p", "BUDGET_TRY=1.5"],
            "BUDGET_TRY must be",
        ),
        (["teardown", "GOOGLE_CLOUD_PROJECT=p"], "rerun with CONFIRM=yes"),
        (["teardown-budget", "GOOGLE_CLOUD_PROJECT=p"], "rerun with CONFIRM=yes"),
    ],
)
def test_guards_exit_2_before_any_cloud_tool_runs(stub_env, args, message):
    env = dict(stub_env)
    env.pop("GOOGLE_CLOUD_PROJECT", None)
    result = _make(env, *args)
    assert result.returncode == 2, result.stdout + result.stderr
    assert message in result.stderr
    assert "will: " not in result.stdout


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
def test_the_bare_make_command_only_prints_help(stub_env):
    result = _make(stub_env)
    assert result.returncode == 0
    assert "owner approval" in result.stdout
