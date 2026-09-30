import shutil
from pathlib import Path

import pytest

from app.driver import run_pipeline
from app.models import RoleModels
from app.pipeline import build_workflow
from app.schemas import PatchResult, Plan, Review, RunRecord, RunRequest
from tests.fakes import FakeLlm, call, json_out, make_bench_task

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(shutil.which("docker") is None, reason="docker not installed"),
]

FIX = call(
    "edit_file", path="mini.py", old_string="return a - b", new_string="return a + b"
)
DONE = json_out(PatchResult(summary="fix add"))


@pytest.fixture
def bench(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCH_TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("BENCH_REPOS_DIR", str(tmp_path / "repos"))
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    make_bench_task(tmp_path)
    return tmp_path


async def run(coder_steps: list[dict], run_id: str = "d-1") -> RunRecord:
    models = RoleModels(
        planner=FakeLlm([json_out(Plan(actionable=True, summary="fix add"))]),
        coder=FakeLlm(coder_steps),
        reviewer=FakeLlm([json_out(Review(verdict="approve"))]),
    )
    return await run_pipeline(
        RunRequest(task_id="t-1", run_id=run_id), workflow=build_workflow(models)
    )


def assert_patch_fixes_add(record: RunRecord) -> None:
    assert record.outcome == "patch_written", record.reason
    patch = Path(record.patch_path).read_text()
    assert "-    return a - b" in patch and "+    return a + b" in patch


async def test_real_sandbox_edit_produces_patch(bench):
    record = await run(
        [
            call("read_file", path="mini.py"),
            FIX,
            call("bash", command="python -m pytest -q"),
            DONE,
        ]
    )
    assert_patch_fixes_add(record)
    assert record.tool_calls == 3


async def test_coder_commit_does_not_hide_the_diff(bench):
    commit = "git -c user.name=a -c user.email=a@a commit -am x"
    record = await run([FIX, call("bash", command=commit), DONE])
    assert_patch_fixes_add(record)
    assert record.test_attempts == 0
    events = (bench / "runs" / "d-1" / "events.jsonl").read_text()
    assert "1 file changed" in events  # the coder's commit really happened


async def test_deleting_the_worktree_git_file_does_not_break_the_diff(bench):
    record = await run([FIX, call("bash", command="rm -rf .git"), DONE])
    assert_patch_fixes_add(record)
    assert record.test_attempts == 0


async def test_renaming_a_protected_test_fails_the_attempt(bench):
    record = await run(
        [
            FIX,
            call("bash", command="git mv tests/test_mini.py tests/test_renamed.py"),
            DONE,
            call("bash", command="git mv tests/test_renamed.py tests/test_mini.py"),
            DONE,
        ]
    )
    events = (bench / "runs" / "d-1" / "events.jsonl").read_text()
    assert "These existing test files are read-only but were modified" in events
    assert record.test_attempts == 1
    assert_patch_fixes_add(record)
    assert "test_renamed" not in Path(record.patch_path).read_text()
