"""Dry-run wiring check for the budget guard (spec 5.4). Operator tooling.

    uv run python scripts/budget_guard_check.py [--timeout-s 180]

Publishes ONE test message to the budget topic and waits for the function's log line
with that message id. The message always carries `itp_dry_run=1` and never a
`billingAccountId`, so the function cannot disable billing: it only reads the billing
state and tests its own permissions. Exits 0 only when the logged decision is `dry_run`
and both permissions are granted. The project comes from GOOGLE_CLOUD_PROJECT; the topic,
the budget id and the amount come from the budget root's Terraform outputs
(`budget_topic`, `budget_id`, `budget_amount_try`). Standard library only.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

DRY_RUN_ATTRIBUTE = "itp_dry_run"
PERMISSIONS = (
    "resourcemanager.projects.deleteBillingAssignment",
    "resourcemanager.projects.get",
)
SERVICE = "budget-guard"
POLL_INTERVAL_S = 10
ROOT = Path(__file__).resolve().parents[1]
BUDGET_ROOT = ROOT / "deployment" / "terraform" / "budget"

Runner = Callable[[list[str]], subprocess.CompletedProcess]


def build_test_message(
    budget_id: str, amount: float, currency: str = "TRY"
) -> tuple[str, dict[str, str]]:
    """A payload with cost == budget, and attributes that make it a dry run."""
    if not re.fullmatch(r"[A-Za-z0-9-]+", budget_id):
        raise ValueError("budget id must match [A-Za-z0-9-]+")
    payload = {
        "budgetDisplayName": "issue-to-pr guard check",
        "costAmount": amount,
        "budgetAmount": amount,
        "currencyCode": currency,
        "alertThresholdExceeded": 1.0,
    }
    return json.dumps(payload), {"budgetId": budget_id, DRY_RUN_ATTRIBUTE: "1"}


def publish_args(
    topic: str, message: str, attributes: dict[str, str], project: str
) -> list[str]:
    pairs = ",".join(f"{key}={value}" for key, value in attributes.items())
    return [
        "gcloud",
        "pubsub",
        "topics",
        "publish",
        topic,
        "--project",
        project,
        "--message",
        message,
        "--attribute",
        pairs,
        "--format",
        "json",
    ]


def passed(log_line: dict) -> bool:
    permissions = log_line.get("permissions")
    if log_line.get("decision") != "dry_run" or not isinstance(permissions, dict):
        return False
    return all(permissions.get(name) is True for name in PERMISSIONS)


def _message_id(output: str) -> str | None:
    try:
        ids = json.loads(output).get("messageIds")
    except (ValueError, AttributeError):
        return None
    if isinstance(ids, list) and ids and re.fullmatch(r"\d+", str(ids[0])):
        return str(ids[0])
    return None


def _read_args(message_id: str, project: str) -> list[str]:
    log_filter = (
        'resource.type="cloud_run_revision" '
        f'AND resource.labels.service_name="{SERVICE}" '
        f'AND jsonPayload.message_id="{message_id}"'
    )
    return [
        "gcloud",
        "logging",
        "read",
        log_filter,
        "--project",
        project,
        "--freshness",
        "30m",
        "--limit",
        "5",
        "--format",
        "json",
    ]


def run_check(
    runner: Runner,
    topic: str,
    budget_id: str,
    amount: float,
    project: str,
    timeout_s: float,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[int, dict | None]:
    message, attributes = build_test_message(budget_id, amount)
    published = runner(publish_args(topic, message, attributes, project))
    if published.returncode != 0:
        print(f"error: publish failed: {published.stderr.strip()}", file=sys.stderr)
        return 1, None
    message_id = _message_id(published.stdout)
    if message_id is None:
        print("error: publish returned no message id", file=sys.stderr)
        return 1, None
    deadline = monotonic() + timeout_s
    last_error = ""
    while True:
        read = runner(_read_args(message_id, project))
        if read.returncode != 0:
            last_error = (read.stderr or "").strip()
        else:
            try:
                entries = json.loads(read.stdout or "[]")
            except ValueError:
                entries = []
            for entry in entries:
                payload = entry.get("jsonPayload") if isinstance(entry, dict) else None
                if isinstance(payload, dict):
                    return (0 if passed(payload) else 1), payload
        if monotonic() >= deadline:
            if last_error:
                print(
                    f"error: last gcloud logging read failed: {last_error}",
                    file=sys.stderr,
                )
            return 1, None
        sleep(POLL_INTERVAL_S)


def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _terraform_outputs() -> dict:
    result = _run(["terraform", f"-chdir={BUDGET_ROOT}", "output", "-json"])
    if result.returncode != 0:
        raise SystemExit(f"error: terraform output failed: {result.stderr.strip()}")
    outputs = json.loads(result.stdout)
    try:
        return {
            name: outputs[name]["value"]
            for name in ("budget_topic", "budget_id", "budget_amount_try")
        }
    except KeyError as missing:
        raise SystemExit(f"error: terraform output {missing} is missing") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run wiring check for the budget guard."
    )
    parser.add_argument(
        "--timeout-s", type=float, default=180, help="seconds to wait for the log line"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if not project:
        print("error: GOOGLE_CLOUD_PROJECT is not set", file=sys.stderr)
        return 2
    outputs = _terraform_outputs()
    try:
        build_test_message(str(outputs["budget_id"]), 1.0)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    code, line = run_check(
        _run,
        str(outputs["budget_topic"]),
        str(outputs["budget_id"]),
        float(outputs["budget_amount_try"]),
        project,
        args.timeout_s,
    )
    if line is None:
        print(
            f"FAIL: no budget-guard log line within {args.timeout_s:g} s",
            file=sys.stderr,
        )
    else:
        print(json.dumps(line, indent=2))
        print(
            "PASS" if code == 0 else "FAIL: not a dry run with both permissions granted"
        )
    return code


if __name__ == "__main__":
    sys.exit(main())
