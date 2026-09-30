import shutil
from pathlib import Path

import pytest

from app.baseline import build_baseline_workflow
from app.driver import run_pipeline
from app.schemas import RunRequest, SoloResult
from tests.fakes import FakeLlm, call, json_out, make_bench_task

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(shutil.which("docker") is None, reason="docker not installed"),
]

FIX = call(
    "edit_file", path="mini.py", old_string="return a - b", new_string="return a + b"
)


@pytest.fixture
def bench(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCH_TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("BENCH_REPOS_DIR", str(tmp_path / "repos"))
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    make_bench_task(tmp_path)
    return tmp_path


async def test_baseline_edits_in_a_real_sandbox_and_writes_a_patch(bench):
    solo = FakeLlm(
        [
            call("read_file", path="mini.py"),
            FIX,
            call("bash", command="python -m pytest -q"),
            json_out(
                SoloResult(declined=False, summary="fix add", files_changed=["mini.py"])
            ),
        ]
    )
    record = await run_pipeline(
        RunRequest(task_id="t-1", run_id="b-1"),
        workflow=build_baseline_workflow(solo),
    )
    assert record.outcome == "patch_written", record.reason
    patch = Path(record.patch_path).read_text()
    assert "-    return a - b" in patch and "+    return a + b" in patch
