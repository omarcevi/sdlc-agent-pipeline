"""Show the reviewer prepared patches and measure what it does with them.

uv run python -m bench.review_probe [--probes rp-01,...] [--repeats 3] [--preset flash]
    [--concurrency 1] [--out results] [--report PATH] [--quiet] [--skip-validate]

Catch rate: the share of bad probes (they pass the visible tests and fail the hidden
ones) on which the reviewer requested changes. False-alarm rate: the same share on good
probes (they pass both). Every probe is validated before any run. Each run spends model
credits (planner and reviewer only).
"""

import argparse
import asyncio
import os
import sys
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from google.adk.events import Event

from app.driver import RunCrashed, run_pipeline
from app.review_probe import build_review_probe_workflow
from app.schemas import ProbeRequest
from app.tracing import enable_cloud_trace, flush_traces, trace_explorer_url
from bench.matrix import RunSpec, _complete, _crash_row, run_matrix
from bench.presets import PRESETS, role_models
from bench.probes import ProbeSpec, dev_task, list_probes, load_probe, validate_probe

SYSTEM = "review"


def plan_probe_runs(
    probes: list[ProbeSpec], preset: str, repeats: int, stamp: str
) -> list[RunSpec]:
    specs = []
    for repeat in range(1, repeats + 1):
        for probe in probes:
            task = dev_task(probe.task_id)
            if task is None:
                raise ValueError("task is not in the dev split")
            specs.append(
                RunSpec(task, SYSTEM, preset, repeat, stamp, variant=probe.probe_id)
            )
    return specs


def _probe_fields(probe: ProbeSpec, verdict: dict | None) -> dict:
    return {
        "probe_id": probe.probe_id,
        "kind": probe.kind,
        "verdict": verdict["verdict"] if verdict else None,
        "must_fix": list(verdict["must_fix"]) if verdict else [],
    }


async def run_probe_spec(
    spec: RunSpec,
    *,
    on_event: Callable[[Event], None] | None = None,
    workflow_factory: Callable = lambda preset: build_review_probe_workflow(
        role_models(preset)
    ),
) -> dict:
    """Run one probe (own workflow, budget, run dir, sandbox). No scoring: the row's
    verdict is the reviewer's, taken from the outcome the graph recorded."""
    probe = load_probe(spec.variant)
    verdict: dict | None = None

    def watch(event: Event) -> None:
        nonlocal verdict
        outcome = (
            (event.actions.state_delta or {}).get("outcome") if event.actions else None
        )
        if outcome and "verdict" in outcome:
            verdict = outcome
        if on_event is not None:
            on_event(event)

    try:
        record = await run_pipeline(
            ProbeRequest(
                task_id=spec.task.task_id, run_id=spec.run_id, probe_id=probe.probe_id
            ),
            workflow=workflow_factory(spec.preset),
            on_event=watch,
        )
    except RunCrashed as crashed:
        row = _crash_row(spec, crashed.__cause__ or crashed, crashed.record)
        return {**row, **_probe_fields(probe, None)}
    except Exception as exc:
        return {**_crash_row(spec, exc), **_probe_fields(probe, None)}
    row = {**record.model_dump(), **_probe_fields(probe, verdict)}
    return _complete(row, spec)


@dataclass
class ProbeSummary:
    bad_counted: int = 0
    bad_caught: int = 0
    good_counted: int = 0
    good_alarms: int = 0
    probes: dict[str, str] = field(default_factory=dict)  # probe id -> kind
    repeats: int = 0
    # (run id, why) for every run left out of both rates
    excluded: list[tuple[str, str]] = field(default_factory=list)

    @property
    def catch_rate(self) -> float | None:
        return self.bad_caught / self.bad_counted if self.bad_counted else None

    @property
    def false_alarm_rate(self) -> float | None:
        return self.good_alarms / self.good_counted if self.good_counted else None


def _exclusion(row: dict) -> str | None:
    """Why a run is left out of both rates, or None when it counts."""
    if row.get("crashed"):
        return "crashed"
    if row.get("failure_kind") == "infra":
        return "infra failure"
    if row.get("outcome") == "declined":
        return "planner declined, no verdict"
    if row.get("verdict") not in ("approve", "request_changes"):
        return f"{row.get('failure_kind', 'none')} failure, no verdict"
    return None


def _probe_id(row: dict) -> str:
    return row.get("probe_id") or row.get("variant") or "unknown"


def summarize(rows: list[dict]) -> ProbeSummary:
    summary = ProbeSummary()
    summary.repeats = len({r["repeat"] for r in rows})
    for row in sorted(rows, key=lambda r: r["run_id"]):
        summary.probes[_probe_id(row)] = row.get("kind", "unknown")
        why = _exclusion(row)
        if why:
            summary.excluded.append((row["run_id"], why))
            continue
        sent_back = row["verdict"] == "request_changes"
        if row["kind"] == "bad":
            summary.bad_counted += 1
            summary.bad_caught += sent_back
        else:
            summary.good_counted += 1
            summary.good_alarms += sent_back
    return summary


def _rate(part: int, whole: int) -> str:
    return (
        f"{part}/{whole} ({100 * part / whole:.0f}%)"
        if whole
        else "n/a (no counted runs)"
    )


def render_report(rows: list[dict], *, title: str, sources: list[str]) -> str:
    if not rows:
        return f"# {title}\n\nThere are no rows in the input, so there is nothing to report.\n"
    s = summarize(rows)
    bad = sum(k == "bad" for k in s.probes.values())
    good = sum(k == "good" for k in s.probes.values())
    lines = [
        f"# {title}",
        "",
        f"Caveat: {len(s.probes)} distinct probes ({bad} bad, {good} good), "
        f"{s.repeats} {'repeat' if s.repeats == 1 else 'repeats'} each. Repeats at "
        "temperature 0 show run-to-run variation; they do not make the sample of "
        "probes bigger.",
        "",
        "Catch rate is the share of counted bad-probe runs where the reviewer "
        "requested changes; false-alarm rate is the same share on good probes. A "
        "caught probe counts the verdict, not whether the reasons were right. Infra "
        "failures, crashed runs and runs with no verdict (the planner declined, or "
        "an agent failure) are listed below and left out of both rates.",
        "",
        "## Rates",
        "",
        "| measure | runs | rate |",
        "|---|---|---|",
        f"| catch rate (bad probes sent back) | {s.bad_counted} | "
        f"{_rate(s.bad_caught, s.bad_counted)} |",
        f"| false-alarm rate (good probes sent back) | {s.good_counted} | "
        f"{_rate(s.good_alarms, s.good_counted)} |",
        "",
        "## Excluded runs",
        "",
    ]
    if s.excluded:
        lines += [f"- {run_id}: {why}" for run_id, why in s.excluded]
    else:
        lines.append("None.")
    by_probe: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_probe[_probe_id(row)].append(row)
    lines += [
        "",
        "## Per probe",
        "",
        "| probe | task | kind | verdicts per repeat |",
        "|---|---|---|---|",
    ]
    for probe_id in sorted(by_probe):
        runs = sorted(by_probe[probe_id], key=lambda r: r["repeat"])
        verdicts = ", ".join(
            r["verdict"] if r.get("verdict") else f"({_exclusion(r) or 'none'})"
            for r in runs
        )
        lines.append(
            f"| {probe_id} | {runs[0]['task_id']} | {runs[0].get('kind', 'unknown')} "
            f"| {verdicts} |"
        )
    cost = sum(r.get("cost_usd", 0.0) for r in rows)
    lines += ["", f"Cost: ${cost:.2f} over {len(rows)} runs.", ""]
    lines.append("Sources: " + ", ".join(f"`{p}`" for p in sources))
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bench.review_probe")
    parser.add_argument("--probes", help="comma-separated probe ids (default: all)")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--preset", choices=sorted(PRESETS), default="flash")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--out", default="results")
    parser.add_argument("--report", help="also write the report to this path")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="do not validate the probes before running",
    )
    return parser


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    load_dotenv()
    if args.repeats < 1 or args.concurrency < 1:
        return _fail("--repeats and --concurrency must be at least 1")
    try:
        selected = (
            [load_probe(i.strip()) for i in args.probes.split(",") if i.strip()]
            if args.probes
            else list_probes()
        )
    except (OSError, ValueError) as exc:
        return _fail(f"cannot load probes ({type(exc).__name__})")
    if not selected:
        return _fail("no probes selected")
    if not args.skip_validate:
        problems = []
        for probe in selected:
            try:
                found = validate_probe(probe)
            except Exception as exc:  # a message could quote task or patch text
                found = [f"error ({type(exc).__name__})"]
            problems += [f"{probe.probe_id}: {problem}" for problem in found]
        if problems:
            return _fail("invalid probes:\n  " + "\n  ".join(problems))

    tracing_on = enable_cloud_trace()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results_path = out / f"{stamp}-review-probe-{args.preset}.json"
    specs = plan_probe_runs(selected, args.preset, args.repeats, stamp)
    rows = asyncio.run(
        run_matrix(
            specs,
            results_path,
            concurrency=args.concurrency,
            run_one=run_probe_spec,
            progress=not args.quiet,
        )
    )
    report = render_report(
        rows, title=f"Reviewer probes ({args.preset})", sources=[str(results_path)]
    )
    print("\n" + report)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(report)
    cost = sum(r["cost_usd"] for r in rows)
    print(f"total ${cost:.2f}  → {results_path}")
    if tracing_on:
        flush_traces()
        print(f"traces: {trace_explorer_url(os.environ['GOOGLE_CLOUD_PROJECT'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
