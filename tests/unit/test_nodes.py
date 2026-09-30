import pytest

from app.environment import registry
from app.environment.base import ExecResult
from app.nodes import intake
from app.nodes.finish import deliver_patch, report_failure
from app.nodes.intake import fetch_issue, provision_sandbox
from app.nodes.routing import route_plan, route_review
from app.nodes.verify import collect_diff, run_tests
from app.schemas import Diff, Plan, Review, RunRequest
from tests.fakes import FakeEnvironment, make_bench_task

DIFF = "diff --git a/mini.py b/mini.py\n--- a/mini.py\n+++ b/mini.py\n@@ -1,2 +1,2 @@\n-x\n+y\n"


@pytest.fixture
def bench_root(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCH_TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("BENCH_REPOS_DIR", str(tmp_path / "repos"))
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    make_bench_task(tmp_path)
    return tmp_path


def _last(events):
    return events[-1]


def test_fetch_issue_initialises_state(bench_root):
    events = list(fetch_issue(RunRequest(task_id="t-1", run_id="r-1")))
    assert events[0].content is not None  # visible status message
    final = _last(events)
    assert final.output.title == "add is broken"
    assert final.actions.state_delta["test_attempts"] == 0
    assert "add subtracts" in final.actions.state_delta["issue_text"]


async def test_provision_uploads_planted_repo_and_protects_tests(
    bench_root, monkeypatch
):
    env = FakeEnvironment()

    async def fake_start():
        return env

    monkeypatch.setattr(intake, "start_environment", fake_start)
    issue = _last(list(fetch_issue(RunRequest(task_id="t-1", run_id="r-1")))).output
    events = [e async for e in provision_sandbox(issue)]
    state = _last(events).actions.state_delta
    assert state["sandbox_id"] == env.env_id
    assert state["protected_paths"] == ["tests/test_mini.py"]
    assert "a - b" in env.files["/workspace/repo/mini.py"]
    assert not any("hidden" in path for path in env.files)
    assert env.commands[0].startswith("git init")


async def test_collect_diff_parses_numstat():
    env = FakeEnvironment(
        responses={
            "git add -A && git diff": ExecResult(exit_code=0, stdout=DIFF, stderr=""),
            "git diff --cached --numstat": ExecResult(
                exit_code=0, stdout="3\t1\tmini.py\n-\t-\tlogo.png\n", stderr=""
            ),
        }
    )
    registry.register(env)
    events = [e async for e in collect_diff({"summary": "x"}, env.env_id)]
    diff = _last(events).output
    assert (diff.files, diff.insertions, diff.deletions) == (
        ["mini.py", "logo.png"],
        3,
        1,
    )
    assert _last(events).actions.state_delta["diff_text"] == DIFF


def _diff(files=("mini.py",)):
    return Diff(
        unified_diff=DIFF if files else "", files=list(files), insertions=1, deletions=1
    )


async def test_run_tests_pass_routes_to_review():
    env = FakeEnvironment(
        responses={
            "python -m pytest": ExecResult(exit_code=0, stdout="3 passed", stderr="")
        }
    )
    registry.register(env)
    final = _last([e async for e in run_tests(_diff(), env.env_id, [], 0)])
    assert final.actions.route == "pass" and final.output.passed


async def test_run_tests_fail_counts_attempts_and_exhausts():
    out = "FAILED tests/test_mini.py::test_add - assert 0 == 2\n1 failed"
    env = FakeEnvironment(
        responses={"python -m pytest": ExecResult(exit_code=1, stdout=out, stderr="")}
    )
    registry.register(env)
    first = _last([e async for e in run_tests(_diff(), env.env_id, [], 0)])
    assert first.actions.route == "fail"
    assert first.actions.state_delta["test_attempts"] == 1
    assert first.output.failed_tests == ["tests/test_mini.py::test_add"]
    last = _last([e async for e in run_tests(_diff(), env.env_id, [], 3)])
    assert last.actions.route == "exhausted"
    assert last.actions.state_delta["failure"]["kind"] == "agent"


async def test_run_tests_rejects_empty_diff():
    env = FakeEnvironment(
        responses={
            "python -m pytest": ExecResult(exit_code=0, stdout="3 passed", stderr="")
        }
    )
    registry.register(env)
    final = _last([e async for e in run_tests(_diff(files=()), env.env_id, [], 0)])
    assert final.actions.route == "fail"
    assert "No changes were made" in final.output.output_tail
    assert not any(c.startswith("python -m pytest") for c in env.commands)


async def test_run_tests_rejects_protected_file_edits():
    env = FakeEnvironment()
    registry.register(env)
    final = _last(
        [
            e
            async for e in run_tests(
                _diff(files=("tests/test_mini.py",)),
                env.env_id,
                ["tests/test_mini.py"],
                0,
            )
        ]
    )
    assert final.actions.route == "fail"
    assert "tests/test_mini.py" in final.output.output_tail


@pytest.mark.parametrize(
    "result",
    [
        ExecResult(exit_code=5, stdout="no tests ran", stderr=""),
        ExecResult(exit_code=2, stdout="ERROR collecting tests/test_x.py", stderr=""),
        ExecResult(exit_code=124, stdout="", stderr="", timed_out=True),
    ],
)
async def test_run_tests_treats_no_tests_and_timeouts_as_failure(result):
    env = FakeEnvironment(responses={"python -m pytest": result})
    registry.register(env)
    final = _last([e async for e in run_tests(_diff(), env.env_id, [], 0)])
    assert final.actions.route == "fail" and not final.output.passed


def test_route_plan():
    ok = route_plan(Plan(actionable=True, summary="s"))
    assert ok.actions.route == "actionable"
    no = route_plan(Plan(actionable=False, summary="s", decline_reason="needs OAuth"))
    assert no.actions.route == "declined"
    assert no.actions.state_delta["failure"] == {
        "kind": "declined",
        "reason": "needs OAuth",
    }


def test_route_review_counts_rounds():
    assert route_review(Review(verdict="approve"), 0).actions.route == "approve"
    changes = route_review(Review(verdict="request_changes", must_fix=["x"]), 0)
    assert (
        changes.actions.route == "changes"
        and changes.actions.state_delta["review_rounds"] == 1
    )
    assert (
        route_review(Review(verdict="request_changes"), 2).actions.route == "exhausted"
    )


def test_deliver_patch_writes_file(bench_root):
    issue = {"run_id": "r-9"}
    events = list(deliver_patch({}, issue, {"unified_diff": DIFF}))
    outcome = _last(events).actions.state_delta["outcome"]
    assert outcome["outcome"] == "patch_written"
    assert (bench_root / "runs" / "r-9" / "patch.diff").read_text() == DIFF


def test_report_failure_maps_declines_and_agent_failures():
    declined = _last(
        list(report_failure({}, {"kind": "declined", "reason": "r"}))
    ).actions.state_delta["outcome"]
    assert (declined["outcome"], declined["failure_kind"]) == ("declined", "none")
    failed = _last(
        list(report_failure({}, {"kind": "agent", "reason": "r"}))
    ).actions.state_delta["outcome"]
    assert (failed["outcome"], failed["failure_kind"]) == ("failed", "agent")
