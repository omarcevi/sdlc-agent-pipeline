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
    "repo": "unknown",
    "difficulty": "unknown",
    "split": "unknown",
}
_DIFFICULTY_ORDER = ("easy", "medium", "hard", "unknown")
_NUMBERS = (
    "cost_usd",
    "duration_s",
    "tool_calls",
    "tokens_in",
    "tokens_out",
    "test_attempts",
    "review_rounds",
)
_REASON_CHARS = 160


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
    counted: int = 0  # rows in the resolve-rate denominator
    tasks: int = 0
    task_ids: frozenset[str] = frozenset()
    repeats: int = 0
    rate_repeats: int = 0  # repeats that had at least one counted run
    # Rates are None when nothing was counted: there is no rate to report.
    resolve_rate_mean: float | None = None
    resolve_rate_min: float | None = None
    resolve_rate_max: float | None = None
    clean_resolve_rate_mean: float | None = None
    flagged_resolved: int = 0
    by_category: dict[str, tuple[int, int]] = field(default_factory=dict)
    by_repo: dict[str, tuple[int, int]] = field(default_factory=dict)
    by_difficulty: dict[str, tuple[int, int]] = field(default_factory=dict)
    # The six buckets below sum to `runs`.
    resolved: int = 0
    unresolved: int = 0  # counted, not resolved, failure_kind "none"
    agent_failures: int = 0
    budget_failures: int = 0
    infra_failures: int = 0  # infra failures that did not crash the runner
    crashed: int = 0
    cost_per_run_mean: float = 0.0
    cost_total: float = 0.0
    cost_per_resolved: float | None = None
    duration_median_s: float = 0.0
    flagged: list[tuple[str, list[str]]] = field(default_factory=list)
    # (run id, bucket, one-line reason) for every run that is not resolved
    not_resolved: list[tuple[str, str, str]] = field(default_factory=list)
    budget_reasons: dict[str, int] = field(default_factory=dict)


def _counted(r: dict) -> bool:
    """A run counts toward the resolve rate unless it was infra or crashed."""
    return not r["crashed"] and r["failure_kind"] != "infra"


def _bucket(r: dict) -> str:
    """The one bucket a run lands in."""
    if r["crashed"]:
        return "crashed"
    if r["failure_kind"] == "infra":
        return "infra"
    if r["resolved"]:
        return "resolved"
    if r["failure_kind"] in ("agent", "budget"):
        return r["failure_kind"]
    return "unresolved"


def _one_line(text: str | None, limit: int) -> str:
    """Collapse whitespace to one line, cut to `limit` characters; `-` if empty."""
    flat = " ".join((text or "").split())
    return flat[:limit] if flat else "-"


def _summarize_group(system: str, preset: str, rows: list[dict]) -> Summary:
    s = Summary(system=system, preset=preset, runs=len(rows))
    s.task_ids = frozenset(r["task_id"] for r in rows)
    s.tasks = len(s.task_ids)
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
    s.rate_repeats = len(rates)
    if rates:
        s.resolve_rate_mean = statistics.fmean(rates)
        s.resolve_rate_min, s.resolve_rate_max = min(rates), max(rates)
        s.clean_resolve_rate_mean = statistics.fmean(clean_rates)

    cats: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    repos: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    diffs: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    reasons: dict[str, int] = defaultdict(int)
    for r in rows:
        bucket = _bucket(r)
        run_id = r.get("run_id", r["task_id"])
        if bucket == "resolved":
            s.resolved += 1
        elif bucket == "unresolved":
            s.unresolved += 1
        elif bucket == "agent":
            s.agent_failures += 1
        elif bucket == "budget":
            s.budget_failures += 1
            reasons[_one_line(r.get("reason"), 400)] += 1
        elif bucket == "infra":
            s.infra_failures += 1
        else:
            s.crashed += 1
        if _counted(r):
            s.counted += 1
            for table, key in (
                (cats, r["category"]),
                (repos, r.get("repo", "unknown")),
                (diffs, r.get("difficulty", "unknown")),
            ):
                table[key][1] += 1
                table[key][0] += bool(r["resolved"])
        if not r["resolved"]:
            s.not_resolved.append(
                (run_id, bucket, _one_line(r.get("reason"), _REASON_CHARS))
            )
        if r["resolved"] and r["audit"]:
            s.flagged.append((run_id, list(r["audit"])))
    s.by_category = {c: (v[0], v[1]) for c, v in sorted(cats.items())}
    s.by_repo = {c: (v[0], v[1]) for c, v in sorted(repos.items())}
    s.by_difficulty = {
        d: (diffs[d][0], diffs[d][1])
        for d in sorted(
            diffs,
            key=lambda d: (
                _DIFFICULTY_ORDER.index(d)
                if d in _DIFFICULTY_ORDER
                else len(_DIFFICULTY_ORDER),
                d,
            ),
        )
    }
    s.flagged_resolved = len(s.flagged)
    s.budget_reasons = dict(sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0])))

    s.cost_total = sum(r["cost_usd"] for r in rows)
    s.cost_per_run_mean = s.cost_total / len(rows)
    s.cost_per_resolved = s.cost_total / s.resolved if s.resolved else None
    s.duration_median_s = statistics.median(r["duration_s"] for r in rows)
    return s


def summarize(rows: list[dict]) -> list[Summary]:
    seen: dict[tuple, int] = defaultdict(int)
    for r in rows:
        seen[(r["system"], r["preset"], r["repeat"], r["task_id"])] += 1
    dupes = sorted((k for k, n in seen.items() if n > 1), key=str)
    if dupes:
        listed = "; ".join(
            f"system={sy} preset={pr} repeat={rp} task={t} ({seen[(sy, pr, rp, t)]} rows)"
            for sy, pr, rp, t in dupes
        )
        raise ValueError(f"duplicate rows for: {listed}")
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["system"], r["preset"])].append(r)
    return [_summarize_group(sy, pr, groups[(sy, pr)]) for sy, pr in sorted(groups)]


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def _rate(s: Summary) -> str:
    """The rate alone unless two or more repeats contributed to it."""
    if s.resolve_rate_mean is None:
        return "n/a"
    if s.rate_repeats < 2:
        return _pct(s.resolve_rate_mean)
    return (
        f"{_pct(s.resolve_rate_mean)} "
        f"({_pct(s.resolve_rate_min)}-{_pct(s.resolve_rate_max)})"
    )


def _money(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.3f}"


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _models(system: str, preset: str) -> str:
    planner, coder, reviewer = _PRESET_MODELS.get(preset, (preset,) * 3)
    if system == "single":
        return coder
    if planner == coder == reviewer:
        return planner
    return f"planner/reviewer {planner}, coder {coder}"


def _caveat(summaries: list[Summary]) -> str:
    same = len({(s.task_ids, s.repeats) for s in summaries}) == 1
    if same:
        tasks, repeats = summaries[0].tasks, summaries[0].repeats
        one = f"{100 / tasks:.1f}".removesuffix(".0")
        head = (
            f"Caveat: every configuration ran the same {_plural(tasks, 'task')} "
            f"with {_plural(repeats, 'repeat')} each. Within a repeat, one task is "
            f"{one} points of resolve rate (100 / {tasks}). "
        )
    elif len({s.task_ids for s in summaries}) == 1:
        tasks = summaries[0].tasks
        one = f"{100 / tasks:.1f}".removesuffix(".0")
        head = (
            f"Caveat: every configuration ran the same {_plural(tasks, 'task')}, "
            "but the configurations differ in repeats; see the repeats, runs and "
            "counted columns for each row's own sample. Within a repeat, one task "
            f"is {one} points of resolve rate (100 / {tasks}). "
        )
    else:
        head = (
            "Caveat: the configurations differ in tasks or repeats; see the "
            "tasks, repeats, runs and counted columns for each row's own sample. "
            "Within a repeat, one task is 100 / tasks points of resolve rate, "
            "where tasks is that row's own count. "
        )
    return head + (
        "Repeats rerun the same tasks, so they show run-to-run variation and "
        "do not make the sample of tasks bigger."
    )


def render_markdown(summaries: list[Summary], *, title: str, sources: list[str]) -> str:
    lines = [f"# {title}", ""]
    if not summaries:
        lines += ["There are no rows in the input, so there is nothing to report.", ""]
    else:
        configs = [f"{s.system} ({s.preset})" for s in summaries]
        lines += [
            _caveat(summaries),
            "",
            "The resolve rate's denominator (the counted column) holds only runs "
            "that are neither infra failures nor crashed; those runs are shown in "
            "their own columns. Crashed runs are not also counted under infra. "
            "Every run is in exactly one of resolved, unresolved, agent, budget, "
            "infra or crashed, and those six columns sum to runs. Unresolved is a "
            "counted run that was not resolved and has no failure kind: a wrong "
            "patch, a wrong decline, or a trap that was patched.",
            "",
            "## Results",
            "",
            "| system | models | tasks | repeats | runs | counted | resolved "
            "| resolve rate (mean, min-max) | audit-clean resolved | unresolved "
            "| agent | budget | infra | crashed | $/run | total $ | $/resolved "
            "| median time |",
            "|" + "---|" * 18,
        ]
        for s, cfg in zip(summaries, configs, strict=True):
            lines.append(
                f"| {cfg} | {_models(s.system, s.preset)} "
                f"| {s.tasks} | {s.repeats} | {s.runs} | {s.counted} | {s.resolved} "
                f"| {_rate(s)} | {_pct(s.clean_resolve_rate_mean)} | {s.unresolved} "
                f"| {s.agent_failures} | {s.budget_failures} "
                f"| {s.infra_failures} | {s.crashed} "
                f"| {_money(s.cost_per_run_mean)} | {_money(s.cost_total)} "
                f"| {_money(s.cost_per_resolved)} | {s.duration_median_s:.0f}s |"
            )
        lines += [
            "",
            "$/run is the mean cost over all runs of the configuration, total $ "
            "the sum, and $/resolved the total divided by resolved runs. Cost "
            "includes reruns after infra failures; crashed runs may record less "
            "than they spent.",
        ]
        for title, noun, attr, order in (
            ("By category", "category", "by_category", None),
            ("By repo", "repo", "by_repo", None),
            ("By difficulty", "difficulty", "by_difficulty", _DIFFICULTY_ORDER),
        ):
            keys = {k for s in summaries for k in getattr(s, attr)}
            keys = (
                sorted(
                    keys,
                    key=lambda k: (order.index(k) if k in order else len(order), k),
                )
                if order
                else sorted(keys)
            )
            lines += [
                "",
                f"## {title}",
                "",
                "Cells are resolved / counted, all repeats pooled; infra and crashed "
                "runs excluded.",
                "",
                f"| {noun} | " + " | ".join(configs) + " |",
                "|---|" + "---|" * len(summaries),
            ]
            for k in keys:
                cells = []
                for s in summaries:
                    got, total = getattr(s, attr).get(k, (0, 0))
                    cells.append(f"{got} / {total}" if total else "-")
                lines.append(f"| {k} | " + " | ".join(cells) + " |")

        lines += ["", "## Runs that did not resolve", ""]
        if any(s.not_resolved for s in summaries):
            groups = []
            for s, cfg in zip(summaries, configs, strict=True):
                if s.not_resolved:
                    groups.append(
                        [f"### {cfg}", ""]
                        + [f"- {rid} [{b}] {why}" for rid, b, why in s.not_resolved]
                    )
            lines += [
                line for i, g in enumerate(groups) for line in ([""] if i else []) + g
            ]
        else:
            lines.append("None.")

        lines += ["", "## Budget failures by cap", ""]
        if any(s.budget_reasons for s in summaries):
            lines += ["| configuration | reason | count |", "|---|---|---|"]
            for s, cfg in zip(summaries, configs, strict=True):
                for reason, n in s.budget_reasons.items():
                    lines.append(f"| {cfg} | {_cell(reason)} | {n} |")
        else:
            lines.append("None.")

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
    try:
        summaries = summarize(rows)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    md = render_markdown(
        summaries, title=args.title, sources=[p.name for p in args.results]
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(md)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
