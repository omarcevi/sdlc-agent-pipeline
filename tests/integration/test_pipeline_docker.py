import shutil
from pathlib import Path

import pytest

from app.driver import run_pipeline
from app.models import RoleModels
from app.pipeline import build_workflow
from app.schemas import PatchResult, Plan, Review, RunRequest
from tests.fakes import FakeLlm, call, json_out, make_bench_task

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(shutil.which("docker") is None, reason="docker not installed"),
]


async def test_real_sandbox_edit_produces_patch(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCH_TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("BENCH_REPOS_DIR", str(tmp_path / "repos"))
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    make_bench_task(tmp_path)
    coder = FakeLlm(
        [
            call("read_file", path="mini.py"),
            call(
                "edit_file",
                path="mini.py",
                old_string="return a - b",
                new_string="return a + b",
            ),
            call("bash", command="python -m pytest -q"),
            json_out(PatchResult(summary="fix add")),
        ]
    )
    models = RoleModels(
        planner=FakeLlm([json_out(Plan(actionable=True, summary="fix add"))]),
        coder=coder,
        reviewer=FakeLlm([json_out(Review(verdict="approve"))]),
    )
    record = await run_pipeline(
        RunRequest(task_id="t-1", run_id="d-1"), workflow=build_workflow(models)
    )
    assert record.outcome == "patch_written", record.reason
    patch = Path(record.patch_path).read_text()
    assert "-    return a - b" in patch and "+    return a + b" in patch
    assert record.tool_calls == 3
