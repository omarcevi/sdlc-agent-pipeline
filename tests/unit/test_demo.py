import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from app.task_store import load_task
from bench import demo
from bench.demo import demo_tasks, expected_tree_sha

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is missing")

SENTINELS = {
    "solution": "SOLUTION_SENTINEL_71c4",
    "shortcut": "SHORTCUT_SENTINEL_5d2e",
    "hidden": "HIDDEN_SENTINEL_a90b",
}


@pytest.fixture(autouse=True)
def git_identity(tmp_path, monkeypatch):
    config = tmp_path / "gitconfig"
    config.write_text("[user]\n\tname = Owner\n\temail = owner@example.com\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")


def add_task(root: Path, new_id: str, split: str = "dev", **fields) -> None:
    shutil.copytree(root / "tasks" / "t-1", root / "tasks" / new_id)
    path = root / "tasks" / new_id / "task.yaml"
    data = yaml.safe_load(path.read_text())
    data.update(split=split, **fields)
    path.write_text(yaml.safe_dump(data))


def git(directory: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=directory, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_demo_never_includes_heldout_tasks(bench, monkeypatch):
    add_task(bench, "t-2")
    add_task(bench, "mini-h01", split="heldout")
    loaded: list[str] = []
    real = demo.load_task
    monkeypatch.setattr(
        demo, "load_task", lambda task_id: loaded.append(task_id) or real(task_id)
    )
    materialised: list[str] = []
    real_materialize = demo.materialize
    monkeypatch.setattr(
        demo,
        "materialize",
        lambda task, dest, **kw: (
            materialised.append(task.task_id) or real_materialize(task, dest, **kw)
        ),
    )
    assert [t.task_id for t in demo_tasks("mini")] == ["t-1", "t-2"]
    demo.export("mini", bench / "out")
    assert "mini-h01" not in loaded
    assert "mini-h01" not in materialised
    refs = git(bench / "out" / "mini", "branch", "--list", "demo/*")
    assert "mini-h01" not in refs


def test_expected_tree_sha_is_stable_and_content_sensitive(bench):
    task = load_task("t-1")
    first = expected_tree_sha(task)
    assert first == expected_tree_sha(task)
    (bench / "tasks" / "t-1" / "plant" / "mini.py").write_text("x = 1\n")
    assert expected_tree_sha(task) != first


def test_export_branches_have_the_expected_trees_and_no_parents(bench):
    add_task(bench, "t-2")
    trees = demo.export("mini", bench / "out")
    repo = bench / "out" / "mini"
    assert set(trees) == {"main", "demo/t-1", "demo/t-2"}
    for branch, tree in trees.items():
        assert git(repo, "rev-parse", f"{branch}^{{tree}}") == tree
        assert git(repo, "rev-list", "--parents", "-n1", branch).split() == [
            git(repo, "rev-parse", branch)
        ]
    assert trees["demo/t-1"] == expected_tree_sha(load_task("t-1"))
    assert trees["main"] == demo.base_tree_sha("mini")
    assert git(repo, "log", "-1", "--format=%s", "main") == "Demo base"
    assert git(repo, "log", "-1", "--format=%s", "demo/t-1") == "Demo state for t-1"
    message = git(repo, "log", "-1", "--format=%B", "demo/t-1")
    assert message == "Demo state for t-1"
    assert git(repo, "log", "-1", "--format=%an <%ae>", "main") == (
        "Owner <owner@example.com>"
    )


def test_export_refuses_without_a_git_identity(bench, monkeypatch, tmp_path):
    empty = tmp_path / "empty-gitconfig"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    with pytest.raises(demo.DemoError, match=r"user\.name"):
        demo.export("mini", bench / "out")
    assert not (bench / "out" / "mini").exists()


NOREPLY = "12345+owner@users.noreply.github.com"


def test_export_uses_the_given_email_for_author_and_committer(bench):
    add_task(bench, "t-2")
    demo.export("mini", bench / "out", email=NOREPLY)
    repo = bench / "out" / "mini"
    for branch in ("main", "demo/t-1", "demo/t-2"):
        assert git(repo, "log", "-1", "--format=%an <%ae>|%cn <%ce>", branch) == (
            f"Owner <{NOREPLY}>|Owner <{NOREPLY}>"
        )


@pytest.mark.parametrize(
    "bad",
    ["", "nobody", "a b@example.com", "<a@example.com>", "a@", "@example.com", "a@b c"],
)
def test_export_refuses_an_invalid_email_without_echoing_it(bench, capsys, bad):
    with pytest.raises(demo.DemoError) as raised:
        demo.export("mini", bench / "out", email=bad)
    assert str(raised.value) == "--email is not a valid address"
    assert not (bench / "out" / "mini").exists()
    out = str(bench / "out2")
    status = demo.main(["export", "--repo", "mini", "--out", out, f"--email={bad}"])
    assert status == 2
    captured = capsys.readouterr()
    assert captured.err.strip() == "--email is not a valid address"
    assert captured.out == ""


def test_identity_does_not_change_tree_shas(bench):
    first = demo.export("mini", bench / "out-a", email="a@example.com")
    second = demo.export("mini", bench / "out-b", email="b@example.com")
    assert first == second
    repo_a, repo_b = bench / "out-a" / "mini", bench / "out-b" / "mini"
    for branch in first:
        assert git(repo_a, "rev-parse", branch) != git(repo_b, "rev-parse", branch)


def test_verify_reports_a_mismatch(bench, capsys):
    demo.export("mini", bench / "out")
    clone = bench / "clone" / "mini"
    git(bench, "clone", "-q", str(bench / "out" / "mini"), str(clone))
    assert demo.main(["verify", "--dir", str(clone)]) == 0
    assert "demo/t-1: ok" in capsys.readouterr().out
    (bench / "tasks" / "t-1" / "plant" / "mini.py").write_text("x = 1\n")
    assert demo.main(["verify", "--dir", str(clone)]) == 1
    out = capsys.readouterr().out
    assert "demo/t-1: mismatch" in out
    assert "main: ok" in out


def test_issue_writes_the_task_text_verbatim(bench, capsys):
    body = "Line one.\n\n  indented `code` and unicode é\n"
    add_task(bench, "t-2", title='Odd title: "quoted" $x', body=body)
    out = bench / "issue"
    code = demo.main(
        ["issue", "--task", "t-2", "--repo", "owner/name", "--out", str(out)]
    )
    assert code == 0
    assert (out / "title.txt").read_text() == 'Odd title: "quoted" $x'
    assert (out / "body.md").read_text() == body
    printed = capsys.readouterr().out.strip()
    assert printed == (
        f'gh issue create --repo owner/name --title "$(cat {out}/title.txt)" '
        f"--body-file {out}/body.md"
    )


def test_issue_and_export_refuse_heldout_without_reading_it(bench, capsys):
    add_task(bench, "mini-h01", split="heldout")
    code = demo.main(
        ["issue", "--task", "mini-h01", "--repo", "o/n", "--out", str(bench / "i")]
    )
    assert code == 2
    assert capsys.readouterr().err.strip() == "task is not in the dev split"
    assert not (bench / "i").exists()


def test_trees_lists_dev_tasks_only(bench, capsys):
    add_task(bench, "mini-h01", split="heldout")
    assert demo.main(["trees"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [f"t-1 {expected_tree_sha(load_task('t-1'))}"]


def test_no_overlay_content_reaches_a_tree_or_an_issue_body(bench):
    task = bench / "tasks" / "t-1"
    (task / "solution" / "extra.py").write_text(f"# {SENTINELS['solution']}\n")
    (task / "solution" / "mini.py").write_text(f"# {SENTINELS['solution']}\n")
    (task / "shortcut").mkdir()
    (task / "shortcut" / "mini.py").write_text(f"# {SENTINELS['shortcut']}\n")
    (task / "hidden_tests" / "test_hidden_mini.py").write_text(
        f"# {SENTINELS['hidden']}\n"
    )
    trees = demo.export("mini", bench / "out")
    repo = bench / "out" / "mini"
    for branch in trees:
        names = git(repo, "ls-tree", "-r", "--name-only", branch).splitlines()
        assert not any(
            part in name for name in names for part in ("hidden_tests", "extra.py")
        )
        for name in names:
            blob = git(repo, "show", f"{branch}:{name}")
            for sentinel in SENTINELS.values():
                assert sentinel not in blob
    demo.main(
        ["issue", "--task", "t-1", "--repo", "o/n", "--out", str(bench / "issue")]
    )
    text = (bench / "issue" / "title.txt").read_text() + (
        bench / "issue" / "body.md"
    ).read_text()
    for sentinel in SENTINELS.values():
        assert sentinel not in text
