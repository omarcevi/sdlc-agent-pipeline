"""The token boundary, checked statically over the source: which code may read the
GitHub token, and that nothing reads it from the environment. The canary test
(test_token_canary.py) checks the same boundary at run time."""

import ast
import os
from pathlib import Path

import app
from app import agent
from app.models import RoleModels
from app.nodes import intake
from app.pipeline import build_workflow
from tests.fakes import FakeLlm

ROOT = Path(app.__file__).resolve().parent.parent
TOKEN_MODULE = "app/github_client.py"
# The only code that may build a GitHub client, and so read the token.
CLIENT_BUILDERS = {
    ("app/nodes/intake.py", "fetch_live_issue"),
    ("app/nodes/finish.py", "open_pr"),
    ("app/nodes/finish.py", "post_failure_comment"),
}
# Anything that names the token file or the code that reads it.
TOKEN_NAMES = (
    "load_token",
    "GITHUB_TOKEN_FILE",
    "TOKEN_FILE_ENV",
    "DEFAULT_TOKEN_FILE",
    "issue-to-pr/github-token",  # the default path
)
# Variables the gh CLI and other GitHub tools read a token from.
TOKEN_VARIABLES = {
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GITHUB_ENTERPRISE_TOKEN",
}


# Task data, not pipeline code: bench/tasks holds the sealed held-out tasks, which
# no test may open, and bench/repos holds the repositories the tasks plant bugs in.
SKIPPED_DATA = (ROOT / "bench" / "tasks", ROOT / "bench" / "repos")


def _pipeline_files(package: str) -> list[Path]:
    """Every .py file under the package, pruning the task and repo data before
    descending, so not even their file names are listed."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(ROOT / package):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if here / d not in SKIPPED_DATA)
        found += [here / name for name in filenames if name.endswith(".py")]
    return sorted(found)


def _sources(*packages: str) -> list[tuple[str, str]]:
    return [
        (path.relative_to(ROOT).as_posix(), path.read_text())
        for package in packages
        for path in _pipeline_files(package)
    ]


def test_the_scan_skips_task_and_repo_data():
    paths = [path for path, _ in _sources("app", "bench")]
    assert paths  # it still scans the pipeline's own code
    assert "bench/run.py" in paths
    # any(), not a list: a failing assertion must not print task file names.
    assert not any(p.startswith(("bench/tasks/", "bench/repos/")) for p in paths)


def test_the_scan_never_enters_task_or_repo_data(monkeypatch):
    # Pruned before descending: not even file names under them are listed.
    entered = []
    real_walk = os.walk

    def spy(top, *args, **kwargs):
        for dirpath, dirnames, filenames in real_walk(top, *args, **kwargs):
            entered.append(Path(dirpath))
            yield dirpath, dirnames, filenames

    monkeypatch.setattr(os, "walk", spy)
    _pipeline_files("bench")
    assert entered
    assert not any(
        data == path or data in path.parents
        for path in entered
        for data in SKIPPED_DATA
    )


class _ClientUses(ast.NodeVisitor):
    """Every place that builds a GitHubClient or names `from_token_file`, with the
    function it is in."""

    def __init__(self) -> None:
        self.function: list[str] = []
        self.uses: list[tuple[str, int]] = []

    def _enter(self, node) -> None:
        self.function.append(node.name)
        self.generic_visit(node)
        self.function.pop()

    visit_FunctionDef = _enter
    visit_AsyncFunctionDef = _enter

    def _here(self, node: ast.AST) -> None:
        where = self.function[-1] if self.function else "<module>"
        self.uses.append((where, getattr(node, "lineno", 0)))

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name == "GitHubClient":
            self._here(node)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "from_token_file":
            self._here(node)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == "from_token_file":
            self._here(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if node.value == "from_token_file":  # getattr(GitHubClient, "...")
            self._here(node)


def test_token_is_read_only_in_the_three_nodes():
    sources = _sources("app", "bench")
    # The token file and its reader are named in one module only.
    for path, text in sources:
        if path != TOKEN_MODULE:
            for name in TOKEN_NAMES:
                assert name not in text, f"{path} mentions {name}"
    builders: set[tuple[str, str]] = set()
    for path, text in sources:
        tree = ast.parse(text, filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    # An alias would hide a call from the check below.
                    assert not (alias.name == "GitHubClient" and alias.asname), path
        if path == TOKEN_MODULE:
            continue
        uses = _ClientUses()
        uses.visit(tree)
        for function, line in uses.uses:
            assert (path, function) in CLIENT_BUILDERS, (
                f"{path}:{line} builds a GitHub client in {function}"
            )
            builders.add((path, function))
    # Each of the three still builds its client (the check above saw them).
    assert builders == CLIENT_BUILDERS


def test_nothing_reads_github_token_env_vars():
    found = []
    for path, text in _sources("app", "bench"):
        for node in ast.walk(ast.parse(text, filename=path)):
            if isinstance(node, ast.Constant) and node.value in TOKEN_VARIABLES:
                found.append(f"{path}:{node.lineno} {node.value}")
    assert found == []


def test_agents_cli_entry_point_serves_the_bench_graph():
    nodes = {node.name: node for node in agent.root_agent.graph.nodes}
    assert not {"human_gate", "route_approval", "open_pr"} & set(nodes)
    assert nodes["fetch_issue"]._func is intake.fetch_issue
    assert agent.app.root_agent is agent.root_agent
    models = RoleModels(planner=FakeLlm([]), coder=FakeLlm([]), reviewer=FakeLlm([]))
    bench = build_workflow(models)

    def edges(workflow) -> list[tuple]:
        return [
            (e.from_node.name, e.to_node.name, e.route) for e in workflow.graph.edges
        ]

    assert edges(agent.root_agent) == edges(bench)
