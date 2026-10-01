"""Demo repositories: export the dev tasks of a bench repo as a git repository, one
branch per task, ready for the owner to push (Task 7, owner approval).

uv run python -m bench.demo export --repo REPO --out DIR
uv run python -m bench.demo trees
uv run python -m bench.demo verify --dir DIR/REPO
uv run python -m bench.demo issue --task TASK_ID --repo OWNER/NAME --out DIR

`main` is the clean repo; `demo/<task-id>` is that repo with the task's `plant/` on top
and nothing else: no `solution/`, `shortcut/` or `hidden_tests/` content reaches a tree
or an issue body. Held-out tasks are never listed, exported or read: their ids are
refused by name before any file of the task is opened. Nothing here talks to GitHub;
the owner pushes the branches and files the issues by hand.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from app.task_store import (
    _NO_CACHES,
    TaskSpec,
    load_task,
    materialize,
    repos_dir,
    task_dir,
    tasks_dir,
)

NOT_DEV = "task is not in the dev split"
_HELDOUT_ID = re.compile(r"-h\d\d$")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_REPO_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_SLUG = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
BASE_MESSAGE = "Demo base"


class DemoError(Exception):
    pass


def is_heldout_id(task_id: str) -> bool:
    return bool(_HELDOUT_ID.search(task_id))


def dev_task(task_id: str) -> TaskSpec | None:
    """The task, only if it exists and is in the dev split. A held-out id is refused
    by its name before any file of the task is opened."""
    if is_heldout_id(task_id) or not _SAFE_ID.fullmatch(task_id):
        return None
    if not (task_dir(task_id) / "task.yaml").is_file():
        return None
    task = load_task(task_id)
    return task if task.split == "dev" else None


def dev_tasks() -> list[TaskSpec]:
    """Every dev task, sorted by id. Held-out directories are skipped by name."""
    tasks = []
    for path in sorted(tasks_dir().glob("*/task.yaml")):
        task_id = path.parent.name
        if is_heldout_id(task_id):
            continue
        task = load_task(task_id)
        if task.split == "dev":
            tasks.append(task)
    return tasks


def demo_tasks(repo: str) -> list[TaskSpec]:
    """Every dev task of `repo`, sorted by id (decision 6A)."""
    return [task for task in dev_tasks() if task.repo == repo]


def _git(
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> str:
    result = subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args],
        cwd=cwd,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise DemoError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip() if result.returncode == 0 else ""


def _tree_of(directory: Path) -> str:
    """The tree sha of a directory's files, in a throwaway repository."""
    with tempfile.TemporaryDirectory() as tmp:
        git_dir = Path(tmp) / "git"
        _git("init", "-q", str(git_dir))
        env = {"GIT_DIR": str(git_dir / ".git"), "GIT_WORK_TREE": str(directory)}
        _git("add", "-A", env=env)
        return _git("write-tree", env=env)


def expected_tree_sha(task: TaskSpec) -> str:
    """Tree sha of base + plant, with no other overlay."""
    with tempfile.TemporaryDirectory() as tmp:
        return _tree_of(materialize(task, Path(tmp) / "repo"))


def base_tree_sha(repo: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "repo"
        shutil.copytree(repos_dir() / repo, target, ignore=_NO_CACHES)
        return _tree_of(target)


def _identity(cwd: Path) -> dict[str, str]:
    name = _git("config", "user.name", cwd=cwd, check=False)
    email = _git("config", "user.email", cwd=cwd, check=False)
    if not name or not email:
        raise DemoError("git user.name and user.email must be set to export")
    return {
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
    }


def _commit_tree(
    git_dir: Path, directory: Path, branch: str, message: str, who: dict[str, str]
) -> str:
    env = {
        **who,
        "GIT_DIR": str(git_dir),
        "GIT_WORK_TREE": str(directory),
        "GIT_INDEX_FILE": str(git_dir / "demo-index"),
    }
    _git("add", "-A", env=env)
    tree = _git("write-tree", env=env)
    commit = _git("commit-tree", tree, "-m", message, env=env)
    _git("update-ref", f"refs/heads/{branch}", commit, env=env)
    (git_dir / "demo-index").unlink(missing_ok=True)
    return tree


def export(repo: str, out: Path) -> dict[str, str]:
    """A git repository at out/<repo>: `main` and one root-commit branch per demo task.
    Returns {branch: tree sha}."""
    if not _REPO_NAME.fullmatch(repo) or not (repos_dir() / repo).is_dir():
        raise DemoError(f"unknown repo: {repo}")
    tasks = demo_tasks(repo)
    target = Path(out) / repo
    if target.exists():
        raise DemoError(f"{target} already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    who = _identity(target.parent)
    _git("init", "-q", "-b", "main", str(target))
    git_dir = target / ".git"
    trees: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "base"
        shutil.copytree(repos_dir() / repo, base, ignore=_NO_CACHES)
        trees["main"] = _commit_tree(git_dir, base, "main", BASE_MESSAGE, who)
        for task in tasks:
            work = materialize(task, Path(tmp) / task.task_id)
            branch = f"demo/{task.task_id}"
            message = f"Demo state for {task.task_id}"
            trees[branch] = _commit_tree(git_dir, work, branch, message, who)
    _git("checkout", "-q", "main", cwd=target)
    return trees


def verify(directory: Path) -> dict[str, bool]:
    """Compare fetched origin refs with the expected trees: {branch: matches}."""
    directory = Path(directory)
    repo = directory.resolve().name
    expected = {"main": base_tree_sha(repo)}
    for task in demo_tasks(repo):
        expected[f"demo/{task.task_id}"] = expected_tree_sha(task)
    return {
        branch: _git(
            "rev-parse", f"origin/{branch}^{{tree}}", cwd=directory, check=False
        )
        == tree
        for branch, tree in expected.items()
    }


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def _export(args: argparse.Namespace) -> int:
    for branch, tree in export(args.repo, Path(args.out)).items():
        print(f"{branch} {tree}")
    return 0


def _trees(_: argparse.Namespace) -> int:
    for task in dev_tasks():
        print(f"{task.task_id} {expected_tree_sha(task)}")
    return 0


def _verify(args: argparse.Namespace) -> int:
    results = verify(Path(args.dir))
    for branch, ok in results.items():
        print(f"{branch}: {'ok' if ok else 'mismatch'}")
    return 0 if all(results.values()) else 1


def _issue(args: argparse.Namespace) -> int:
    task = dev_task(args.task)
    if task is None:
        return _fail(NOT_DEV)
    if not _SLUG.fullmatch(args.repo):
        return _fail("--repo must be OWNER/NAME")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "title.txt").open("w", newline="") as handle:
        handle.write(task.title)
    with (out / "body.md").open("w", newline="") as handle:
        handle.write(task.body)
    print(
        f'gh issue create --repo {args.repo} --title "$(cat {out}/title.txt)" '
        f"--body-file {out}/body.md"
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bench.demo")
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export", help="build the demo git repository locally")
    exp.add_argument("--repo", required=True)
    exp.add_argument("--out", required=True)
    sub.add_parser("trees", help="print the expected tree sha of every demo task")
    ver = sub.add_parser("verify", help="compare fetched origin refs to the trees")
    ver.add_argument("--dir", required=True)
    iss = sub.add_parser("issue", help="write a task's issue text, print the command")
    iss.add_argument("--task", required=True)
    iss.add_argument("--repo", required=True)
    iss.add_argument("--out", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    handler = {"export": _export, "trees": _trees, "verify": _verify, "issue": _issue}
    try:
        return handler[args.command](args)
    except DemoError as exc:
        return _fail(str(exc))


if __name__ == "__main__":
    sys.exit(main())
