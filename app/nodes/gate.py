"""The live graph's human approval step: `human_gate` pauses the run with what is
about to be published, and `route_approval` checks the answer.

The gate is a FunctionNode with `rerun_on_resume=False`: when the driver resumes the
run with the approver's decision, ADK marks the gate completed and passes the
decision on as its output, so no node before the gate runs again.
"""

from typing import Any

from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput

from app import pr_text
from app.approval import ApprovalDecision, ApprovalRequest
from app.nodes.finish import _patch_hash, runs_dir


def human_gate(
    node_input: Any,
    issue: dict,
    diff: dict,
    patch_sha256: str,
    plan: dict | None = None,
    patch: dict | None = None,
    test_report: dict | None = None,
    review: dict | None = None,
    budget: dict | None = None,
):
    """Ask for approval of the pull request exactly as `open_pr` would publish it:
    same branch, title and body (the published footer adds the approver)."""
    digest = _patch_hash(diff)
    if digest != patch_sha256:
        raise RuntimeError("the patch hash in state is not the hash of the diff")
    run_id = issue["run_id"]
    number = int(issue["issue_number"])
    report = test_report or {}
    verdict = review or {}
    spend = budget or {}
    request = ApprovalRequest(
        run_id=run_id,
        repo=issue["repo"],
        issue_number=number,
        issue_url=issue["html_url"],
        base_ref=issue["base_ref"],
        branch=pr_text.branch_name(number, run_id),
        pr_title=pr_text.pr_title(issue["title"]),
        pr_body=pr_text.pr_body(
            run_id=run_id,
            issue_number=number,
            diff=diff,
            plan=plan,
            patch=patch,
            test_report=test_report,
            review=review,
            budget=budget,
            patch_sha256=digest,
        ),
        files=list(diff.get("files") or []),
        insertions=int(diff.get("insertions") or 0),
        deletions=int(diff.get("deletions") or 0),
        patch_path=str(runs_dir() / run_id / "patch.diff"),
        patch_sha256=digest,
        tests_passed=bool(report.get("passed")),
        test_exit_code=int(report.get("exit_code", -1)),
        review_verdict=str(verdict.get("verdict", "")),
        review_must_fix=[str(item) for item in verdict.get("must_fix") or []],
        cost_usd=float(spend.get("cost_usd") or 0.0),
        tool_calls=int(spend.get("tool_calls") or 0),
    )
    yield Event(message="waiting for human approval")
    yield RequestInput(
        interrupt_id=f"approve-{run_id}",
        message=(
            f"Approve the pull request for {request.repo}#{number}: "
            f"{len(request.files)} files, +{request.insertions} "
            f"-{request.deletions}, patch SHA-256 {digest}"
        ),
        payload=request.model_dump(),
        response_schema=ApprovalDecision,
    )


def route_approval(node_input: ApprovalDecision, patch_sha256: str) -> Event:
    """`approved` only for an approval of the patch in state; the decision is passed
    on as the output, which `open_pr` reads. Anything else is `rejected`."""
    if node_input.approved and node_input.patch_sha256 == patch_sha256:
        return Event(output=node_input, route="approved")
    approver = node_input.approver or None
    if node_input.approved:
        reason = "approval was for a different patch"
    elif approver is None:
        # No one decided: the approval timed out. No login to name.
        reason = node_input.note or "not approved"
    else:
        reason = f"not approved by {approver}"
        if node_input.note:
            reason += f": {node_input.note}"
    return Event(
        output=node_input,
        route="rejected",
        state={
            "failure": {"kind": "rejected", "reason": reason},
            "approver": approver,
        },
    )
