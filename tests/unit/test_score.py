import subprocess
import tempfile
from pathlib import Path

from app.schemas import RunRecord
from app.task_store import load_task, materialize, task_dir
from bench.score import is_resolved, score_patch


def reference_patch(task_id: str, out: Path) -> Path:
    """Diff base+plant → base+plant+solution, as the pipeline would produce it."""
    task = load_task(task_id)
    with tempfile.TemporaryDirectory() as tmp:
        repo = materialize(task, Path(tmp) / "repo")
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "commit",
                "-qm",
                "base",
            ],
            cwd=repo,
            check=True,
        )
        subprocess.run(
            ["cp", "-R", f"{task_dir(task_id) / 'solution'}/.", str(repo)], check=True
        )
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        diff = subprocess.run(
            ["git", "diff", "--cached", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    out.write_text(diff)
    return out


def test_reference_solution_scores_as_resolved(tmp_path):
    patch = reference_patch("tc-001", tmp_path / "p.diff")
    assert score_patch(load_task("tc-001"), patch)


def test_empty_patch_is_not_resolved(tmp_path):
    empty = tmp_path / "empty.diff"
    empty.write_text("")
    assert not score_patch(load_task("tc-001"), empty)


def test_trap_resolved_only_when_declined():
    trap = load_task("tc-005")
    declined = RunRecord(
        task_id="tc-005", run_id="r", outcome="declined", failure_kind="none"
    )
    patched = RunRecord(
        task_id="tc-005",
        run_id="r",
        outcome="patch_written",
        failure_kind="none",
        patch_path="x",
    )
    assert is_resolved(trap, declined) and not is_resolved(trap, patched)
