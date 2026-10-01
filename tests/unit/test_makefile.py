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
SP_DIR = "deployment/terraform/single-project"
BUDGET_DIR = "deployment/terraform/budget"

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
# Recipe lines that reach the cloud, matched after variable expansion. `terraform` is
# the command, not a path segment such as deployment/terraform/budget.
CLOUD_COMMAND = re.compile(
    r"(?<![\w/.-])terraform(?![\w/.-])|gcloud|agents-cli (infra|deploy)"
    r"|sandbox_infra\.py|budget_guard_check\.py"
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


def test_apply_takes_only_a_saved_plan_destroy_auto_approves_nothing_asks():
    """An apply is always of the plan file the owner read (Terraform applies a saved
    plan without asking and refuses a stale one); destroys stay behind CONFIRM=yes."""
    seen = set()
    for name in TARGETS:
        for line in _tf_lines(name):
            words = line.split(";")[0].split()
            sub = next(w for w in words[1:] if not w.startswith("-"))
            seen.add(sub)
            if sub == "apply":
                assert words[-2:] == ["-input=false", "budget.tfplan"], line
                assert "-auto-approve" not in words and "-var" not in words, line
            if sub == "destroy":
                assert "-auto-approve" in words, line
                assert "-input=false" not in words, line
            if sub in ("init", "plan"):
                assert "-input=false" in words, line
    assert {"init", "plan", "apply", "destroy"} <= seen


def test_budget_plan_requires_the_lira_amount_and_passes_it():
    prereqs, _ = TARGETS["budget-plan"]
    assert "require-budget-try" in prereqs
    assert any(
        "-var budget_amount_try=$(BUDGET_TRY)" in line
        for line in _tf_lines("budget-plan")
    )
    guard = " ".join(TARGETS["require-budget-try"][1])
    assert "BUDGET_TRY" in guard and "exit 2" in guard


def test_budget_plan_saves_the_plan_and_budget_apply_applies_only_that_file():
    (plan,) = [line for line in _tf_lines("budget-plan") if " plan " in line]
    assert " -out=budget.tfplan " in plan
    (apply,) = _tf_lines("budget-apply")  # no init, no re-plan
    assert apply.split(";")[0] == (
        "terraform -chdir=deployment/terraform/budget apply -input=false budget.tfplan"
    )
    assert "require-budget-plan" in TARGETS["budget-apply"][0]
    guard = " ".join(_recipe("require-budget-plan"))
    assert "deployment/terraform/budget/budget.tfplan" in guard
    assert "run make budget-plan first" in guard and "exit 2" in guard


def test_saved_plans_are_git_ignored():
    """A plan file holds the project id."""
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", f"{BUDGET_DIR}/budget.tfplan"],
        cwd=ROOT,
        check=False,
    )
    assert ignored.returncode == 0


PINS = {
    "infra-plan": "require-same-project-infra",
    "infra-apply": "require-same-project-infra",
    "teardown-dry-run": "require-same-project-infra",
    "teardown": "require-same-project-infra",
    "budget-plan": "require-same-project-budget",
    "budget-apply": "require-same-project-budget",
    "teardown-budget": "require-same-project-budget",
}


def test_every_plan_apply_and_destroy_target_pins_its_root_to_the_project():
    for name, pin in PINS.items():
        assert pin in TARGETS[name][0], name
    assert _recipe("require-same-project-infra") == [f"$(call SAME_PROJECT,{SP_DIR})"]
    assert _recipe("require-same-project-budget") == [
        f"$(call SAME_PROJECT,{BUDGET_DIR})"
    ]
    pin = VARIABLES["SAME_PROJECT"]
    assert "output -raw project_id" in pin and "$(PROJECT)" in pin
    for root in (SP_DIR, BUDGET_DIR):
        assert 'output "project_id"' in squash((ROOT / root / "outputs.tf").read_text())


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text)


BACKED_UP = {
    "infra-apply": "agents-cli infra single-project",
    "budget-apply": "$(TF_BUDGET) apply",
    "teardown": "$(TF_SP) destroy",
    "teardown-budget": "$(TF_BUDGET) destroy",
}


def test_every_apply_and_destroy_is_backed_up_before_and_after():
    for name, command in BACKED_UP.items():
        raw = TARGETS[name][1]
        (index,) = [i for i, line in enumerate(raw) if command in line]
        assert raw[index - 1] == "@$(TF_BACKUP)", name
        assert raw[index].endswith("; $(TF_BACKUP_AFTER)"), name
    assert _recipe("tf-backup") == [_expand("$(TF_BACKUP)")]
    after = VARIABLES["TF_BACKUP_AFTER"]
    assert after.startswith("s=$$?;") and after.endswith("exit $$s")
    assert VARIABLES["TF_BACKUP_DIR"] == "$(HOME)/.local/state/issue-to-pr/tfstate"


def test_teardown_names_the_engine_from_terraform_on_every_step():
    """An exported SANDBOX_ENGINE must never redirect a teardown step."""
    engine = (
        '"$(terraform -chdir=deployment/terraform/single-project output -raw '
        'agent_runtime_resource_name)"'
    )
    for name in ("teardown", "teardown-dry-run"):
        steps = [line for line in _recipe(name) if "sandbox_infra.py" in line]
        assert len(steps) >= 3, name
        for line in steps:
            flag = "--name" if "delete-engine" in line else "--engine"
            assert f"{flag} {engine}" in line, line


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
        "tf-backup: copied",
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
            ["budget-plan", "GOOGLE_CLOUD_PROJECT=p", "BUDGET_TRY=1.5"],
            "BUDGET_TRY must be",
        ),
        (["budget-apply", "GOOGLE_CLOUD_PROJECT=p"], "run make budget-plan first"),
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


# ---- behaviour: the project pin, the saved plan and the state backup ----

STUB_TERRAFORM = """#!/bin/sh
# terraform -chdir=<root> <command...>: answers `state list` and `output -raw
# project_id` from the environment and logs every other command.
shift
case "$*" in
  "state list")
    [ -z "$STUB_STATE_UNREADABLE" ] || exit 1
    printf '%s' "$STUB_RESOURCES"; exit 0;;
  "output -raw project_id")
    [ -n "$STUB_PROJECT_ID" ] || { echo "Error: Output not found" >&2; exit 1; }
    printf '%s' "$STUB_PROJECT_ID"; exit 0;;
esac
echo "terraform $*" >> "$STUB_LOG"
exit "${STUB_EXIT:-0}"
"""


@pytest.fixture
def budget_root(tmp_path, stub_env):
    """A budget root with state and a saved plan, a stub terraform that answers
    from STUB_* variables, and a private backup directory."""
    stub = tmp_path / "terraform"
    stub.write_text(STUB_TERRAFORM)
    stub.chmod(0o755)
    root = tmp_path / "budget"
    root.mkdir()
    (root / "terraform.tfstate").write_text('{"secret": "proj-alpha"}')
    (root / "budget.tfplan").write_text("plan")
    env = {
        **stub_env,
        "HOME": str(tmp_path / "home"),
        "STUB_LOG": str(tmp_path / "terraform.log"),
        "STUB_RESOURCES": "google_pubsub_topic.budget",
        "STUB_PROJECT_ID": "proj-alpha",
    }
    args = [
        f"TF_BUDGET_DIR={root}",
        f"TF_SP_DIR={tmp_path / 'single-project'}",
        f"TF_BACKUP_DIR={tmp_path / 'backups'}",
    ]
    return env, args, tmp_path


def _terraform_log(tmp_path) -> str:
    log = tmp_path / "terraform.log"
    return log.read_text() if log.exists() else ""


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
def test_the_pin_refuses_a_root_applied_with_another_project(budget_root):
    env, args, tmp_path = budget_root
    for target in ("budget-apply", "budget-plan"):
        result = _make(
            env, target, "GOOGLE_CLOUD_PROJECT=proj-beta", "BUDGET_TRY=1", *args
        )
        assert result.returncode == 2, result.stderr
        assert "belongs to another project than GOOGLE_CLOUD_PROJECT" in result.stderr
        output = result.stdout + result.stderr
        assert "proj-alpha" not in output and "proj-beta" not in output
        assert "will: " not in result.stdout
    assert _terraform_log(tmp_path) == ""
    assert not (tmp_path / "backups").exists()


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"STUB_PROJECT_ID": ""}, "has no project_id output"),
        ({"STUB_STATE_UNREADABLE": "1"}, "cannot read the Terraform state"),
    ],
)
def test_the_pin_fails_closed_when_it_cannot_tell(budget_root, changes, message):
    env, args, tmp_path = budget_root
    result = _make(
        {**env, **changes}, "budget-apply", "GOOGLE_CLOUD_PROJECT=proj-alpha", *args
    )
    assert result.returncode == 2
    assert message in result.stderr
    assert _terraform_log(tmp_path) == ""


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
@pytest.mark.parametrize(
    "changes",
    [
        {},  # the same project
        {"STUB_RESOURCES": "", "STUB_PROJECT_ID": ""},  # state with no resources
    ],
)
def test_the_pin_passes_the_same_project_or_a_root_without_resources(
    budget_root, changes
):
    env, args, tmp_path = budget_root
    result = _make(
        {**env, **changes}, "budget-apply", "GOOGLE_CLOUD_PROJECT=proj-alpha", *args
    )
    assert result.returncode == 0, result.stderr
    assert _terraform_log(tmp_path) == "terraform apply -input=false budget.tfplan\n"


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
def test_budget_apply_needs_the_saved_plan(budget_root):
    env, args, tmp_path = budget_root
    (tmp_path / "budget" / "budget.tfplan").unlink()
    result = _make(env, "budget-apply", "GOOGLE_CLOUD_PROJECT=proj-alpha", *args)
    assert result.returncode == 2
    assert "no saved budget plan; run make budget-plan first" in result.stderr
    assert _terraform_log(tmp_path) == ""


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
def test_state_is_backed_up_before_and_after_even_a_failed_apply(budget_root):
    env, args, tmp_path = budget_root
    (tmp_path / "budget" / "terraform.tfstate.backup").write_text("older")
    result = _make(
        {**env, "STUB_EXIT": "1"},
        "budget-apply",
        "GOOGLE_CLOUD_PROJECT=proj-alpha",
        *args,
    )
    assert result.returncode != 0
    assert result.stdout.count("tf-backup: copied 2 state files") == 2
    stamps = sorted((tmp_path / "backups").iterdir())
    assert len(stamps) == 2
    for stamp in stamps:
        assert (stamp / "budget" / "terraform.tfstate").read_text() == (
            '{"secret": "proj-alpha"}'
        )


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
def test_tf_backup_copies_privately_and_prints_only_the_count(budget_root):
    env, args, tmp_path = budget_root
    single = tmp_path / "single-project"
    single.mkdir()
    (single / "terraform.tfstate").write_text("sp")
    (single / "terraform.tfstate.backup").write_text("sp-old")
    result = _make(env, "tf-backup", *args)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "tf-backup: copied 3 state files\n"
    (stamp,) = (tmp_path / "backups").iterdir()
    assert re.fullmatch(r"\d{8}T\d{6}Z", stamp.name)
    copied = sorted(p.relative_to(stamp).as_posix() for p in stamp.rglob("*"))
    assert copied == [
        "budget",
        "budget/terraform.tfstate",
        "single-project",
        "single-project/terraform.tfstate",
        "single-project/terraform.tfstate.backup",
    ]
    for path in [stamp, *stamp.rglob("*")]:
        mode = path.stat().st_mode & 0o777
        assert mode == (0o700 if path.is_dir() else 0o600), path
    assert (stamp / "single-project" / "terraform.tfstate.backup").read_text() == (
        "sp-old"
    )


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
def test_tf_backup_with_no_state_copies_nothing(budget_root):
    env, args, tmp_path = budget_root
    (tmp_path / "budget" / "terraform.tfstate").unlink()
    result = _make(env, "tf-backup", *args)
    assert result.returncode == 0
    assert result.stdout == "tf-backup: copied 0 state files\n"
    assert not (tmp_path / "backups").exists()


def _dry_run(*args: str) -> list[str]:
    result = subprocess.run(
        [shutil.which("make") or "make", "-n", *args, "GOOGLE_CLOUD_PROJECT=fake-proj"],
        cwd=ROOT,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


def _first(lines: list[str], text: str) -> int:
    return next(i for i, line in enumerate(lines) if text in line)


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
@pytest.mark.parametrize(
    ("target", "command"),
    [
        ("budget-apply", "apply -input=false budget.tfplan"),
        ("infra-apply", "agents-cli infra single-project"),
        ("teardown", "single-project destroy -auto-approve"),
        ("teardown-budget", "budget destroy -auto-approve"),
    ],
)
def test_dry_runs_show_the_pin_then_a_backup_around_the_change(target, command):
    lines = _dry_run(target, "CONFIRM=yes")
    pin = _first(lines, "output -raw project_id")
    will = _first(lines, 'echo "will: ')
    change = _first(lines, command)
    assert pin < will < change
    assert "tf-backup: copied" in lines[change - 1]
    assert "tf-backup: copied" in lines[change].split(command, 1)[1]


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
def test_billing_account_reaches_the_budget_plan_and_destroy_only_when_set():
    for target in ("budget-plan", "teardown-budget"):
        args = (target, "CONFIRM=yes", "BUDGET_TRY=1")
        lines = _dry_run(*args)
        assert not any("billing_account" in line for line in lines)
        lines = _dry_run(*args, "BILLING_ACCOUNT=AAAAAA-BBBBBB-CCCCCC")
        (line,) = [
            line
            for line in lines
            if line.startswith("terraform")
            and (" plan " in line or " destroy " in line)
        ]
        assert "-var billing_account=AAAAAA-BBBBBB-CCCCCC" in line
