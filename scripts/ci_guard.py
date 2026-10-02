"""Dispatch guard for the manual paid-run workflow.

Standard library only: it runs before `uv sync`, imports nothing from `app` or
`bench`, and reads no file inside any task directory. Held-out task directories
are told apart by name only.

Exit 0: allowed. Prints `worst case: $<cost> for <runs> runs` and, when
GITHUB_OUTPUT is set, appends tasks/system/preset/repeats/runs lines to it.
Exit 2: refused, one `error: ...` line on stderr and nothing on stdout.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

SMOKE_TASKS = ("tc-001", "tc-003", "sr-001")
EVAL_RUNS = 5
LIMIT_USD = Decimal("25.00")
DEV_ID = re.compile(r"[a-z]{2}-[0-9]{3}")
HELD_OUT = re.compile(r"-h[0-9][0-9]", re.IGNORECASE)


class Refused(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # argparse would print usage and exit 2
        raise Refused(message)


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog="ci_guard", add_help=False, allow_abbrev=False)
    p.add_argument("--kind", required=True, choices=("smoke", "eval", "bench"))
    p.add_argument("--ref", required=True)
    p.add_argument("--system", choices=("multi", "single"))
    p.add_argument("--preset", choices=("flash", "pro", "mixed"))
    p.add_argument("--tasks")
    p.add_argument("--repeats")
    p.add_argument("--accept-over-25", action="store_true")
    p.add_argument("--tasks-dir", default="bench/tasks")
    p.add_argument("--budget-usd", default="1.00")
    return p


def _dev_tasks(tasks_dir: str) -> list[str]:
    try:
        names = os.listdir(tasks_dir)
    except OSError:
        raise Refused("tasks directory not readable") from None
    return sorted(n for n in names if DEV_ID.fullmatch(n))


def _listed_tasks(raw: str, tasks_dir: str) -> list[str]:
    if HELD_OUT.search(raw):
        raise Refused("held-out tasks never run in CI")
    ids = [item.strip() for item in raw.split(",")]
    for i in ids:
        if not DEV_ID.fullmatch(i):
            raise Refused("bad task id")
    if len(set(ids)) != len(ids):
        raise Refused("task listed twice")
    known = set(_dev_tasks(tasks_dir))
    if any(i not in known for i in ids):
        raise Refused("unknown task")
    return ids


def _plan(a: argparse.Namespace) -> tuple[list[str], str, str, int, int]:
    """Return tasks, system, preset, repeats and runs."""
    if a.ref != "refs/heads/main":
        raise Refused("paid runs start from main only")
    if a.kind in ("smoke", "eval"):
        if a.tasks is not None or a.system or a.preset or a.repeats is not None:
            raise Refused(f"{a.kind} takes no task, system, preset or repeats")
        if a.kind == "smoke":
            return list(SMOKE_TASKS), "multi", "flash", 1, len(SMOKE_TASKS)
        return [], "multi", "flash", 1, EVAL_RUNS
    system = a.system or "multi"
    preset = a.preset or "flash"
    if system == "single" and preset == "mixed":
        raise Refused("single does not accept mixed")
    repeats_text = "1" if a.repeats is None else a.repeats
    if not re.fullmatch(r"[1-5]", repeats_text):
        raise Refused("repeats must be 1 to 5")
    repeats = int(repeats_text)
    if a.tasks is not None and a.tasks.strip() != "":
        tasks = _listed_tasks(a.tasks, a.tasks_dir)
    else:
        tasks = _dev_tasks(a.tasks_dir)
        if not tasks:
            raise Refused("no dev tasks found")
    return tasks, system, preset, repeats, len(tasks) * repeats


def main(argv: list[str]) -> int:
    try:
        a = _parser().parse_args(argv)
        tasks, system, preset, repeats, runs = _plan(a)
        try:
            budget = Decimal(a.budget_usd)
        except InvalidOperation:
            raise Refused("budget-usd must be a number") from None
        if not budget.is_finite() or budget <= 0:
            raise Refused("budget-usd must be a number")
        try:
            cost = (budget * runs).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except InvalidOperation:
            raise Refused("budget-usd must be a number") from None
        if cost > LIMIT_USD and not a.accept_over_25:
            raise Refused(
                f"worst case ${cost} is over $25; pass accept_over_25 to run it"
            )
    except Refused as e:
        print(f"error: {' '.join(str(e).split())}", file=sys.stderr)
        return 2
    print(f"worst case: ${cost} for {runs} runs")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with Path(out).open("a", encoding="utf-8") as f:
            f.write(
                f"tasks={','.join(tasks)}\nsystem={system}\npreset={preset}\n"
                f"repeats={repeats}\nruns={runs}\n"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
