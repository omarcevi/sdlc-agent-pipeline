"""Reviewer probes: prepared patches shown to the reviewer to measure whether it
catches a wrong patch that passes the visible tests, and whether it approves a right one.

A probe is `bench/review_probes/<probe-id>/{probe.yaml, patch.diff}`. `probe.yaml` holds
the task id, the kind (`bad` or `good`) and where the patch came from. The kind is read
only by this module and the harness, never by an agent.

uv run python -m bench.probes validate [--probes rp-01,...]
uv run python -m bench.probes from-overlay --task TASK_ID --id rp-NN
uv run python -m bench.probes candidates results/<single>.json [...] [--runs-dir runs]

Probes use dev tasks only. A held-out task id is refused before anything of the task or
the probe's patch is read, with a message that names nothing from either.
"""

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

from app.environment.factory import check_environment_config
from app.task_store import TaskSpec, load_task, materialize, task_dir, test_files

NOT_DEV = "task is not in the dev split"
_HELDOUT_ID = re.compile(r"-h\d\d$")
_PROBE_ID = re.compile(r"[a-z0-9][a-z0-9-]*")
_LABEL_WORD = re.compile(r"shortcut|probe|bad patch|good patch", re.IGNORECASE)
_DIFF_PATH = re.compile(r"^(?:diff --git a/(.+) b/(.+)|rename (?:from|to) (.+))$")


class ProbeSpec(BaseModel):
    probe_id: str
    task_id: str
    kind: Literal["bad", "good"]
    source: Literal["shortcut", "bench-run", "hand-written"]
    source_run: str | None = None
    note: str = ""


@dataclass(frozen=True)
class PatchChecks:
    """What the sandbox said about a probe patch. `hidden_passes` is None when the
    hidden tests were not run (the patch did not apply, or failed the visible tests)."""

    applies: bool
    visible_passes: bool
    hidden_passes: bool | None


def probes_dir() -> Path:
    return Path(os.environ.get("REVIEW_PROBES_DIR", "bench/review_probes"))


def _probe_path(probe_id: str) -> Path:
    if not _PROBE_ID.fullmatch(probe_id):
        raise ValueError(f"invalid probe id {probe_id!r}")
    return probes_dir() / probe_id


def load_probe(probe_id: str) -> ProbeSpec:
    data = yaml.safe_load((_probe_path(probe_id) / "probe.yaml").read_text())
    return ProbeSpec(probe_id=probe_id, **data)


def list_probes() -> list[ProbeSpec]:
    root = probes_dir()
    if not root.is_dir():
        return []
    return [load_probe(p.parent.name) for p in sorted(root.glob("*/probe.yaml"))]


def patch_file(probe_id: str) -> Path:
    """The probe's patch. The graph reads this file and never the probe's yaml."""
    return _probe_path(probe_id) / "patch.diff"


def _read_patch(probe: ProbeSpec) -> str:
    return patch_file(probe.probe_id).read_text()


def dev_task(task_id: str) -> TaskSpec | None:
    """The task, only if it exists and is in the dev split. A held-out id is refused
    by its name before any file of the task is opened."""
    if _HELDOUT_ID.search(task_id) or not _PROBE_ID.fullmatch(task_id):
        return None
    if not (task_dir(task_id) / "task.yaml").is_file():
        return None
    task = load_task(task_id)
    return task if task.split == "dev" else None


def patch_paths(patch: str) -> set[str]:
    """Every repo path a unified diff names (both sides of a rename included)."""
    paths: set[str] = set()
    for line in patch.splitlines():
        if match := _DIFF_PATH.match(line):
            paths.update(p for p in match.groups() if p)
    return paths


def _touches_protected(patch: str, protected: list[str]) -> bool:
    guarded = set(protected)
    return any(
        path in guarded or path == ".github" or path.startswith(".github/")
        for path in patch_paths(patch)
    )


def _has_label_word(patch: str) -> bool:
    return any(
        _LABEL_WORD.search(line[1:])
        for line in patch.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )


async def check_patch(task: TaskSpec, patch: str) -> PatchChecks:
    """Apply `patch` to base+plant in a fresh sandbox and run the visible tests; if
    they pass, do the same in a second sandbox for the hidden tests. Same sandboxes,
    commands and restoring of test files as the scorer."""
    from app.environment.factory import start_environment
    from bench import score

    visible_env = await start_environment()
    try:
        if not await score._prepare(visible_env, task, patch):
            return PatchChecks(False, False, None)
        visible = await score._passes(visible_env, score.VISIBLE_CMD)
    finally:
        await score._release(visible_env)
    if not visible:
        return PatchChecks(True, False, None)
    hidden_env = await start_environment()
    try:
        await score._prepare(hidden_env, task, patch)
        await hidden_env.upload_dir(
            task_dir(task.task_id) / "hidden_tests", score.HIDDEN_DIR
        )
        hidden = await score._passes(hidden_env, score.HIDDEN_CMD)
    finally:
        await score._release(hidden_env)
    return PatchChecks(True, True, hidden)


def validate_probe(probe: ProbeSpec) -> list[str]:
    """Problems with a probe, as fixed strings; empty when it is sound. A task that
    is not a dev task ends the check before anything else is read."""
    task = dev_task(probe.task_id)
    if task is None:
        return [NOT_DEV]
    try:
        patch = _read_patch(probe)
    except OSError:
        return ["patch does not apply"]
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        protected = test_files(materialize(task, Path(tmp) / "repo"))
    if _touches_protected(patch, protected):
        problems.append("patch touches a protected test file or .github/")
    if _has_label_word(patch):
        problems.append("patch contains a label word")
    checks = asyncio.run(check_patch(task, patch))
    if not checks.applies:
        problems.insert(0, "patch does not apply")
    elif not checks.visible_passes:
        problems.append("visible tests fail")
    elif probe.kind == "bad" and checks.hidden_passes:
        problems.append("bad probe passes the hidden tests")
    elif probe.kind == "good" and not checks.hidden_passes:
        problems.append("good probe fails the hidden tests")
    return problems


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "core.autocrlf=false",
            "-c",
            "maintenance.auto=false",
            "-c",
            "gc.auto=0",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def overlay_patch(task: TaskSpec) -> str:
    """The task's shortcut/ overlay as a patch against base+plant, in the form the
    pipeline's diff takes. Only file copies and git plumbing run, never repo code."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = materialize(task, Path(tmp) / "repo")
        _git(repo, "init", "-q")
        _git(repo, "add", "-A")
        baseline = _git(repo, "write-tree").strip()
        shutil.copytree(
            task_dir(task.task_id) / "shortcut",
            repo,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
        )
        # Re-hash every file: git's stat cache trusts an unchanged size and mtime,
        # and copytree keeps mtimes, so an overlay file can look untouched.
        _git(repo, "read-tree", "--empty")
        _git(repo, "add", "-A")
        return _git(repo, "diff", "--cached", "--no-renames", "--binary", baseline)


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def _select(ids: str | None) -> list[ProbeSpec]:
    if not ids:
        return list_probes()
    return [load_probe(i.strip()) for i in ids.split(",") if i.strip()]


def _validate(args: argparse.Namespace) -> int:
    failures = 0
    for probe in _select(args.probes):
        try:
            problems = validate_probe(probe)
        except Exception as exc:  # a message could quote task or patch text
            print(f"{probe.probe_id}: error ({type(exc).__name__})")
            failures += 1
            continue
        print(f"{probe.probe_id}: {'ok' if not problems else '; '.join(problems)}")
        failures += bool(problems)
    return 1 if failures else 0


def _from_overlay(args: argparse.Namespace) -> int:
    task = dev_task(args.task)
    if task is None:
        return _fail(NOT_DEV)
    directory = _probe_path(args.id)
    if directory.exists():
        return _fail(f"probe {args.id} already exists")
    if not (task_dir(task.task_id) / "shortcut").is_dir():
        return _fail(f"task {task.task_id} has no shortcut/")
    patch = overlay_patch(task)
    if not patch.strip():
        return _fail("the shortcut overlay changes nothing")
    directory.mkdir(parents=True)
    (directory / "patch.diff").write_text(patch)
    (directory / "probe.yaml").write_text(
        yaml.safe_dump(
            {
                "task_id": task.task_id,
                "kind": "bad",
                "source": "shortcut",
                "source_run": None,
                "note": "the task's shortcut overlay",
            },
            sort_keys=False,
        )
    )
    print(f"{args.id}: wrote {directory / 'patch.diff'}")
    return 0


def _candidates(args: argparse.Namespace) -> int:
    runs = Path(args.runs_dir)
    found: dict[str, str] = {}
    for path in args.results:
        for row in json.loads(Path(path).read_text()):
            task_id = row.get("task_id", "")
            if (
                row.get("system") != "single"
                or row.get("split") != "dev"
                or _HELDOUT_ID.search(task_id)
                or row.get("outcome") != "patch_written"
                or row.get("crashed")
                or not (runs / row["run_id"] / "patch.diff").is_file()
            ):
                continue
            found[row["run_id"]] = (
                f"{row['run_id']} {task_id} "
                f"{'resolved' if row.get('resolved') else 'unresolved'}"
            )
    for run_id in sorted(found):
        print(found[run_id])
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bench.probes")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="check probes against the tests")
    validate.add_argument("--probes", help="comma-separated probe ids (default: all)")
    overlay = sub.add_parser("from-overlay", help="make a bad probe from a shortcut/")
    overlay.add_argument("--task", required=True)
    overlay.add_argument("--id", required=True)
    candidates = sub.add_parser(
        "candidates", help="list single-agent runs that could become probes"
    )
    candidates.add_argument("results", nargs="+")
    candidates.add_argument("--runs-dir", default=os.environ.get("RUNS_DIR", "runs"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    load_dotenv()
    try:
        check_environment_config()
    except ValueError as exc:
        return _fail(str(exc))
    handler = {
        "validate": _validate,
        "from-overlay": _from_overlay,
        "candidates": _candidates,
    }[args.command]
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
