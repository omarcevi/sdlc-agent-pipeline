"""The replay converter: recorded events to one scrubbed replay (design section 4 and 5).

Every log here is synthetic, built from real ADK Events (tests/unit/replay_events.py).
"""

import json
from pathlib import Path

import pytest
from google.adk.events import Event
from google.genai import types

import bench.replay as replay
from app.approval import _escaped
from app.driver import PUBLIC_AGENT, PUBLIC_CRASH, PUBLIC_INFRA
from app.task_store import load_task
from bench.progress import call_detail, format_event
from bench.replay_check import scan_value
from tests.fakes import make_bench_task
from tests.unit import replay_events as ev
from tests.unit.replay_events import Log, cap_run, multi_run, single_run

REPO_ROOT = Path("/Users/tester/work/sdlc")
HOME = Path("/Users/tester")
CAPS = {"cost_usd": 1.0, "tool_calls": 75, "wall_clock_s": 3000}


@pytest.fixture(autouse=True)
def _no_exact_values(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("REPLAY_REDACT", raising=False)


@pytest.fixture(scope="module")
def graphs():
    return replay.export_graphs()


@pytest.fixture(scope="module")
def task(tmp_path_factory):
    root = tmp_path_factory.mktemp("bench")
    make_bench_task(root, ev.TASK_ID)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("BENCH_TASKS_DIR", str(root / "tasks"))
        return load_task(ev.TASK_ID)


@pytest.fixture
def convert(tmp_path, task, graphs):
    def run(
        log: Log,
        *,
        record=None,
        row=None,
        system=None,
        project=None,
        redact=(),
        allow=(),
        edit=None,
    ) -> dict:
        system = system or ("single" if "-single-" in log.run_id else "multi")
        path = log.write(tmp_path, edit)
        record = record or log.record()
        row = row if row is not None else log.row(record, system=system)
        inputs = replay.RunInputs(
            run_id=record.run_id,
            events_path=path,
            record=record,
            row=row,
            task=task,
            caps=dict(CAPS),
            allow=tuple(allow),
        )
        return replay.convert_run(
            inputs,
            graphs=graphs,
            project=project,
            redact=redact,
            repo_root=REPO_ROOT,
            home=HOME,
        )

    return run


def kind(out: dict, name: str) -> list[dict]:
    return [s for s in out["steps"] if s["kind"] == name]


# --- mapping ----------------------------------------------------------------------


def test_function_node_visit_becomes_a_node_step_with_its_status_line(convert):
    out = convert(multi_run())
    nodes = kind(out, "node")
    assert [s["node"] for s in nodes] == [
        "fetch_issue",
        "provision_sandbox",
        "planner",
        "route_plan",
        "coder",
        "collect_diff",
        "run_tests",
        "reviewer",
        "route_review",
        "deliver_patch",
    ]
    assert nodes[0] == {
        "i": 0,
        "t": 0.0,
        "kind": "node",
        "node": "fetch_issue",
        "visit": 1,
        "cost_usd": 0.0,
        "tool_calls": 0,
        "from": None,
        "via": None,
        "message": "issue: add is broken",
    }
    assert nodes[1]["message"] == "sandbox <sandbox> ready"
    assert nodes[1]["t"] == 1.25
    by_node = {s["node"]: s for s in nodes}
    assert by_node["deliver_patch"]["message"] == "patch written to patch.diff"
    assert by_node["planner"]["message"] is None
    assert by_node["route_plan"]["message"] is None


def test_from_and_via_follow_the_previous_visit_and_its_route(convert):
    nodes = kind(convert(multi_run()), "node")
    assert [(s["from"], s["via"], s["node"]) for s in nodes] == [
        (None, None, "fetch_issue"),
        ("fetch_issue", None, "provision_sandbox"),
        ("provision_sandbox", None, "planner"),
        ("planner", None, "route_plan"),
        ("route_plan", "actionable", "coder"),
        ("coder", None, "collect_diff"),
        ("collect_diff", None, "run_tests"),
        ("run_tests", "pass", "reviewer"),
        ("reviewer", None, "route_review"),
        ("route_review", "approve", "deliver_patch"),
    ]


def _fix_loop() -> Log:
    """run_tests fails once, so the coder and the checks run a second time."""
    log = Log()
    log.start()
    log.answer("planner", "plan", ev.PLAN)
    log.router("route_plan", "actionable")
    log.tool("coder", "edit_file", {"path": "mini.py"}, {"ok": True, "path": "mini.py"})
    log.answer("coder", "patch", ev.PATCH)
    log.verify(test_report=ev.FAILED_TESTS, route="fail")
    log.tool(
        "coder",
        "edit_file",
        {"path": "mini.py"},
        {"ok": True, "path": "mini.py"},
        visit=2,
    )
    log.answer("coder", "patch", ev.PATCH, visit=2)
    log.verify(visit=2)
    log.answer("reviewer", "review", ev.REVIEW)
    log.router("route_review", "approve")
    log.deliver()
    return log


def test_second_visit_counts_visits(convert):
    log = _fix_loop()
    out = convert(log, record=log.record(test_attempts=1))
    nodes = kind(out, "node")
    visits = [(s["node"], s["visit"], s["from"], s["via"]) for s in nodes]
    assert ("coder", 2, "run_tests", "fail") in visits
    assert ("collect_diff", 2, "coder", None) in visits
    assert ("run_tests", 2, "collect_diff", None) in visits
    assert ("reviewer", 1, "run_tests", "pass") in visits
    second = next(s["i"] for s in nodes if (s["node"], s["visit"]) == ("coder", 2))
    following = out["steps"][second + 1 : second + 5]
    assert [s["kind"] for s in following] == [
        "model_call",
        "tool_result",
        "model_call",
        "claim",
    ]
    assert {(s["node"], s["visit"]) for s in following} == {("coder", 2)}


def test_model_call_renumbers_call_ids_and_labels_them(convert):
    log = Log()
    log.start()
    first, second = log.model_call(
        "planner",
        ("list_dir", {"path": "."}),
        ("grep", {"pattern": "add", "path": "mini.py"}),
    )
    log.tool_result("planner", first, "list_dir", {"entries": ["mini.py"]})
    log.tool_result("planner", second, "grep", {"matches": ["mini.py:1:def add"]})
    log.tool("planner", "bash", {"command": "ls\n  -la"}, {"exit_code": 0})
    log.answer("planner", "plan", ev.PLAN)
    out = convert(log)
    calls = [c for s in kind(out, "model_call") for c in s["calls"]]
    assert [c["id"] for c in calls] == ["c1", "c2", "c3", "c4"]
    assert [c["tool"] for c in calls] == [
        "list_dir",
        "grep",
        "bash",
        "set_model_response",
    ]
    assert [c["label"] for c in calls] == [
        "list_dir .",
        "grep 'add' in mini.py",
        "bash ls -la",
        "answer",
    ]
    assert calls[0] == {
        "id": "c1",
        "tool": "list_dir",
        "label": "list_dir .",
        "args": {"path": "."},
    }
    assert [s["call"] for s in kind(out, "tool_result")] == ["c1", "c2", "c3"]
    assert "adk-call" not in replay.dumps(out)


def test_tokens_out_counts_candidates_and_thoughts(convert):
    log = Log()
    log.start()
    (call,) = log.model_call(
        "planner",
        ("list_dir", {"path": "."}),
        tokens=(914, 28, 100),
        thought="private reasoning",
        text="Let me look around.",
    )
    log.tool_result("planner", call, "list_dir", {"entries": []})
    out = convert(log)
    (step,) = kind(out, "model_call")
    assert step["agent"] == "planner"
    assert step["model"] == ev.MODEL
    assert step["tokens_in"] == 914
    assert step["tokens_out"] == 128
    assert step["text"] == "Let me look around."
    assert "private reasoning" not in replay.dumps(out)
    assert list(step) == [
        "i",
        "t",
        "kind",
        "node",
        "visit",
        "cost_usd",
        "tool_calls",
        "agent",
        "model",
        "tokens_in",
        "tokens_out",
        "text",
        "calls",
    ]


def test_tool_result_carries_error_and_skips_set_model_response(convert):
    log = Log()
    log.start()
    (call,) = log.model_call(
        "coder", ("write_file", {"path": "tests/test_mini.py", "content": "x"})
    )
    log.tool_result(
        "coder",
        call,
        "write_file",
        {"error": "path is protected", "path": "tests/test_mini.py"},
    )
    log.tool("coder", "read_file", {"path": "mini.py"}, {"content": "def add"})
    log.answer("coder", "patch", ev.PATCH)
    out = convert(log)
    results = kind(out, "tool_result")
    assert [r["tool"] for r in results] == ["write_file", "read_file"]
    assert {k: results[0][k] for k in ("call", "tool", "error", "result")} == {
        "call": "c1",
        "tool": "write_file",
        "error": "path is protected",
        "result": {"path": "tests/test_mini.py"},
    }
    assert results[1]["error"] is None
    assert results[1]["result"] == {"content": "def add"}
    assert out["steps"][-1]["tool_calls"] == 3  # set_model_response counts


def test_answers_become_plan_claim_and_review_steps(convert):
    review = {
        "verdict": "request_changes",
        "comments": [
            {"file": "mini.py", "line": 2, "severity": "minor", "issue": "Add a test."}
        ],
        "must_fix": ["Add a test."],
    }
    out = convert(multi_run(review=review))
    (plan,) = kind(out, "plan")
    assert plan["node"] == "planner"
    assert plan["value"] == {"decline_reason": None, **ev.PLAN}
    assert list(plan["value"]) == [
        "actionable",
        "decline_reason",
        "summary",
        "files_to_inspect",
        "steps",
        "test_strategy",
    ]
    (claim,) = kind(out, "claim")
    assert (claim["node"], claim["agent"]) == ("coder", "coder")
    assert claim["value"] == ev.PATCH
    assert list(claim["value"]) == ["summary", "files_changed", "tests_added", "notes"]
    (verdict,) = kind(out, "review")
    assert verdict["node"] == "reviewer"
    assert verdict["value"] == review

    single = convert(single_run())
    assert not kind(single, "plan") and not kind(single, "review")
    (solo,) = kind(single, "claim")
    assert (solo["node"], solo["agent"]) == ("solo", "solo")
    assert solo["value"] == {
        "declined": False,
        "decline_reason": None,
        "summary": ev.SOLO["summary"],
        "files_changed": ev.SOLO["files_changed"],
    }


def test_diff_comes_from_unified_diff_never_diff_text(convert):
    def plant(_index: int, event: dict) -> None:
        delta = event.get("actions", {}).get("state_delta", {})
        if "diff_text" in delta:
            delta["diff_text"] = "DIFFTEXT-SENTINEL-77"

    out = convert(multi_run(), edit=plant)
    (diff,) = kind(out, "diff")
    assert diff["node"] == "collect_diff"
    assert diff["value"] == {
        "files": ["mini.py"],
        "insertions": 1,
        "deletions": 1,
        "unified_diff": ev.UNIFIED_DIFF,
        "cut": False,
    }
    assert "DIFFTEXT-SENTINEL-77" not in replay.dumps(out)
    (tests,) = kind(out, "tests")
    assert tests["node"] == "run_tests"
    assert tests["value"] == ev.TEST_REPORT
    assert list(tests["value"]) == [
        "passed",
        "exit_code",
        "failed_tests",
        "output_tail",
        "duration_s",
    ]


def test_running_cost_and_tool_calls(convert):
    log = Log()
    log.start()
    (a,) = log.model_call("planner", ("list_dir", {"path": "."}), cost=0.0008)
    log.tool_result("planner", a, "list_dir", {"entries": []})
    (b,) = log.model_call("planner", ("grep", {"pattern": "x"}), cost=0.0012)
    log.tool_result("planner", b, "grep", {"matches": []})
    log.answer("planner", "plan", ev.PLAN, cost=0.003)
    out = convert(log)
    assert [(s["kind"], s["cost_usd"], s["tool_calls"]) for s in out["steps"]] == [
        ("node", 0.0, 0),
        ("node", 0.0, 0),
        ("node", 0.0, 0),
        ("model_call", 0.0008, 0),
        ("tool_result", 0.0008, 1),
        ("model_call", 0.002, 1),
        ("tool_result", 0.002, 2),
        ("model_call", 0.005, 2),
        ("plan", 0.005, 3),
        ("outcome", 0.005, 3),
    ]
    assert [s["i"] for s in out["steps"]] == list(range(len(out["steps"])))


def test_error_event_becomes_a_stop_with_the_public_reason(convert):
    log = cap_run()
    record = log.record(
        outcome="failed", failure_kind="budget", reason=ev.CAP_REASON, patch_path=None
    )
    out = convert(log, record=record)
    (stop,) = kind(out, "stop")
    assert stop["text"] == ev.CAP_REASON
    assert (stop["node"], stop["visit"]) == ("coder", 1)
    assert out["steps"][-1]["kind"] == "outcome"
    assert out["steps"][-2] == stop
    assert "Error in plugin" not in replay.dumps(out)


def test_run_and_outcome_fields(convert):
    log = multi_run()
    record = log.record()
    row = log.row(record, audit=["touches tests/"], prompt_version="2c")
    out = convert(log, record=record, row=row)
    assert list(out) == ["schema", "run", "caps", "outcome", "steps"]
    assert out["schema"] == 1
    assert out["run"] == {
        "run_id": ev.MULTI_RUN_ID,
        "task_id": ev.TASK_ID,
        "repo": "mini",
        "category": "bug",
        "difficulty": "easy",
        "tempting": False,
        "levers": [],
        "system": "multi",
        "graph": "multi",
        "preset": "flash",
        "models": {"planner": ev.MODEL, "coder": ev.MODEL, "reviewer": ev.MODEL},
        "prompt_version": "2c",
        "mode": "bench",
        "recorded_at": "2026-10-01T06:26:11Z",
        "issue": {"title": ev.ISSUE["title"], "body": ev.ISSUE["body"]},
    }
    assert out["caps"] == CAPS
    assert out["outcome"] == {
        "outcome": "patch_written",
        "failure_kind": "none",
        "reason": "",
        "resolved": True,
        "cost_usd": record.cost_usd,
        "tool_calls": record.tool_calls,
        "tokens_in": record.tokens_in,
        "tokens_out": record.tokens_out,
        "duration_s": record.duration_s,
        "test_attempts": 0,
        "review_rounds": 0,
        "audit": ["touches tests/"],
    }


def test_prompt_version_defaults_to_2a(convert):
    log = single_run()
    row = log.row(log.record(), system="single")
    row.pop("prompt_version", None)
    out = convert(log, row=row)
    assert out["run"]["prompt_version"] == "2a"
    assert out["run"]["models"] == {"solo": ev.MODEL}
    assert out["run"]["graph"] == "single"


# --- time ---------------------------------------------------------------------------


def test_outcome_step_never_precedes_the_last_event(convert):
    log = cap_run(stop_at=541.85)
    record = log.record(
        outcome="failed",
        failure_kind="budget",
        reason=ev.CAP_REASON,
        patch_path=None,
        duration_s=541.78,
    )
    out = convert(log, record=record)
    stop, last = out["steps"][-2:]
    assert stop["t"] == 541.85
    assert last == {
        "i": len(out["steps"]) - 1,
        "t": 541.85,
        "kind": "outcome",
        "node": "coder",
        "visit": 1,
        "cost_usd": record.cost_usd,
        "tool_calls": record.tool_calls,
    }


def test_step_times_never_decrease(convert):
    log = Log()
    log.start()
    (a,) = log.model_call("planner", ("list_dir", {"path": "."}), at=10.004)
    log.tool_result("planner", a, "list_dir", {"entries": []}, at=9.5)
    (b,) = log.model_call("planner", ("list_dir", {"path": "x"}), at=12.346)
    log.tool_result("planner", b, "list_dir", {"entries": []}, at=12.3)
    out = convert(log, record=log.record(duration_s=12.0))
    times = [s["t"] for s in out["steps"]]
    assert times == sorted(times)
    assert all(round(t, 2) == t for t in times)
    assert times[-5:] == [10.0, 10.0, 12.35, 12.35, 12.35]


def test_a_log_that_ends_without_an_outcome_still_gets_one(convert):
    log = Log()
    log.start()
    log.answer("planner", "plan", ev.PLAN)
    log.router("route_plan", "actionable")
    (call,) = log.model_call("coder", ("bash", {"command": "pytest"}), at=700.0)
    log.tool_result("coder", call, "bash", {"exit_code": 0}, at=788.0)
    reason = "run exceeded 3000 s wall clock"
    record = log.record(
        outcome="failed",
        failure_kind="budget",
        reason=reason,
        patch_path=None,
        duration_s=3000.0,
    )
    out = convert(log, record=record)
    assert not kind(out, "stop")
    assert out["steps"][-2]["t"] == 788.0
    assert out["steps"][-1] == {
        "i": len(out["steps"]) - 1,
        "t": 3000.0,
        "kind": "outcome",
        "node": "coder",
        "visit": 1,
        "cost_usd": record.cost_usd,
        "tool_calls": record.tool_calls,
    }
    assert out["outcome"]["reason"] == reason
    assert out["outcome"]["duration_s"] == 3000.0


def test_output_is_byte_identical_when_built_twice(convert):
    first = replay.dumps(convert(multi_run()))
    second = replay.dumps(convert(multi_run()))
    assert first == second
    assert first.endswith("}\n")
    assert first.startswith('{\n  "schema": 1,\n  "run": {')
    assert json.loads(first)["steps"][-1]["kind"] == "outcome"


# --- refusals -------------------------------------------------------------------------


def refused(call, *args, **kwargs) -> replay.ReplayRefused:
    with pytest.raises(replay.ReplayRefused) as caught:
        call(*args, **kwargs)
    return caught.value


def test_totals_that_differ_from_the_record_are_refused(convert):
    log = multi_run()
    record = log.record()
    over = record.model_copy(update={"cost_usd": round(record.cost_usd + 0.0003, 4)})
    assert str(refused(convert, log, record=over)) == replay.TOTALS_DIFFER
    more = record.model_copy(update={"tool_calls": record.tool_calls + 1})
    assert str(refused(convert, log, record=more)) == replay.TOTALS_DIFFER
    within = record.model_copy(update={"cost_usd": round(record.cost_usd + 0.0002, 4)})
    out = convert(log, record=within)
    assert out["steps"][-2]["cost_usd"] == record.cost_usd
    assert (
        out["steps"][-1]["cost_usd"] == within.cost_usd
    )  # the meter ends on the record
    assert out["outcome"]["cost_usd"] == within.cost_usd


def test_unknown_node_is_refused(convert):
    log = multi_run()
    log.status("mystery_node", "hello")
    assert str(refused(convert, log)) == replay.UNKNOWN_NODE
    # The planner is not in the single-agent graph.
    assert str(refused(convert, multi_run(), system="single")) == replay.UNKNOWN_NODE


def test_tool_result_without_a_call_is_refused(convert):
    log = Log()
    log.start()
    log.model_call("planner", ("list_dir", {"path": "."}))
    log.tool_result("planner", "adk-call-9999", "list_dir", {"entries": []})
    assert str(refused(convert, log)) == replay.RESULT_WITHOUT_CALL

    early = Log()
    early.start()
    early.tool_result("planner", "adk-call-0001", "list_dir", {"entries": []})
    early.model_call("planner", ("list_dir", {"path": "."}))  # issues adk-call-0001
    assert str(refused(convert, early)) == replay.RESULT_WITHOUT_CALL


@pytest.mark.parametrize(
    "field, value",
    [
        ("task_id", "md-002"),
        ("run_id", "md-001-multi-flash-r2-20261001T062611Z"),
        ("outcome", "failed"),
    ],
)
def test_record_and_row_disagreement_is_refused(convert, field, value):
    log = multi_run()
    record = log.record()
    row = log.row(record, **{field: value})
    assert (
        str(refused(convert, log, record=record, row=row)) == replay.RECORD_ROW_DIFFER
    )


def test_an_unreadable_log_is_refused(convert, tmp_path, task, graphs):
    def bad_plan(_index: int, event: dict) -> None:
        delta = event.get("actions", {}).get("state_delta", {})
        if "plan" in delta:
            delta["plan"] = {"actionable": "maybe", "summary": SENTINEL}

    log = multi_run()
    error = refused(convert, log, edit=bad_plan)
    assert str(error) == replay.LOG_UNREADABLE
    assert SENTINEL not in repr(error) and error.__cause__ is None

    path = log.write(tmp_path)
    good = path.read_text()
    record = log.record()
    inputs = replay.RunInputs(
        record.run_id, path, record, log.row(record), task, dict(CAPS)
    )
    for text in ("", "\n", "not json\n", "[1, 2]\n", good + '{"truncated": \n'):
        path.write_text(text)
        error = refused(
            replay.convert_run,
            inputs,
            graphs=graphs,
            project=None,
            redact=(),
            repo_root=REPO_ROOT,
            home=HOME,
        )
        assert str(error) == replay.LOG_UNREADABLE


# --- public reasons (design 4.5) --------------------------------------------------------

DECLINE = "Needs a product decision; the rule is in /Users/alice/notes.md."
DECLINE_SHOWN = "Needs a product decision; the rule is in <host-path>/notes.md."
DRIVER_TEXT = "provider said: quota for projects/acme-prod in /Users/alice/x"


def _declined_multi():
    log = Log()
    log.start()
    log.answer(
        "planner",
        "plan",
        {**ev.PLAN, "actionable": False, "decline_reason": DECLINE, "steps": []},
    )
    log.router(
        "route_plan",
        "declined",
        state={"failure": {"kind": "declined", "reason": DECLINE}},
    )
    log.report_failure("declined", "none", DECLINE)
    return log, {"outcome": "declined", "reason": DECLINE}, {}


def _declined_single():
    log = Log(ev.SINGLE_RUN_ID)
    log.start()
    log.answer(
        "solo",
        "solo",
        {
            "declined": True,
            "decline_reason": DECLINE,
            "summary": "",
            "files_changed": [],
        },
    )
    log.router("route_solo", "declined")
    log.report_failure("declined", "none", DECLINE)
    return log, {"outcome": "declined", "reason": DECLINE}, {}


def _patch_written():
    return multi_run(), {}, {}


def _budget():
    fields = {"outcome": "failed", "failure_kind": "budget", "reason": ev.CAP_REASON}
    return cap_run(), fields, {}


def _agent_in_graph():
    reason = "tests still failing after 3 fix attempts"
    log = Log()
    log.start()
    log.answer("planner", "plan", ev.PLAN)
    log.router("route_plan", "actionable")
    log.answer("coder", "patch", ev.PATCH)
    log.verify(test_report=ev.FAILED_TESTS, route="exhausted")
    log.report_failure("failed", "agent", reason)
    return log, {"outcome": "failed", "failure_kind": "agent", "reason": reason}, {}


def _agent_from_driver():
    log = Log()
    log.start()
    log.answer("planner", "plan", ev.PLAN)
    log.error("planner", code="ValueError", message=DRIVER_TEXT)
    fields = {"outcome": "failed", "failure_kind": "agent", "reason": DRIVER_TEXT}
    return log, fields, {}


def _infra():
    log = Log()
    log.start()
    log.error("provision_sandbox", code="InfraError", message=DRIVER_TEXT)
    fields = {"outcome": "failed", "failure_kind": "infra", "reason": DRIVER_TEXT}
    return log, fields, {}


def _crashed():
    fields = {"outcome": "failed", "failure_kind": "budget", "reason": ev.CAP_REASON}
    return cap_run(), fields, {"crashed": True}


@pytest.mark.parametrize(
    "build, expected",
    [
        (_patch_written, ""),
        (_declined_multi, DECLINE_SHOWN),
        (_declined_single, DECLINE_SHOWN),
        (_budget, ev.CAP_REASON),
        (_agent_in_graph, "tests still failing after 3 fix attempts"),
        (_agent_from_driver, PUBLIC_AGENT),
        (_infra, PUBLIC_INFRA),
        (_crashed, PUBLIC_CRASH),
    ],
    ids=[
        "patch_written",
        "declined-multi",
        "declined-single",
        "budget",
        "agent-report_failure",
        "agent-driver",
        "infra",
        "crashed",
    ],
)
def test_public_reasons(convert, build, expected):
    log, fields, row_fields = build()
    record = log.record(**({"patch_path": None, **fields} if fields else {}))
    system = "single" if "-single-" in log.run_id else "multi"
    out = convert(log, record=record, row=log.row(record, system=system, **row_fields))
    assert out["outcome"]["reason"] == expected
    for stop in kind(out, "stop"):
        assert stop["text"] == expected
    text = replay.dumps(out)
    assert "provider said" not in text and "acme-prod" not in text


# --- scrubbing and caps --------------------------------------------------------------------

PROJECT = "acme-prod-123456"
NUMBER = "123456789012"
RUN = ev.MULTI_RUN_ID


@pytest.mark.parametrize(
    "text, expected",
    [
        # 1: this repository, then the home directory
        ("/Users/tester/work/sdlc/app/x.py", "<repo>/app/x.py"),
        ("/Users/tester/notes.txt", "~/notes.txt"),
        ("cd /Users/tester/work/sdlc && ls /Users/tester", "cd <repo> && ls ~"),
        ("/Users/tester/work/sdlc-old/x", "~/work/sdlc-old/x"),
        # 2: other host paths; sandbox paths stay
        ("/Users/alice/x.py", "<host-path>/x.py"),
        ("/home/bob/src", "<host-path>/src"),
        ("/root/.cache/pip", "<host-path>/.cache/pip"),
        ("/private/var/folders/ab/cd/T/tmp1", "<host-path>/ab/cd/T/tmp1"),
        ("/var/folders/ab/T/x", "<host-path>/ab/T/x"),
        ("C:\\Users\\carol\\proj", "<host-path>/proj"),
        ("/workspace/mini.py and /tmp/out", "/workspace/mini.py and /tmp/out"),
        ("GET /users/42", "GET /users/42"),
        # 3: sandbox names and container ids
        ("sandbox itp-0123456789ab ready", "sandbox <sandbox> ready"),
        ("container " + "0123456789abcdef" * 4, "container <container>"),
        # 4: the project id, projects/<x>, REPLAY_REDACT values
        (f"gcloud --project {PROJECT}", "gcloud --project <project>"),
        (f"{PROJECT.upper()}_cloudbuild", "<project>_cloudbuild"),
        ("projects/other-proj/locations/us", "projects/<project>/locations/us"),
        (f"projects/{PROJECT}/x", "projects/<project>/x"),
        (f"number {NUMBER}", "number <redacted>"),
        # 5: run artifact paths
        (f"patch written to runs/{RUN}/patch.diff", "patch written to patch.diff"),
        (f"/Users/tester/work/sdlc/runs/{RUN}/record.json", "record.json"),
        (f"at /srv/x/runs/{RUN}/pr_body.md now", "at pr_body.md now"),
        ("see runs/latest/x.txt", "see runs/latest/x.txt"),
        # 6: control and format characters (app/approval.py's escape set)
        ("\u202eevil", "<U+202E>evil"),
        ("\x1b[31mred", "<U+001B>[31mred"),
        ("a\u2028b", "a<U+2028>b"),
        ("x\ud800y", "x<U+D800>y"),
        ("zero\u200bwidth", "zero<U+200B>width"),
        ("tag\U000e0041", "tag<U+E0041>"),
        ("emoji\ufe0f", "emoji<U+FE0F>"),
        ("line\r\n", "line<U+000D>\n"),
        ("tab\tand\nnewline", "tab\tand\nnewline"),
        ("/Users/al\u202eice/x", "<host-path>/x"),
    ],
)
def test_rewrites_in_order(text, expected):
    out = replay.scrub_text(
        text, project=PROJECT, redact=(NUMBER,), repo_root=REPO_ROOT, home=HOME
    )
    assert out == expected


def test_escapes_are_the_approval_escape_set():
    def by_approval(points) -> str:
        return "".join(
            f"<U+{c:04X}>" if chr(c) not in "\t\n" and _escaped(chr(c)) else chr(c)
            for c in points
        )

    points = [*range(0x30000), *range(0xE0000, 0xE1000), 0x10FFFF]
    text = "".join(chr(c) for c in points)
    assert replay._escape_invisible(text) == by_approval(points)
    ascii_only = "".join(chr(c) for c in range(128))  # the ASCII fast path
    assert replay._escape_invisible(ascii_only) == by_approval(range(128))


def test_planted_project_id_is_rewritten_and_never_published(convert):
    log = Log()
    log.start(issue={"body": f"Deploying to {PROJECT} fails"})
    log.tool(
        "planner",
        "bash",
        {"command": "gcloud config get-value project"},
        {
            "stdout": f"{PROJECT}\n",
            "stderr": f"projects/{PROJECT}/locations/us",
            PROJECT: True,
        },
    )
    log.answer("planner", "plan", {**ev.PLAN, "summary": f"Use {PROJECT.upper()}."})
    out = convert(log, project=PROJECT)
    text = replay.dumps(out)
    assert PROJECT not in text.lower()
    assert "Deploying to <project> fails" in text
    assert "projects/<project>/locations/us" in text
    (result,) = kind(out, "tool_result")
    assert result["result"]["<project>"] is True


def filler(n: int) -> str:
    """n characters of distinct short words, which no leak rule matches."""
    return "".join(f"w{i} " for i in range(n))[:n]


def cut(original: str, head: int, tail: int) -> str:
    removed = len(original) - head - tail
    marker = f"\n[... {removed:,} characters cut ...]\n"
    return original[:head] + marker + original[-tail:]


def _tool_argument(text):
    log = Log()
    log.start()
    log.tool("coder", "write_file", {"path": "a.py", "content": text}, {"ok": True})
    return (
        log,
        {},
        lambda out: kind(out, "model_call")[0]["calls"][0]["args"]["content"],
    )


def _tool_result(text):
    log = Log()
    log.start()
    log.tool("coder", "read_file", {"path": "a.py"}, {"content": text})
    return log, {}, lambda out: kind(out, "tool_result")[0]["result"]["content"]


def _tool_error(text):
    log = Log()
    log.start()
    log.tool("coder", "bash", {"command": "x"}, {"error": text})
    return log, {}, lambda out: kind(out, "tool_result")[0]["error"]


def _plan_string(text):
    log = Log()
    log.start()
    log.answer("planner", "plan", {**ev.PLAN, "summary": text})
    return log, {}, lambda out: kind(out, "plan")[0]["value"]["summary"]


def _claim_string(text):
    log = Log()
    log.start()
    log.answer("coder", "patch", {**ev.PATCH, "notes": text})
    return log, {}, lambda out: kind(out, "claim")[0]["value"]["notes"]


def _review_string(text):
    log = Log()
    log.start()
    log.answer("reviewer", "review", {**ev.REVIEW, "must_fix": [text]})
    return log, {}, lambda out: kind(out, "review")[0]["value"]["must_fix"][0]


def _model_text(text):
    log = Log()
    log.start()
    log.model_call("planner", text=text)
    return log, {}, lambda out: kind(out, "model_call")[0]["text"]


def _decline_reason(text):
    log = Log()
    log.start()
    plan = {**ev.PLAN, "actionable": False, "decline_reason": text}
    log.answer("planner", "plan", plan)
    return log, {}, lambda out: kind(out, "plan")[0]["value"]["decline_reason"]


def _outcome_reason(text):
    log = Log()
    log.start()
    plan = {**ev.PLAN, "actionable": False, "decline_reason": text}
    log.answer("planner", "plan", plan)
    record = {"outcome": "declined", "reason": text, "patch_path": None}
    return log, record, lambda out: out["outcome"]["reason"]


def _status_message(text):
    log = Log()
    log.start()
    log.node("collect_diff", text + "\nsecond line")
    return log, {}, lambda out: kind(out, "node")[-1]["message"]


def _issue_body(text):
    log = Log()
    log.start(issue={"body": text})
    return log, {}, lambda out: out["run"]["issue"]["body"]


def _test_output(text):
    log = Log()
    log.start()
    report = {**ev.TEST_REPORT, "output_tail": text}
    log.node("run_tests", "tests: failed", state={"test_report": report})
    return log, {}, lambda out: kind(out, "tests")[0]["value"]["output_tail"]


@pytest.mark.parametrize(
    "build, length, head, tail, limit",
    [
        (_tool_argument, 10_000, 3000, 800, 4000),
        (_tool_result, 10_000, 3000, 800, 4000),
        (_tool_error, 5_000, 3000, 800, 4000),
        (_plan_string, 5_000, 3000, 800, 4000),
        (_claim_string, 5_000, 3000, 800, 4000),
        (_review_string, 5_000, 3000, 800, 4000),
        (_model_text, 5_000, 3000, 800, 4000),
        (_decline_reason, 2_000, 800, 150, 1000),
        (_outcome_reason, 2_000, 800, 150, 1000),
        (_status_message, 500, 200, 60, 300),
        (_issue_body, 10_000, 6000, 1500, 8000),
        (_test_output, 10_000, 1000, 6500, 8000),
    ],
    ids=[
        "tool-argument",
        "tool-result",
        "tool-error",
        "plan",
        "claim",
        "review",
        "model-text",
        "decline-reason",
        "outcome-reason",
        "status-message",
        "issue-body",
        "test-output",
    ],
)
def test_caps_and_markers(convert, build, length, head, tail, limit):
    original = filler(length)
    log, fields, pick = build(original)
    shown = pick(convert(log, record=log.record(**fields)))
    assert shown == cut(original, head, tail)
    assert len(shown) <= limit
    # A string at the cap is left whole.
    log, fields, pick = build(original[:limit])
    assert pick(convert(log, record=log.record(**fields))) == original[:limit]


def test_cut_marker_has_thousands_separators():
    text = filler(10_000)
    assert replay.cut_text(text, 4000, head=3000, tail=800) == (
        text[:3000] + "\n[... 6,200 characters cut ...]\n" + text[-800:]
    )
    assert replay.cut_text("short", 4000, head=3000, tail=800) == "short"


def test_lists_are_capped_with_a_count_of_the_rest(convert):
    log = Log()
    log.start()
    log.tool(
        "planner",
        "grep",
        {"pattern": "x", "paths": [f"p{i}" for i in range(120)]},
        {"matches": [f"m{i}" for i in range(150)]},
    )
    out = convert(log)
    matches = kind(out, "tool_result")[0]["result"]["matches"]
    assert matches == [f"m{i}" for i in range(100)] + ["[... 50 more items ...]"]
    paths = kind(out, "model_call")[0]["calls"][0]["args"]["paths"]
    assert paths[-1] == "[... 20 more items ...]" and len(paths) == 101


def test_names_keys_and_object_entries_are_capped(convert):
    name, key, model = "n" * 20_000, "k" * 20_000, "m" * 20_000
    log = Log()
    log.start()
    (call_id,) = log.model_call("coder", (name, {key: 1}), model=model)
    entries = {f"e{i}": i for i in range(150)}
    log.tool_result("coder", call_id, name, {key: "v", **entries})
    out = convert(log)
    (step,) = kind(out, "model_call")
    (result,) = kind(out, "tool_result")
    assert step["calls"][0]["tool"] == cut(name, 200, 60) == result["tool"]
    assert step["model"] == cut(model, 200, 60) == out["run"]["models"]["coder"]
    assert list(step["calls"][0]["args"]) == [cut(key, 200, 60)]
    keys = list(result["result"])
    assert keys[0] == cut(key, 200, 60) and keys[1:100] == list(entries)[:99]
    assert keys[-1] == "[... 51 more entries ...]" and len(keys) == 101
    assert result["result"][keys[-1]] is None


@pytest.mark.parametrize("where", ["record cost", "record duration", "test duration"])
def test_numbers_that_are_not_finite_are_refused(convert, where):
    log = multi_run()
    record, edit = log.record(), None
    if where == "record cost":
        record = log.record(cost_usd=float("nan"))
    elif where == "record duration":
        record = log.record(duration_s=float("inf"))
    else:

        def edit(_index: int, event: dict) -> None:
            report = event.get("actions", {}).get("state_delta", {}).get("test_report")
            if report:
                report["duration_s"] = float("nan")

    assert str(refused(convert, log, record=record, edit=edit)) == replay.NOT_FINITE


def _big_diff(files: int, lines_per_hunk: int) -> dict:
    text = ""
    for f in range(files):
        text += f"diff --git a/f{f}.py b/f{f}.py\n--- a/f{f}.py\n+++ b/f{f}.py\n"
        for h in range(2):
            start = h * 100 + 1
            text += f"@@ -{start},{lines_per_hunk} +{start},{lines_per_hunk} @@\n"
            text += "".join(f"+line {f} {h} {n}\n" for n in range(lines_per_hunk))
    return {
        "unified_diff": text,
        "files": [f"f{f}.py" for f in range(files)],
        "insertions": 9999,
        "deletions": 7777,
    }


def test_diff_cut_keeps_true_counts(convert):
    diff = _big_diff(files=40, lines_per_hunk=60)
    original = diff["unified_diff"]
    assert len(original) > 60_000
    out = convert(multi_run(diff=diff))
    value = kind(out, "diff")[0]["value"]
    assert value["cut"] is True
    assert (value["insertions"], value["deletions"]) == (9999, 7777)
    assert value["files"] == diff["files"]
    text = value["unified_diff"]
    assert len(text) <= 60_000
    kept, marker = text.rsplit("\n[... ", 1)  # kept ends with its last line's "\n"
    assert original.startswith(kept)
    assert original[len(kept) :].startswith(("diff --git ", "@@ "))
    assert "\n@@ " in kept.rsplit("diff --git ", 1)[-1]  # no file header left alone
    assert marker == f"{len(original) - len(kept):,} characters cut ...]\n"
    small = convert(multi_run())
    assert kind(small, "diff")[0]["value"]["cut"] is False


def test_a_diff_with_one_huge_hunk_is_cut_at_a_line():
    hunk = "diff --git a/x b/x\n@@ -1,9000 +1,9000 @@\n" + "".join(
        f"+row {n}\n" for n in range(9000)
    )
    text = replay._cut_diff(hunk, 60_000)
    kept = text.rsplit("\n[... ", 1)[0]
    assert hunk.startswith(kept) and kept.endswith("\n") and len(text) <= 60_000
    assert len(kept) > 50_000


# --- review focus ---------------------------------------------------------------------------

SENTINEL = "SENTINELqq"


def _plant_everywhere(_index: int, event: dict) -> None:
    """A sentinel in every place the converter must never read from."""
    event["zz_future_field"] = SENTINEL
    event["custom_metadata"] = {"note": SENTINEL}
    event["invocation_id"] = SENTINEL
    event["id"] = SENTINEL
    event["branch"] = SENTINEL
    event["output"] = {"leak": SENTINEL}
    if "error_code" in event:
        event["error_message"] = SENTINEL
    event.setdefault("node_info", {})["zz"] = SENTINEL
    actions = event.setdefault("actions", {})
    actions["zz_future_action"] = SENTINEL
    actions["set_model_response"] = {"leak": SENTINEL}
    delta = actions.setdefault("state_delta", {})
    delta["zz_future_key"] = SENTINEL
    for key in ("sandbox_id", "baseline_sha", "issue_text", "diff_text"):
        if key in delta:
            delta[key] = SENTINEL
    if "protected_paths" in delta:
        delta["protected_paths"] = [SENTINEL]
    for key in ("issue", "budget", "plan", "patch", "diff", "test_report", "review"):
        if isinstance(delta.get(key), dict):
            delta[key]["zz"] = SENTINEL
    if isinstance(delta.get("outcome"), dict):
        delta["outcome"]["patch_path"] = SENTINEL
    if "usage_metadata" in event:
        event["usage_metadata"]["traffic_type"] = SENTINEL
        event["usage_metadata"]["zz"] = SENTINEL
    parts = event.get("content", {}).get("parts", [])
    for part in parts:
        part["zz_part"] = SENTINEL
        for key in ("function_call", "function_response"):
            if key in part:
                part[key]["zz"] = SENTINEL
        if "function_call" in part:
            part["thought_signature"] = SENTINEL
    if "usage_metadata" in event:
        parts.append({"text": SENTINEL, "thought": True})


@pytest.mark.parametrize("build", [multi_run, cap_run])
def test_unknown_event_fields_never_reach_the_replay(convert, build):
    log = build()
    fields = (
        {"outcome": "failed", "failure_kind": "budget", "reason": ev.CAP_REASON}
        if build is cap_run
        else {}
    )
    out = convert(log, record=log.record(**fields), edit=_plant_everywhere)
    assert SENTINEL not in replay.dumps(out)
    assert out["steps"][-1]["kind"] == "outcome"


def test_a_token_cut_in_half_by_a_cap_is_still_refused(convert):
    token = "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    content = "x " * 1495 + token + " " + "y " * 4000
    assert content.index(token) == 2990  # the 3,000-character head keeps 10 of it
    shown = replay.cut_text(content, 4000, head=3000, tail=800)
    assert not scan_value(shown, file="x.json")  # the cut text alone looks clean

    log = Log()
    log.start()
    log.tool("coder", "read_file", {"path": "a.py"}, {"content": content})
    error = refused(convert, log)
    assert str(error) == replay.LEAK_FOUND
    found = {(h.file, h.rule) for h in error.hits if h.path.endswith(".result.content")}
    assert (f"{RUN}.json", "github-token") in found
    assert all(token[:12] not in str(h) for h in error.hits)
    assert token[:12] not in repr(error)


ENTROPIC = "aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3zA5"  # a false positive of high-entropy


def test_allow_clears_only_its_rule_at_its_path(convert):
    log = Log()
    log.start(issue={"body": f"the build id {ENTROPIC} is in the log"})
    error = refused(convert, log)
    assert str(error) == replay.LEAK_FOUND
    assert [(h.path, h.rule) for h in error.hits] == [
        ("$.run.issue.body", "high-entropy")
    ]
    assert error.hits[0].file == f"{RUN}.json"
    other = refused(convert, log, allow=[("$.run.issue.title", "high-entropy")])
    assert other.hits == error.hits
    wrong_rule = refused(convert, log, allow=[("$.run.issue.body", "email")])
    assert wrong_rule.hits == error.hits
    out = convert(log, allow=[("$.run.issue.body", "high-entropy")])
    assert ENTROPIC in out["run"]["issue"]["body"]


def test_a_run_id_the_entropy_rule_would_flag_needs_no_allow_entry(convert):
    run_id = "md-001-single-flash-r1-20261001T084838Z"
    log = single_run(run_id=run_id)
    out = convert(log)
    assert out["run"]["run_id"] == run_id


def test_a_lone_surrogate_in_a_log_is_escaped(convert):
    def plant(_index: int, event: dict) -> None:
        for part in event.get("content", {}).get("parts", []):
            response = part.get("function_response", {}).get("response")
            if isinstance(response, dict) and "content" in response:
                response["content"] = "a\ud800b\u202ec"

    log = Log()
    log.start()
    log.tool("coder", "read_file", {"path": "a.py"}, {"content": "x"})
    out = convert(log, edit=plant)
    assert kind(out, "tool_result")[0]["result"]["content"] == "a<U+D800>b<U+202E>c"
    replay.dumps(out).encode("utf-8")


# --- progress lines ----------------------------------------------------------------------


def test_call_detail_matches_the_progress_lines(convert):
    calls = [
        ("bash", {"command": "ls\n  -la " + "a" * 100}),
        ("read_file", {"path": "mini.py"}),
        ("write_file", {"path": "b.py", "content": "x = 1\n"}),
        ("edit_file", {"path": "mini.py", "old": "-", "new": "+"}),
        ("list_dir", {"path": "."}),
        ("grep", {"pattern": "add", "path": "mini.py"}),
        ("run_python", {"code": "1"}),
    ]
    log = Log()
    log.start()
    ids = log.model_call("coder", *calls)
    for call_id, (name, _args) in zip(ids, calls, strict=True):
        log.tool_result("coder", call_id, name, {"ok": True})
    out = convert(log)
    labels = [c["label"] for c in kind(out, "model_call")[0]["calls"]]
    for label, (name, args) in zip(labels, calls, strict=True):
        part = types.Part(function_call=types.FunctionCall(name=name, args=args))
        event = Event(author="coder", content=types.Content(role="model", parts=[part]))
        (line,) = format_event("t", event)
        assert line.split(" → ", 1)[1] == label
        assert label == f"{name} {call_detail(name, args)}".rstrip()
