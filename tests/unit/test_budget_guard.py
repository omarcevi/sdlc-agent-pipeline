"""The budget guard (deployment/terraform/budget/function): the decision rules and the
function wiring. No cloud call: guard.py is loaded by path, main.py with fake clients and
a stubbed functions_framework. The sources are compiled from text, so no __pycache__
appears in the function directory (Terraform zips it)."""

import ast
import base64
import json
import sys
import types
from pathlib import Path

import pytest

FUNCTION_DIR = (
    Path(__file__).resolve().parents[2]
    / "deployment"
    / "terraform"
    / "budget"
    / "function"
)
PROJECT = "demo-project"
BUDGET_ID = "b1b2c3d4-0000-1111-2222-333344445555"
ACCOUNT = "AAAAAA-BBBBBB-CCCCCC"
DELETE_PERMISSION = "resourcemanager.projects.deleteBillingAssignment"
GET_PERMISSION = "resourcemanager.projects.get"


def load_source(name: str):
    """Execute function/<name>.py as module `name` without writing bytecode."""
    path = FUNCTION_DIR / f"{name}.py"
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
    return module


@pytest.fixture
def guard(monkeypatch):
    module = load_source("guard")
    monkeypatch.setitem(sys.modules, "guard", module)
    return module


@pytest.fixture
def config(guard):
    return guard.GuardConfig(
        project_id=PROJECT, budget_id=BUDGET_ID, billing_account=ACCOUNT
    )


@pytest.fixture
def main(guard, monkeypatch):
    stub = types.ModuleType("functions_framework")
    stub.cloud_event = lambda function: function
    monkeypatch.setitem(sys.modules, "functions_framework", stub)
    return load_source("main")


def payload(cost=600.0, budget=500.0, currency="TRY") -> bytes:
    return json.dumps(
        {"costAmount": cost, "budgetAmount": budget, "currencyCode": currency}
    ).encode()


def real_attributes(**overrides) -> dict:
    attributes = {"budgetId": BUDGET_ID, "billingAccountId": ACCOUNT}
    attributes.update(overrides)
    return attributes


def event(data: bytes, attributes: dict, message_id: str = "m-1") -> dict:
    """CloudEvent data as Eventarc delivers a Pub/Sub message."""
    return {
        "message": {
            "data": base64.b64encode(data).decode(),
            "attributes": attributes,
            "messageId": message_id,
        },
        "subscription": "projects/p/subscriptions/s",
    }


class FakeClients:
    def __init__(
        self,
        enabled=True,
        permissions=(DELETE_PERMISSION, GET_PERMISSION),
        error=None,
        fail=(),
    ):
        self.fail = set(fail)  # with `error`: only these methods raise (default: all)
        self.enabled = enabled
        self.permissions = set(permissions)
        self.error = error
        self.calls: list[tuple] = []

    def _maybe_raise(self, name):
        if self.error and (not self.fail or name in self.fail):
            raise self.error

    def billing_enabled(self, project_id):
        self.calls.append(("billing_enabled", project_id))
        self._maybe_raise("billing_enabled")
        return self.enabled

    def disable_billing(self, project_id):
        self.calls.append(("disable_billing", project_id))
        self._maybe_raise("disable_billing")
        self.enabled = False

    def granted_permissions(self, project_id, permissions):
        self.calls.append(("granted_permissions", project_id))
        self._maybe_raise("granted_permissions")
        return {p for p in permissions if p in self.permissions}

    def names(self):
        return [call[0] for call in self.calls]


def run(main, config, data, attributes, clients=None, message_id="m-1"):
    clients = clients or FakeClients()
    lines: list[dict] = []
    result = main.handle(
        event(data, attributes, message_id), config, clients, lines.append
    )
    return result, lines, clients


# --- guard.decide ---------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not json",
        b"[1, 2]",
        b'{"costAmount": 1, "budgetAmount": 2}',
        b'{"costAmount": "1", "budgetAmount": 2, "currencyCode": "TRY"}',
        b'{"costAmount": true, "budgetAmount": 2, "currencyCode": "TRY"}',
        b'{"costAmount": NaN, "budgetAmount": 2, "currencyCode": "TRY"}',
        b'{"costAmount": 1, "budgetAmount": 2, "currencyCode": ""}',
        b"\xff\xfe",
    ],
)
def test_malformed_payload_is_ignored(guard, config, data):
    decision = guard.decide(data, real_attributes(), config)
    assert (decision.action, decision.reason) == ("ignore", "malformed")


def test_other_budget_is_ignored(guard, config):
    decision = guard.decide(payload(), real_attributes(budgetId="someone-else"), config)
    assert (decision.action, decision.reason) == ("ignore", "other budget")
    assert decision.cost == 600.0


def test_missing_budget_id_is_ignored(guard, config):
    decision = guard.decide(payload(), {"billingAccountId": ACCOUNT}, config)
    assert decision.reason == "other budget"


def test_below_budget_does_nothing(guard, config):
    decision = guard.decide(payload(499.99, 500), real_attributes(), config)
    assert (decision.action, decision.reason) == ("none", "below budget")
    assert (decision.cost, decision.budget, decision.currency) == (499.99, 500, "TRY")


def test_cost_equal_to_budget_acts(guard, config):
    decision = guard.decide(payload(500, 500), real_attributes(), config)
    assert (decision.action, decision.reason) == ("disable", "cost at or above budget")


def test_dry_run_never_disables(guard, config):
    attributes = real_attributes(itp_dry_run="1")
    decision = guard.decide(payload(), attributes, config)
    assert (decision.action, decision.reason) == ("dry_run", "dry run")
    without_account = {"budgetId": BUDGET_ID, "itp_dry_run": "1"}
    assert guard.decide(payload(), without_account, config).action == "dry_run"


def test_dry_run_below_budget_is_none(guard, config):
    decision = guard.decide(payload(1, 500), real_attributes(itp_dry_run="1"), config)
    assert decision.action == "none"


def test_missing_billing_account_never_disables(guard, config):
    decision = guard.decide(payload(), {"budgetId": BUDGET_ID}, config)
    assert (decision.action, decision.reason) == ("ignore", "not from Cloud Billing")


def test_wrong_billing_account_never_disables(guard, config):
    decision = guard.decide(
        payload(), real_attributes(billingAccountId="X-Y-Z"), config
    )
    assert (decision.action, decision.reason) == ("ignore", "not from Cloud Billing")


def test_real_over_budget_message_disables(guard, config):
    decision = guard.decide(payload(), real_attributes(), config)
    assert decision.action == "disable"
    assert (decision.cost, decision.budget, decision.currency) == (600.0, 500.0, "TRY")


def test_dry_run_attribute_must_be_exactly_one(guard, config):
    # Only "1" is a dry run; anything else falls through to the account check.
    decision = guard.decide(payload(), real_attributes(itp_dry_run="0"), config)
    assert decision.action == "disable"


def test_config_from_env_names_and_formats(guard):
    env = {
        "GUARD_PROJECT_ID": PROJECT,
        "GUARD_BUDGET_ID": BUDGET_ID,
        "GUARD_BILLING_ACCOUNT": ACCOUNT,
    }
    config = guard.GuardConfig.from_env(env)
    assert (config.project_id, config.budget_id, config.billing_account) == (
        PROJECT,
        BUDGET_ID,
        ACCOUNT,
    )
    # Resource-name forms are reduced to the bare ids Cloud Billing puts in attributes.
    env["GUARD_BUDGET_ID"] = f"billingAccounts/{ACCOUNT}/budgets/{BUDGET_ID}"
    env["GUARD_BILLING_ACCOUNT"] = f"billingAccounts/{ACCOUNT}"
    config = guard.GuardConfig.from_env(env)
    assert (config.budget_id, config.billing_account) == (BUDGET_ID, ACCOUNT)


@pytest.mark.parametrize(
    "missing", ["GUARD_PROJECT_ID", "GUARD_BUDGET_ID", "GUARD_BILLING_ACCOUNT"]
)
def test_config_from_env_requires_every_variable(guard, missing):
    env = {
        "GUARD_PROJECT_ID": PROJECT,
        "GUARD_BUDGET_ID": BUDGET_ID,
        "GUARD_BILLING_ACCOUNT": ACCOUNT,
    }
    env[missing] = " "
    with pytest.raises(ValueError, match=missing):
        guard.GuardConfig.from_env(env)


def test_guard_imports_only_the_standard_library():
    tree = ast.parse((FUNCTION_DIR / "guard.py").read_text())
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            roots.add((node.module or "").split(".")[0])
    assert roots <= set(sys.stdlib_module_names), roots


def test_function_directory_has_no_bytecode():
    assert not list(FUNCTION_DIR.rglob("__pycache__"))
    assert not list(FUNCTION_DIR.rglob("*.pyc"))


# --- main.handle ------------------------------------------------------------------


def test_dry_run_never_calls_update_billing_info(main, config):
    _, lines, clients = run(main, config, payload(), real_attributes(itp_dry_run="1"))
    assert clients.names() == ["granted_permissions", "billing_enabled"]
    line = lines[0]
    assert (line["decision"], line["result"]) == ("dry_run", "dry run")
    assert line["billing_enabled"] is True
    assert line["permissions"] == {DELETE_PERMISSION: True, GET_PERMISSION: True}
    assert "would disable billing" in line["message"]
    assert PROJECT in line["message"]


def test_dry_run_reports_a_missing_permission(main, config):
    clients = FakeClients(permissions=(DELETE_PERMISSION,))
    _, lines, _ = run(
        main, config, payload(), real_attributes(itp_dry_run="1"), clients
    )
    assert lines[0]["permissions"] == {DELETE_PERMISSION: True, GET_PERMISSION: False}


def test_real_path_disables_once(main, config):
    _, lines, clients = run(main, config, payload(), real_attributes())
    assert clients.names().count("disable_billing") == 1
    assert clients.enabled is False
    assert (lines[0]["decision"], lines[0]["result"]) == ("disable", "billing disabled")
    assert lines[0]["message"].startswith(f"billing disabled on {PROJECT}")


def test_already_disabled_project_is_left_alone(main, config):
    clients = FakeClients(enabled=False)
    _, lines, _ = run(main, config, payload(), real_attributes(), clients)
    assert "disable_billing" not in clients.names()
    assert lines[0]["result"] == "already disabled"


def test_running_twice_changes_nothing_more(main, config):
    clients = FakeClients()
    run(main, config, payload(), real_attributes(), clients)
    run(main, config, payload(), real_attributes(), clients)
    assert clients.names().count("disable_billing") == 1


def test_api_error_is_logged_and_raised(main, config):
    clients = FakeClients(error=RuntimeError("quota"))
    lines: list[dict] = []
    with pytest.raises(RuntimeError, match="quota"):
        main.handle(event(payload(), real_attributes()), config, clients, lines.append)
    assert len(lines) == 1
    assert (lines[0]["decision"], lines[0]["result"], lines[0]["severity"]) == (
        "disable",
        "error",
        "ERROR",
    )


def test_ignore_and_none_call_no_client(main, config):
    for data, attributes in [
        (b"junk", real_attributes()),
        (payload(), real_attributes(budgetId="other")),
        (payload(1, 500), real_attributes()),
        (payload(), {"budgetId": BUDGET_ID}),
    ]:
        _, _, clients = run(main, config, data, attributes)
        assert clients.calls == []


def test_each_message_writes_exactly_one_log_line(main, config):
    cases = [
        (b"junk", real_attributes()),
        (payload(), real_attributes(budgetId="other")),
        (payload(1, 500), real_attributes()),
        (payload(), real_attributes(itp_dry_run="1")),
        (payload(), {"budgetId": BUDGET_ID}),
        (payload(), real_attributes()),
    ]
    for data, attributes in cases:
        _, lines, _ = run(main, config, data, attributes)
        assert len(lines) == 1
        assert set(lines[0]) >= {
            "severity",
            "decision",
            "reason",
            "cost",
            "budget",
            "currency",
            "budget_id",
            "message_id",
            "result",
        }
        json.dumps(lines[0])


def test_log_lines_hold_no_credentials(main, config):
    secret = "ya29.SECRET-TOKEN"
    attributes = real_attributes(authorization=secret)
    data = json.dumps(
        {"costAmount": 600, "budgetAmount": 500, "currencyCode": "TRY", "token": secret}
    ).encode()
    _, lines, _ = run(main, config, data, attributes)
    assert secret not in json.dumps(lines)


def test_eventarc_shaped_messages_end_to_end(main, config):
    """All six outcomes, with the CloudEvent data shape Eventarc delivers."""
    cases = {
        "ignored": (b"junk", real_attributes(), True),
        "no action": (payload(1, 500), real_attributes(), True),
        "dry run": (payload(), real_attributes(itp_dry_run="1"), True),
        "already disabled": (payload(), real_attributes(), False),
        "billing disabled": (payload(), real_attributes(), True),
    }
    for expected, (data, attributes, enabled) in cases.items():
        clients = FakeClients(enabled=enabled)
        result, lines, _ = run(
            main, config, data, attributes, clients, message_id="evt-9"
        )
        assert lines[0]["result"] == expected
        assert lines[0]["message_id"] == "evt-9"
        assert result == lines[0]
    clients = FakeClients(error=RuntimeError("boom"))
    lines: list[dict] = []
    cloud_event = types.SimpleNamespace(
        data=event(payload(), real_attributes(), "evt-9")
    )
    with pytest.raises(RuntimeError):
        main.handle(cloud_event.data, config, clients, lines.append)
    assert lines[0]["result"] == "error"


def test_stop_billing_reads_config_from_env(main, monkeypatch, capsys):
    monkeypatch.setenv("GUARD_PROJECT_ID", PROJECT)
    monkeypatch.setenv("GUARD_BUDGET_ID", BUDGET_ID)
    monkeypatch.setenv("GUARD_BILLING_ACCOUNT", ACCOUNT)
    clients = FakeClients()
    monkeypatch.setattr(main, "make_clients", lambda: clients)
    cloud_event = types.SimpleNamespace(
        data=event(payload(), real_attributes(itp_dry_run="1"), "evt-1")
    )
    assert main.stop_billing(cloud_event) is None
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(lines) == 1
    assert lines[0]["decision"] == "dry_run"
    assert "disable_billing" not in clients.names()


# --- fix round 1 ---------------------------------------------------------------


def test_dry_run_shows_get_false_when_get_billing_info_is_denied(main, config):
    clients = FakeClients(
        permissions=(DELETE_PERMISSION,),
        error=RuntimeError("denied"),
        fail=("billing_enabled",),
    )
    _, lines, _ = run(
        main, config, payload(), real_attributes(itp_dry_run="1"), clients
    )
    line = lines[0]
    assert line["result"] == "dry run"
    assert line["billing_enabled"] is None
    assert line["permissions"] == {DELETE_PERMISSION: True, GET_PERMISSION: False}
    assert "disable_billing" not in clients.names()


def test_real_message_disables_when_billing_status_is_unreadable(main, config):
    clients = FakeClients(error=RuntimeError("denied"), fail=("billing_enabled",))
    _, lines, _ = run(main, config, payload(), real_attributes(), clients)
    assert clients.names() == ["billing_enabled", "disable_billing"]
    assert lines[0]["result"] == "billing disabled"
    assert lines[0]["billing_enabled"] is None
    assert "could not be read" in lines[0]["message"]


def test_disable_billing_error_is_logged_and_raised_after_enabled_read(main, config):
    clients = FakeClients(error=RuntimeError("nope"), fail=("disable_billing",))
    lines: list[dict] = []
    with pytest.raises(RuntimeError, match="nope"):
        main.handle(event(payload(), real_attributes()), config, clients, lines.append)
    assert clients.names() == ["billing_enabled", "disable_billing"]
    assert len(lines) == 1
    assert lines[0]["result"] == "error"


def test_dry_run_client_error_is_logged_and_acknowledged(main, config):
    """Never re-raised: Eventarc would retry the test message for up to a day."""
    clients = FakeClients(error=RuntimeError("iam"), fail=("granted_permissions",))
    lines: list[dict] = []
    attributes = real_attributes(itp_dry_run="1")
    result = main.handle(event(payload(), attributes), config, clients, lines.append)
    assert lines == [result]
    assert (result["decision"], result["result"], result["severity"]) == (
        "dry_run",
        "error",
        "ERROR",
    )
    assert result["error"] == "RuntimeError"
    assert "iam" not in json.dumps(result)
    assert "disable_billing" not in clients.names()


@pytest.mark.parametrize("value", [10**400, -(10**400)])
def test_huge_integer_amounts_are_malformed(guard, config, value):
    for cost, budget in [(value, 5), (5, value)]:
        data = json.dumps(
            {"costAmount": cost, "budgetAmount": budget, "currencyCode": "TRY"}
        ).encode()
        assert guard.decide(data, real_attributes(), config).reason == "malformed"


def test_huge_integer_message_writes_one_log_line(main, config):
    data = (
        '{"costAmount": ' + "9" * 400 + ', "budgetAmount": 5, "currencyCode": "TRY"}'
    ).encode()
    _, lines, _ = run(main, config, data, real_attributes())
    assert len(lines) == 1
    assert lines[0]["result"] == "ignored"


@pytest.mark.parametrize("budget", [0, -5])
def test_non_positive_budget_is_malformed(guard, config, budget):
    decision = guard.decide(payload(10, budget), real_attributes(), config)
    assert (decision.action, decision.reason) == ("ignore", "malformed")


@pytest.mark.parametrize(
    "name,value",
    [
        ("GUARD_BUDGET_ID", "billingAccounts/A/budgets/"),
        ("GUARD_BILLING_ACCOUNT", "billingAccounts/"),
        ("GUARD_BUDGET_ID", "/"),
    ],
)
def test_ids_blank_after_reduction_are_refused(guard, name, value):
    env = {
        "GUARD_PROJECT_ID": PROJECT,
        "GUARD_BUDGET_ID": BUDGET_ID,
        "GUARD_BILLING_ACCOUNT": ACCOUNT,
        name: value,
    }
    with pytest.raises(ValueError, match=name):
        guard.GuardConfig.from_env(env)


def test_every_log_line_says_whether_the_account_matched(main, config):
    _, lines, _ = run(main, config, payload(1, 500), real_attributes())
    assert lines[0]["account_matches"] is True
    _, lines, _ = run(main, config, payload(1, 500), {"budgetId": BUDGET_ID})
    assert lines[0]["account_matches"] is False
    assert ACCOUNT not in json.dumps(lines)


def test_clients_are_built_lazily_and_once(main, monkeypatch):
    monkeypatch.setenv("GUARD_PROJECT_ID", PROJECT)
    monkeypatch.setenv("GUARD_BUDGET_ID", BUDGET_ID)
    monkeypatch.setenv("GUARD_BILLING_ACCOUNT", ACCOUNT)
    built = []
    clients = FakeClients()
    monkeypatch.setattr(main, "make_clients", lambda: built.append(1) or clients)
    main.stop_billing(types.SimpleNamespace(data=event(b"junk", real_attributes())))
    assert built == []
    dry = real_attributes(itp_dry_run="1")
    main.stop_billing(types.SimpleNamespace(data=event(payload(), dry)))
    assert built == [1]
    lazy = main.LazyClients()
    lazy.billing_enabled(PROJECT)
    lazy.billing_enabled(PROJECT)
    assert len(built) == 2
