import pytest

from app.environment import registry
from app.environment.base import WORKDIR, ExecResult, InfraError
from app.nodes import intake
from app.nodes.finish import deliver_patch, report_failure
from app.nodes.intake import (
    BASELINE_SHA_CMD,
    GIT_BASELINE,
    PIPELINE_GIT_DIR,
    fetch_issue,
    provision_sandbox,
)
from app.nodes.routing import route_plan, route_review
from app.nodes.verify import DIFF_CMD, NUMSTAT_CMD, TEST_CMD, collect_diff, run_tests
from app.schemas import Diff, Plan, Review, RunRequest
from tests.fakes import BASELINE_SHA, FakeEnvironment, make_bench_task

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
    assert env.commands == [GIT_BASELINE, BASELINE_SHA_CMD]
    assert state["baseline_sha"] == BASELINE_SHA


def test_pipeline_git_dir_is_outside_the_worktree():
    assert not PIPELINE_GIT_DIR.startswith(f"{WORKDIR}/")
    assert f"git init -q --separate-git-dir {PIPELINE_GIT_DIR}" in GIT_BASELINE
    for command in (GIT_BASELINE, BASELINE_SHA_CMD, DIFF_CMD, NUMSTAT_CMD):
        assert f"--git-dir={PIPELINE_GIT_DIR} --work-tree={WORKDIR}" in command


async def test_provision_fails_as_infra_without_a_baseline_sha(bench_root, monkeypatch):
    env = FakeEnvironment(
        responses={BASELINE_SHA_CMD: ExecResult(exit_code=0, stdout="", stderr="")}
    )

    async def fake_start():
        return env

    monkeypatch.setattr(intake, "start_environment", fake_start)
    issue = _last(list(fetch_issue(RunRequest(task_id="t-1", run_id="r-1")))).output
    with pytest.raises(InfraError, match="baseline"):
        [e async for e in provision_sandbox(issue)]
    assert env.closed


async def test_collect_diff_parses_numstat():
    env = FakeEnvironment(
        responses={
            DIFF_CMD: ExecResult(exit_code=0, stdout=DIFF, stderr=""),
            NUMSTAT_CMD: ExecResult(
                exit_code=0, stdout="3\t1\tmini.py\n-\t-\tlogo.png\n", stderr=""
            ),
        }
    )
    registry.register(env)
    events = [e async for e in collect_diff({"summary": "x"}, env.env_id, BASELINE_SHA)]
    diff = _last(events).output
    assert (diff.files, diff.insertions, diff.deletions) == (
        ["mini.py", "logo.png"],
        3,
        1,
    )
    assert _last(events).actions.state_delta["diff_text"] == DIFF


async def test_collect_diff_is_taken_against_the_baseline_without_renames():
    env = FakeEnvironment()
    registry.register(env)
    [e async for e in collect_diff({"summary": "x"}, env.env_id, BASELINE_SHA)]
    diff_command, numstat_command = env.commands
    assert diff_command == f"{DIFF_CMD} {BASELINE_SHA}"
    assert numstat_command == f"{NUMSTAT_CMD} {BASELINE_SHA}"
    for command in env.commands:
        assert "--cached" in command and "--no-renames" in command
        assert " HEAD" not in command
    assert "--binary" in diff_command


@pytest.mark.parametrize(
    "numstat",
    [
        "0\t4\ttests/test_mini.py\n4\t0\ttests/x.py\n",  # what --no-renames prints
        "0\t0\ttests/{test_mini.py => x.py}\n",
        "0\t0\ttests/test_mini.py => tests/x.py\n",
    ],
)
async def test_a_renamed_protected_test_is_reported_as_delete_and_add(numstat):
    env = FakeEnvironment(
        responses={
            DIFF_CMD: ExecResult(exit_code=0, stdout=DIFF, stderr=""),
            NUMSTAT_CMD: ExecResult(exit_code=0, stdout=numstat, stderr=""),
        }
    )
    registry.register(env)
    events = [e async for e in collect_diff({"summary": "x"}, env.env_id, BASELINE_SHA)]
    diff = _last(events).output
    assert diff.files == ["tests/test_mini.py", "tests/x.py"]
    final = _last(
        [e async for e in run_tests(diff, env.env_id, ["tests/test_mini.py"], 0)]
    )
    assert final.actions.route == "fail"
    assert "read-only" in final.output.output_tail
    assert "tests/test_mini.py" in final.output.output_tail
    assert not any(c.startswith(TEST_CMD) for c in env.commands)


async def test_collect_diff_expands_renames_that_move_directories():
    env = FakeEnvironment(
        responses={
            NUMSTAT_CMD: ExecResult(
                exit_code=0,
                stdout="1\t1\t{tests => checks}/test_mini.py\n"
                "0\t0\tpkg/{ => sub}/mod.py\n",
                stderr="",
            )
        }
    )
    registry.register(env)
    events = [e async for e in collect_diff({"summary": "x"}, env.env_id, BASELINE_SHA)]
    assert _last(events).output.files == [
        "tests/test_mini.py",
        "checks/test_mini.py",
        "pkg/mod.py",
        "pkg/sub/mod.py",
    ]


@pytest.mark.parametrize("failing", [DIFF_CMD, NUMSTAT_CMD])
async def test_collect_diff_failure_is_an_infra_error(failing):
    env = FakeEnvironment(
        responses={failing: ExecResult(exit_code=128, stdout="", stderr="fatal: x")}
    )
    registry.register(env)
    with pytest.raises(InfraError, match="fatal: x"):
        [e async for e in collect_diff({"summary": "x"}, env.env_id, BASELINE_SHA)]


def _diff(files=("mini.py",)):
    return Diff(
        unified_diff=DIFF if files else "", files=list(files), insertions=1, deletions=1
    )


async def test_run_tests_pass_routes_to_review():
    env = FakeEnvironment(
        responses={TEST_CMD: ExecResult(exit_code=0, stdout="3 passed", stderr="")}
    )
    registry.register(env)
    final = _last([e async for e in run_tests(_diff(), env.env_id, [], 0)])
    assert final.actions.route == "pass" and final.output.passed


async def test_run_tests_fail_counts_attempts_and_exhausts():
    out = "FAILED tests/test_mini.py::test_add - assert 0 == 2\n1 failed"
    env = FakeEnvironment(
        responses={TEST_CMD: ExecResult(exit_code=1, stdout=out, stderr="")}
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
        responses={TEST_CMD: ExecResult(exit_code=0, stdout="3 passed", stderr="")}
    )
    registry.register(env)
    final = _last([e async for e in run_tests(_diff(files=()), env.env_id, [], 0)])
    assert final.actions.route == "fail"
    assert "No changes were made" in final.output.output_tail
    assert not any(c.startswith(TEST_CMD) for c in env.commands)


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
    "path", [".github/workflows/ci.yaml", ".github/CODEOWNERS", ".github"]
)
async def test_run_tests_rejects_github_edits(path):
    env = FakeEnvironment()
    registry.register(env)
    final = _last(
        [
            e
            async for e in run_tests(
                _diff(files=("mini.py", path)), env.env_id, ["tests/test_mini.py"], 0
            )
        ]
    )
    assert final.actions.route == "fail"
    assert ".github/ is read-only" in final.output.output_tail
    assert path in final.output.output_tail
    assert not any(c.startswith(TEST_CMD) for c in env.commands)


async def test_run_tests_allows_paths_that_only_resemble_github():
    env = FakeEnvironment(
        responses={TEST_CMD: ExecResult(exit_code=0, stdout="3 passed", stderr="")}
    )
    registry.register(env)
    final = _last(
        [
            e
            async for e in run_tests(
                _diff(files=(".githubx/a.py", "docs/.github/b.md")), env.env_id, [], 0
            )
        ]
    )
    assert final.actions.route == "pass"


@pytest.mark.parametrize(
    "result",
    [
        ExecResult(exit_code=5, stdout="no tests ran", stderr=""),
        ExecResult(exit_code=2, stdout="ERROR collecting tests/test_x.py", stderr=""),
        ExecResult(exit_code=124, stdout="", stderr="", timed_out=True),
    ],
)
async def test_run_tests_treats_no_tests_and_timeouts_as_failure(result):
    env = FakeEnvironment(responses={TEST_CMD: result})
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
