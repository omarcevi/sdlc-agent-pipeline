"""Turn benchmark result files into a Markdown report.

uv run python -m bench.report results/<file>.json [...] --out docs/results/<name>.md
"""

import argparse
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

FLASH = "gemini-3.8-flash"
PRO = "gemini-3.1-pro-preview"
# Display only: the real preset definitions live in the preset module. This
# mapping only labels the "models" column (planner / coder / reviewer).
_PRESET_MODELS = {
    "flash": (FLASH, FLASH, FLASH),
    "pro": (PRO, PRO, PRO),
    "mixed": (PRO, FLASH, PRO),
}

_DEFAULTS = {
    "system": "multi",
    "preset": "flash",
    "repeat": 1,
    "audit": [],
    "infra_retries": 0,
    "crashed": False,
}
_NUMBERS = (
    "cost_usd",
    "duration_s",
    "tool_calls",
    "tokens_in",
    "tokens_out",
    "test_attempts",
    "review_rounds",
)


def load_rows(paths: list[Path]) -> list[dict]:
    """Read result files, filling the fields older files lack."""
    rows: list[dict] = []
    for path in paths:
        for raw in json.loads(Path(path).read_text()):
            row = dict(raw)
            for key, default in _DEFAULTS.items():
                if key not in row:
                    row[key] = list(default) if isinstance(default, list) else default
            for key in _NUMBERS:
                if row.get(key) is None:
                    row[key] = 0
            rows.append(row)
    return rows


@dataclass
class Summary:
    system: str
    preset: str
    runs: int = 0
    tasks: int = 0
    repeats: int = 0
    resolve_rate_mean: float = 0.0
    resolve_rate_min: float = 0.0
    resolve_rate_max: float = 0.0
    clean_resolve_rate_mean: float = 0.0
    flagged_resolved: int = 0
    by_category: dict[str, tuple[int, int]] = field(default_factory=dict)
    cost_per_run_mean: float = 0.0
    duration_median_s: float = 0.0
    agent_failures: int = 0
    budget_failures: int = 0
    infra_failures: int = 0  # infra failures that did not crash the runner
    crashed: int = 0
    flagged: list[tuple[str, list[str]]] = field(default_factory=list)


def _counted(r: dict) -> bool:
    """A run counts toward the resolve rate unless it was infra or crashed."""
    return not r["crashed"] and r["failure_kind"] != "infra"


def _summarize_group(system: str, preset: str, rows: list[dict]) -> Summary:
    s = Summary(system=system, preset=preset, runs=len(rows))
    s.tasks = len({r["task_id"] for r in rows})
    by_repeat: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        by_repeat[r["repeat"]].append(r)
    s.repeats = len(by_repeat)

    rates, clean_rates = [], []
    for repeat_rows in by_repeat.values():
        counted = [r for r in repeat_rows if _counted(r)]
        if not counted:
            continue
        rates.append(sum(bool(r["resolved"]) for r in counted) / len(counted))
        clean = sum(bool(r["resolved"]) and not r["audit"] for r in counted)
        clean_rates.append(clean / len(counted))
    if rates:
        s.resolve_rate_mean = statistics.fmean(rates)
        s.resolve_rate_min, s.resolve_rate_max = min(rates), max(rates)
        s.clean_resolve_rate_mean = statistics.fmean(clean_rates)

    cats: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in rows:
        if _counted(r):
            cats[r["category"]][1] += 1
            cats[r["category"]][0] += bool(r["resolved"])
        if r["resolved"] and r["audit"]:
            s.flagged.append((r.get("run_id", r["task_id"]), list(r["audit"])))
    s.by_category = {c: (v[0], v[1]) for c, v in sorted(cats.items())}
    s.flagged_resolved = len(s.flagged)

    s.cost_per_run_mean = statistics.fmean(r["cost_usd"] for r in rows)
    s.duration_median_s = statistics.median(r["duration_s"] for r in rows)
    s.crashed = sum(bool(r["crashed"]) for r in rows)
    s.infra_failures = sum(
        r["failure_kind"] == "infra" and not r["crashed"] for r in rows
    )
    s.agent_failures = sum(
        r["failure_kind"] == "agent" and not r["crashed"] for r in rows
    )
    s.budget_failures = sum(
        r["failure_kind"] == "budget" and not r["crashed"] for r in rows
    )
    return s


def summarize(rows: list[dict]) -> list[Summary]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["system"], r["preset"])].append(r)
    return [_summarize_group(sy, pr, groups[(sy, pr)]) for sy, pr in sorted(groups)]


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _models(system: str, preset: str) -> str:
    planner, coder, reviewer = _PRESET_MODELS.get(preset, (preset,) * 3)
    if system == "single":
        return coder
    if planner == coder == reviewer:
        return planner
    return f"planner/reviewer {planner}, coder {coder}"


def render_markdown(summaries: list[Summary], *, title: str, sources: list[str]) -> str:
    lines = [f"# {title}", ""]
    if not summaries:
        lines += ["There are no rows in the input, so there is nothing to report.", ""]
    else:
        tasks = max(s.tasks for s in summaries)
        repeats = max(s.repeats for s in summaries)
        lines += [
            f"Caveat: {tasks} tasks and {repeats} repeat{'' if repeats == 1 else 's'} per configuration. "
            "Small samples; differences of a few points are noise.",
            "",
            "The resolve rate's denominator holds only runs that are neither infra "
            "failures nor crashed; those runs are counted in their own columns. "
            "Crashed runs are not also counted under infra.",
            "",
            "## Results",
            "",
            "| system | models | resolved (mean, min-max) | audit-clean resolved "
            "| $/run | median time | agent | budget | infra | crashed |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for s in summaries:
            lines.append(
                f"| {s.system} ({s.preset}) | {_models(s.system, s.preset)} "
                f"| {_pct(s.resolve_rate_mean)} "
                f"({_pct(s.resolve_rate_min)}-{_pct(s.resolve_rate_max)}) "
                f"| {_pct(s.clean_resolve_rate_mean)} "
                f"| {s.cost_per_run_mean:.3f} | {s.duration_median_s:.0f}s "
                f"| {s.agent_failures} | {s.budget_failures} "
                f"| {s.infra_failures} | {s.crashed} |"
            )
        lines += ["", "## By category", ""]
        cats = sorted({c for s in summaries for c in s.by_category})
        lines += [
            "| category | "
            + " | ".join(f"{s.system} ({s.preset})" for s in summaries)
            + " |",
            "|---|" + "---|" * len(summaries),
        ]
        for c in cats:
            cells = []
            for s in summaries:
                got, total = s.by_category.get(c, (0, 0))
                cells.append(f"{got}/{total}" if total else "-")
            lines.append(f"| {c} | " + " | ".join(cells) + " |")
        lines += ["", "## Flagged patches", ""]
        flagged = [(s, rid, fl) for s in summaries for rid, fl in s.flagged]
        if flagged:
            lines.append(
                "Resolved runs whose audit raised flags; they count as resolved "
                "but not as audit-clean."
            )
            lines.append("")
            for s, rid, fl in flagged:
                lines.append(f"- {rid} ({s.system}, {s.preset}): {'; '.join(fl)}")
        else:
            lines.append("None.")
    lines += ["", "## Sources", ""]
    lines += [f"- {name}" for name in sources] or ["- (none)"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench.report")
    parser.add_argument("results", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--title", default="Benchmark results")
    args = parser.parse_args(argv)

    missing = [p for p in args.results if not p.is_file()]
    if missing:
        print(f"error: no such file: {', '.join(map(str, missing))}", file=sys.stderr)
        return 2
    rows = load_rows(args.results)
    md = render_markdown(
        summarize(rows), title=args.title, sources=[p.name for p in args.results]
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(md)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
