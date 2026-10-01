"""Pydantic models passed between workflow nodes and stored in session state."""

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Outcome = Literal[
    "patch_written", "declined", "failed", "pr_opened", "rejected", "refused"
]
FailureKind = Literal["agent", "budget", "infra", "none"]
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class RunRequest(BaseModel):
    """Workflow input: a bench task, or (live mode) a GitHub issue."""

    run_id: str
    mode: Literal["bench", "live"] = "bench"
    task_id: str | None = None
    repo: str | None = None
    issue_number: int | None = None
    base_ref: str | None = None

    @model_validator(mode="after")
    def _check_mode_fields(self) -> "RunRequest":
        if self.mode == "bench":
            if not self.task_id:
                raise ValueError("a bench request needs a task_id")
            if self.repo is not None or self.issue_number is not None:
                raise ValueError("a bench request takes no repo or issue_number")
        else:
            if not self.repo or not _REPO.fullmatch(self.repo):
                raise ValueError("a live request needs repo as 'owner/name'")
            if self.issue_number is None or self.issue_number < 1:
                raise ValueError("a live request needs a positive issue_number")
        return self

    @property
    def subject_id(self) -> str:
        """The task id (bench) or '<owner>/<name>#<number>' (live)."""
        if self.mode == "live":
            return f"{self.repo}#{self.issue_number}"
        return self.task_id or ""


class ProbeRequest(RunRequest):
    """Input of the reviewer-probe graph: a run of one prepared patch (bench/probes.py)."""

    probe_id: str


class IssueTask(BaseModel):
    task_id: str
    run_id: str
    repo: str
    title: str
    body: str
    mode: Literal["bench", "live"] = "bench"
    issue_number: int | None = None
    base_ref: str | None = None
    base_sha: str | None = None
    base_tree_sha: str | None = None
    html_url: str | None = None


class Plan(BaseModel):
    actionable: bool = Field(
        description="False if the issue is ambiguous, impossible, or needs network, "
        "credentials or a product decision."
    )
    decline_reason: str | None = Field(
        default=None, description="Why the issue is not actionable."
    )
    summary: str = Field(description="One paragraph describing the intended change.")
    files_to_inspect: list[str] = Field(
        default_factory=list,
        description="Repo-relative paths most relevant to the change.",
    )
    steps: list[str] = Field(
        default_factory=list, description="Ordered implementation steps."
    )
    test_strategy: str = Field(
        default="", description="Tests that will prove the change."
    )


class PatchResult(BaseModel):
    summary: str
    files_changed: list[str] = Field(default_factory=list)
    tests_added: list[str] = Field(default_factory=list)
    notes: str = ""


class SoloResult(BaseModel):
    declined: bool = Field(description="True if the issue is not actionable.")
    decline_reason: str | None = None
    summary: str = Field(description="What was changed, or why nothing was.")
    files_changed: list[str] = Field(default_factory=list)


class Diff(BaseModel):
    unified_diff: str
    files: list[str]
    insertions: int
    deletions: int

    @property
    def is_empty(self) -> bool:
        return not self.files


class TestReport(BaseModel):
    __test__ = False  # stop pytest from collecting this class

    passed: bool
    exit_code: int
    failed_tests: list[str] = Field(default_factory=list)
    output_tail: str = ""
    duration_s: float = 0.0


class ReviewComment(BaseModel):
    file: str
    line: int | None = None
    severity: Literal["blocker", "major", "minor", "nit"]
    issue: str


class Review(BaseModel):
    verdict: Literal["approve", "request_changes"]
    comments: list[ReviewComment] = Field(default_factory=list)
    must_fix: list[str] = Field(default_factory=list)


class RunRecord(BaseModel):
    task_id: str
    run_id: str
    outcome: Outcome
    failure_kind: FailureKind
    reason: str = ""
    patch_path: str | None = None
    test_attempts: int = 0
    review_rounds: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    tool_calls: int = 0
    duration_s: float = 0.0
    mode: str = "bench"
    base_ref: str | None = None
    base_sha: str | None = None
    base_tree_sha: str | None = None
    pr_url: str | None = None
    # Time spent waiting for the approver of a live run; duration_s excludes it.
    approval_wait_s: float = 0.0
    comment_posted: bool = False
    # Model call timeouts (MODEL_CALL_TIMEOUT_S, app/models.py): each counts, so a
    # call that stalled twice counts 2. Older records load as 0.
    model_stalls: int = 0
