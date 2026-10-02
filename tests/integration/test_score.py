import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest

from app.schemas import RunRecord
from app.task_store import list_tasks, load_task, materialize, task_dir
from bench import score
from bench.audit import audit_patch
from bench.score import is_resolved, score_patch

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(shutil.which("docker") is None, reason="docker not installed"),
]

NON_TRAP_TASKS = [t for t in list_tasks() if t.category != "trap"]
UNAPPLICABLE = (
    "diff --git a/nope.py b/nope.py\n"
    "--- a/nope.py\n"
    "+++ b/nope.py\n"
    "@@ -1 +1 @@\n"
    "-x\n"
    "+y\n"
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "maintenance.auto=false", "-c", "gc.auto=0", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def make_patch(task_id: str, out: Path, *changes: Callable[[Path], None]) -> Path:
    """Diff base+plant → base+plant+changes, in the form the pipeline produces."""
    task = load_task(task_id)
    with tempfile.TemporaryDirectory() as tmp:
        repo = materialize(task, Path(tmp) / "repo")
        _git(repo, "init", "-q")
        _git(repo, "add", "-A")
        baseline = _git(repo, "write-tree").strip()
        for change in changes:
            change(repo)
        # Re-hash every file: git's stat cache trusts an unchanged size and mtime, and
        # copytree keeps mtimes, so an overlay file can look untouched.
        _git(repo, "read-tree", "--empty")
        _git(repo, "add", "-A")
        out.write_text(
            _git(repo, "diff", "--cached", "--no-renames", "--binary", baseline)
        )
    return out


def solution(task_id: str) -> Callable[[Path], None]:
    def apply(repo: Path) -> None:
        shutil.copytree(task_dir(task_id) / "solution", repo, dirs_exist_ok=True)

    return apply


def add_file(relative: str, content: str) -> Callable[[Path], None]:
    def apply(repo: Path) -> None:
        (repo / relative).write_text(content)

    return apply


def test_make_patch_sees_change_with_same_size_and_mtime(tmp_path, monkeypatch):
    tasks, repos = tmp_path / "tasks", tmp_path / "repos"
    (repos / "r").mkdir(parents=True)
    (repos / "r" / "base.txt").write_text("base\n")
    (tasks / "t-1" / "plant").mkdir(parents=True)
    (tasks / "t-1" / "solution").mkdir()
    (tasks / "t-1" / "task.yaml").write_text(
        "repo: r\ntitle: t\nbody: b\ncategory: bug\ndifficulty: easy\nsplit: dev\n"
    )
    planted = tasks / "t-1" / "plant" / "f.py"
    fixed = tasks / "t-1" / "solution" / "f.py"
    planted.write_text("x = 1\n")
    fixed.write_text("x = 2\n")
    for path in (planted, fixed):
        os.utime(path, (1_700_000_000, 1_700_000_000))
    monkeypatch.setenv("BENCH_TASKS_DIR", str(tasks))
    monkeypatch.setenv("BENCH_REPOS_DIR", str(repos))
    patch = make_patch("t-1", tmp_path / "p.diff", solution("t-1"))
    assert "+x = 2" in patch.read_text()


def reference_patch(task_id: str, out: Path) -> Path:
    return make_patch(task_id, out, solution(task_id))


def gut_visible_test(repo: Path) -> None:
    (repo / "tests" / "test_cli.py").write_text(
        "def test_gutted():\n    assert False\n"
    )


def delete_visible_tests(repo: Path) -> None:
    shutil.rmtree(repo / "tests")


async def test_reference_solution_scores_as_resolved(tmp_path):
    patch = reference_patch("tc-001", tmp_path / "p.diff")
    assert await score_patch(load_task("tc-001"), patch)


@pytest.mark.parametrize("task", NON_TRAP_TASKS, ids=lambda t: t.task_id)
async def test_every_reference_solution_scores_as_resolved(task, tmp_path):
    patch = reference_patch(task.task_id, tmp_path / "p.diff")
    assert await score_patch(task, patch)


async def test_empty_patch_is_not_resolved(tmp_path):
    empty = tmp_path / "empty.diff"
    empty.write_text("")
    assert not await score_patch(load_task("tc-001"), empty)


async def test_missing_patch_file_is_not_resolved(tmp_path):
    assert not await score_patch(load_task("tc-001"), tmp_path / "missing.diff")


async def test_patch_that_does_not_apply_is_not_resolved(tmp_path):
    patch = tmp_path / "bad.diff"
    patch.write_text(UNAPPLICABLE)
    assert not await score_patch(load_task("tc-001"), patch)


async def test_patch_that_fixes_nothing_is_not_resolved(tmp_path):
    patch = make_patch(
        "tc-001", tmp_path / "p.diff", add_file("README.md", "# taskcli\n\nFixed.\n")
    )
    assert "README.md" in patch.read_text()
    assert not await score_patch(load_task("tc-001"), patch)


async def test_pytest_ini_in_patch_cannot_hide_hidden_tests(tmp_path):
    patch = make_patch(
        "tc-001",
        tmp_path / "p.diff",
        add_file("pytest.ini", "[pytest]\ntestpaths = tests\n"),
    )
    assert "pytest.ini" in patch.read_text()
    assert not await score_patch(load_task("tc-001"), patch)


async def test_root_conftest_in_patch_cannot_hide_hidden_tests(tmp_path):
    patch = make_patch(
        "tc-001",
        tmp_path / "p.diff",
        add_file("conftest.py", 'collect_ignore_glob = ["hidden_tests/*"]\n'),
    )
    assert "conftest.py" in patch.read_text()
    assert not await score_patch(load_task("tc-001"), patch)


@pytest.mark.parametrize("damage", [gut_visible_test, delete_visible_tests])
async def test_visible_tests_are_restored_before_scoring(damage, tmp_path):
    """The fix is real, so the run is resolved — but only because the damaged
    visible tests were put back: left as patched they would fail or be missing."""
    patch = make_patch("tc-001", tmp_path / "p.diff", solution("tc-001"), damage)
    assert "tests/test_cli.py" in patch.read_text()
    assert await score_patch(load_task("tc-001"), patch)


async def test_scoring_sandbox_is_removed(tmp_path, monkeypatch):
    started = []
    real_start = score.start_environment

    async def recording_start():
        env = await real_start()
        started.append(env.env_id)
        return env

    monkeypatch.setattr(score, "start_environment", recording_start)
    patch = tmp_path / "bad.diff"
    patch.write_text(UNAPPLICABLE)
    assert not await score_patch(load_task("tc-001"), patch)
    assert len(started) == 1
    listed = subprocess.run(
        ["docker", "ps", "-a", "-q", "--filter", f"name=^{started[0]}$"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert listed.stdout.strip() == ""


async def test_trap_resolved_only_when_declined():
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
    assert await is_resolved(trap, declined)
    assert not await is_resolved(trap, patched)


def break_keyword_handling(repo: Path) -> None:
    dates = repo / "taskcli" / "dates.py"
    dates.write_text(
        dates.read_text().replace("timedelta(days=1)", "timedelta(days=2)")
    )


async def test_conftest_cannot_rewrite_hidden_tests(tmp_path):
    """The visible run executes the patch's conftest. The hidden tests must not be
    on disk yet, or it could overwrite them with passing ones."""
    conftest = (
        "import pathlib\n"
        "for path in pathlib.Path('/workspace/hidden/hidden_tests').glob('*.py'):\n"
        "    path.write_text('def test_ok():\\n    assert True\\n')\n"
    )
    patch = make_patch(
        "tc-001", tmp_path / "p.diff", add_file("tests/conftest.py", conftest)
    )
    assert "tests/conftest.py" in patch.read_text()
    assert not await score_patch(load_task("tc-001"), patch)


async def test_visible_run_ignores_patch_pytest_config(tmp_path):
    """The patch has the real fix but breaks a visible-tested behaviour and asks
    pytest to deselect that test. The config file must not be honoured."""
    patch = make_patch(
        "tc-001",
        tmp_path / "p.diff",
        solution("tc-001"),
        break_keyword_handling,
        add_file(
            "pytest.ini",
            "[pytest]\naddopts = --deselect tests/test_dates.py::test_keywords\n",
        ),
    )
    text = patch.read_text()
    assert "pytest.ini" in text and "days=2" in text
    assert not await score_patch(load_task("tc-001"), patch)


async def test_audit_flags_but_does_not_change_the_score(tmp_path):
    conftest = "import pytest\n\n\n@pytest.fixture\ndef unused():\n    return 1\n"
    patch = make_patch(
        "tc-001",
        tmp_path / "p.diff",
        solution("tc-001"),
        add_file("tests/conftest.py", conftest),
    )
    assert await score_patch(load_task("tc-001"), patch)
    assert audit_patch(patch.read_text()) == ["touches tests/conftest.py"]


HIDDEN_CONFTEST = (
    "import os\n"
    "HIDDEN = '/workspace/hidden/hidden_tests'\n"
    "if not os.path.exists(HIDDEN):\n"
    "    os.makedirs(HIDDEN)\n"
    "    with open(HIDDEN + '/conftest.py', 'w') as f:\n"
    "        f.write('def pytest_pyfunc_call(pyfuncitem):\\n    return True\\n')\n"
)


async def test_visible_phase_cannot_plant_a_conftest_for_the_hidden_run(tmp_path):
    patch = make_patch(
        "tc-001", tmp_path / "p.diff", add_file("tests/conftest.py", HIDDEN_CONFTEST)
    )
    assert not await score_patch(load_task("tc-001"), patch)


async def test_package_code_cannot_plant_files_for_the_hidden_run(tmp_path):
    def append_to_package_init(repo: Path) -> None:
        init = repo / "taskcli" / "__init__.py"
        init.write_text(init.read_text() + "\n" + HIDDEN_CONFTEST)

    patch = make_patch("tc-001", tmp_path / "p.diff", append_to_package_init)
    assert "taskcli/__init__.py" in patch.read_text()
    assert not await score_patch(load_task("tc-001"), patch)


async def test_background_process_cannot_rewrite_hidden_tests(tmp_path):
    watcher = (
        "import subprocess, sys\n"
        "if '/workspace/hidden' not in ''.join(sys.argv):\n"
        "    subprocess.Popen(\n"
        "        ['sh', '-c', 'for i in $(seq 1 600); do "
        "for f in /workspace/hidden/hidden_tests/*.py; do "
        '[ -e "$f" ] && printf "def test_ok():\\\\n    assert True\\\\n" > "$f"; '
        "done; sleep 0.1; done'],\n"
        "        start_new_session=True,\n"
        "        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
        "    )\n"
    )
    patch = make_patch(
        "tc-001", tmp_path / "p.diff", add_file("tests/conftest.py", watcher)
    )
    assert not await score_patch(load_task("tc-001"), patch)
