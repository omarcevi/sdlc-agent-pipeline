"""scripts/budget_guard_check.py: the dry-run wiring check. No gcloud, no terraform:
the runner and the clock are fakes."""

import importlib.util
import json
import subprocess
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "budget_guard_check.py"
FUNCTION_DIR = ROOT / "deployment" / "terraform" / "budget" / "function"
BUDGET_ID = "b1b2c3d4-0000-1111-2222-333344445555"
ACCOUNT = "AAAAAA-BBBBBB-CCCCCC"
PERMISSIONS = {
    "resourcemanager.projects.deleteBillingAssignment": True,
    "resourcemanager.projects.get": True,
}


@pytest.fixture
def check():
    spec = importlib.util.spec_from_file_location("budget_guard_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_guard():
    path = FUNCTION_DIR / "guard.py"
    module = types.ModuleType("guard")
    exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
    return module


def test_build_test_message_always_sets_dry_run_and_never_billing_account(check):
    message, attributes = check.build_test_message(BUDGET_ID, 1234.0)
    body = json.loads(message)
    assert body["costAmount"] == body["budgetAmount"] == 1234.0
    assert body["currencyCode"] == "TRY"
    assert attributes == {"budgetId": BUDGET_ID, "itp_dry_run": "1"}
    assert "billingAccountId" not in attributes
    assert "billingAccountId" not in message


def test_publish_args_are_exact(check):
    args = check.publish_args(
        "issue-to-pr-budget", '{"a": 1}', {"budgetId": "b", "itp_dry_run": "1"}, "proj"
    )
    assert args == [
        "gcloud",
        "pubsub",
        "topics",
        "publish",
        "issue-to-pr-budget",
        "--project",
        "proj",
        "--message",
        '{"a": 1}',
        "--attribute",
        "budgetId=b,itp_dry_run=1",
        "--format",
        "json",
    ]


def test_passed_needs_dry_run_and_both_permissions(check):
    good = {
        "decision": "dry_run",
        "billing_enabled": True,
        "permissions": dict(PERMISSIONS),
    }
    assert check.passed(good) is True
    assert check.passed({**good, "decision": "disable"}) is False
    assert check.passed({**good, "decision": "none"}) is False
    for name in PERMISSIONS:
        assert (
            check.passed({**good, "permissions": {**PERMISSIONS, name: False}}) is False
        )
    assert check.passed({"decision": "dry_run"}) is False
    assert check.passed({"decision": "dry_run", "permissions": {}}) is False


@pytest.mark.parametrize("enabled", [False, None, "true", 1, "missing"])
def test_passed_needs_billing_enabled_to_be_true(check, enabled):
    """Plan Task 10 Step 6: the dry run must also see billing enabled."""
    line = {"decision": "dry_run", "permissions": dict(PERMISSIONS)}
    if enabled != "missing":
        line["billing_enabled"] = enabled
    assert check.passed(line) is False


class Gcloud:
    """Fake runner: records commands, answers publish and logging read."""

    def __init__(self, log_lines):
        self.log_lines = log_lines
        self.commands: list[list[str]] = []

    def __call__(self, command):
        self.commands.append(command)
        if "publish" in command:
            out = json.dumps({"messageIds": ["4242"]})
        else:
            out = json.dumps([{"jsonPayload": line} for line in self.log_lines])
        return subprocess.CompletedProcess(command, 0, out, "")


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def run_check(check, runner, timeout_s=60):
    clock = Clock()
    return check.run_check(
        runner,
        "topic",
        BUDGET_ID,
        1000.0,
        "proj",
        timeout_s,
        clock.sleep,
        clock.monotonic,
    )


def test_check_fails_when_no_log_line_arrives(check):
    runner = Gcloud([])
    code, line = run_check(check, runner)
    assert (code, line) == (1, None)
    assert sum("publish" in c for c in runner.commands) == 1
    reads = [c for c in runner.commands if "read" in c]
    assert len(reads) >= 2
    assert any("4242" in part for part in reads[0])


def test_check_passes_on_a_good_dry_run_line(check):
    line = {
        "decision": "dry_run",
        "message_id": "4242",
        "billing_enabled": True,
        "permissions": dict(PERMISSIONS),
    }
    code, found = run_check(check, Gcloud([line]))
    assert (code, found) == (0, line)


def test_check_fails_on_a_missing_permission(check):
    line = {
        "decision": "dry_run",
        "permissions": {**PERMISSIONS, "resourcemanager.projects.get": False},
    }
    code, found = run_check(check, Gcloud([line]))
    assert code == 1
    assert found == line


def test_script_has_no_option_that_drops_the_dry_run_attribute(check):
    parser = check.build_parser()
    options = {o for action in parser._actions for o in action.option_strings}
    assert options == {"-h", "--help", "--timeout-s"}


def test_check_script_message_cannot_disable_billing(check):
    guard = load_guard()
    config = guard.GuardConfig(
        project_id="p", budget_id=BUDGET_ID, billing_account=ACCOUNT
    )
    message, attributes = check.build_test_message(BUDGET_ID, 1234.0)
    assert guard.decide(message.encode(), attributes, config).action == "dry_run"
    stripped = {k: v for k, v in attributes.items() if k != "itp_dry_run"}
    assert guard.decide(message.encode(), stripped, config).action == "ignore"


def test_check_surfaces_a_failed_log_read(check, capsys):
    class Failing(Gcloud):
        def __call__(self, command):
            if "read" in command:
                self.commands.append(command)
                return subprocess.CompletedProcess(
                    command, 1, "", "PERMISSION_DENIED logging"
                )
            return super().__call__(command)

    code, line = run_check(check, Failing([]))
    assert (code, line) == (1, None)
    assert "PERMISSION_DENIED logging" in capsys.readouterr().err


def test_constants_match_the_guard(check):
    guard = load_guard()
    assert check.PERMISSIONS == guard.PERMISSIONS
    assert check.DRY_RUN_ATTRIBUTE == guard.DRY_RUN_ATTRIBUTE


@pytest.mark.parametrize("bad", ["x,billingAccountId=ACC", "a b", "", "a=b"])
def test_budget_id_with_odd_characters_is_refused(check, bad):
    with pytest.raises(ValueError):
        check.build_test_message(bad, 1.0)
