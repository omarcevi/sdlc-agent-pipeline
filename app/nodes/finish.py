"""Terminal nodes. Bench mode writes a patch file; live mode arrives in week 2."""

import os
from pathlib import Path
from typing import Any

from google.adk.events.event import Event


def runs_dir() -> Path:
    return Path(os.environ.get("RUNS_DIR", "runs"))


def deliver_patch(node_input: Any, issue: dict, diff: dict):
    run_dir = runs_dir() / issue["run_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "patch.diff"
    path.write_text(diff["unified_diff"])
    outcome = {
        "outcome": "patch_written",
        "failure_kind": "none",
        "reason": "",
        "patch_path": str(path),
    }
    yield Event(message=f"patch written to {path}")
    yield Event(output=outcome, state={"outcome": outcome})


def report_failure(node_input: Any, failure: dict):
    declined = failure["kind"] == "declined"
    outcome = {
        "outcome": "declined" if declined else "failed",
        "failure_kind": "none" if declined else "agent",
        "reason": failure["reason"],
        "patch_path": None,
    }
    yield Event(message=f"{outcome['outcome']}: {failure['reason']}")
    yield Event(output=outcome, state={"outcome": outcome})
