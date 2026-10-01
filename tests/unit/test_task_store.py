import pytest

from app.task_store import list_tasks, load_task, materialize, test_files
from tests.fakes import make_bench_task


@pytest.fixture
def bench_root(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCH_TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("BENCH_REPOS_DIR", str(tmp_path / "repos"))
    make_bench_task(tmp_path)
    return tmp_path


def test_load_and_list(bench_root):
    task = load_task("t-1")
    assert (task.repo, task.category, task.split) == ("mini", "bug", "dev")
    assert [t.task_id for t in list_tasks()] == ["t-1"]


def test_materialize_layers(bench_root, tmp_path):
    task = load_task("t-1")
    planted = materialize(task, tmp_path / "a")
    assert "a - b" in (planted / "mini.py").read_text()
    assert not (planted / "hidden_tests").exists()
    solved = materialize(
        task, tmp_path / "b", with_solution=True, with_hidden_tests=True
    )
    assert "a + b" in (solved / "mini.py").read_text()
    assert (solved / "hidden_tests" / "test_hidden_mini.py").exists()


def test_test_files(bench_root):
    assert test_files(bench_root / "repos" / "mini") == ["tests/test_mini.py"]


def test_tempting_and_levers_default_to_false_and_empty(bench_root):
    task = load_task("t-1")
    assert task.tempting is False
    assert task.levers == []


def test_materialize_with_shortcut_overlays_after_plant(bench_root, tmp_path):
    shortcut = bench_root / "tasks" / "t-1" / "shortcut"
    shortcut.mkdir()
    (shortcut / "mini.py").write_text("def add(a, b):\n    return 42\n")
    task = load_task("t-1")
    copy = materialize(task, tmp_path / "s", with_shortcut=True)
    assert "42" in (copy / "mini.py").read_text()
    assert "a - b" in (materialize(task, tmp_path / "p") / "mini.py").read_text()


def test_materialize_with_solution_and_shortcut_is_an_error(bench_root, tmp_path):
    with pytest.raises(ValueError):
        materialize(
            load_task("t-1"), tmp_path / "x", with_solution=True, with_shortcut=True
        )


def test_repr_hides_task_text():
    from app.task_store import TaskSpec

    spec = TaskSpec(
        task_id="x-1",
        repo="r",
        title="SECRET-TITLE",
        body="SECRET-BODY",
        category="bug",
        difficulty="easy",
        split="dev",
    )
    assert "SECRET-TITLE" not in repr(spec) and "SECRET-BODY" not in repr(spec)
    assert spec.title == "SECRET-TITLE" and spec.body == "SECRET-BODY"
    assert spec == spec.model_copy()


def test_materialize_never_copies_caches(bench_root, tmp_path):
    for base in (bench_root / "repos" / "mini", bench_root / "tasks" / "t-1" / "plant"):
        (base / "__pycache__").mkdir(parents=True, exist_ok=True)
        (base / "__pycache__" / "x.pyc").write_bytes(b"x")
        (base / ".pytest_cache").mkdir(exist_ok=True)
        (base / ".pytest_cache" / "v").write_text("x")
        (base / "stale.pyc").write_bytes(b"x")
    out = materialize(load_task("t-1"), tmp_path / "out")
    assert not list(out.rglob("__pycache__"))
    assert not list(out.rglob(".pytest_cache"))
    assert not list(out.rglob("*.pyc"))
