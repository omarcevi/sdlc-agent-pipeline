"""Pydantic models passed between workflow nodes and stored in session state."""

from typing import Literal

from pydantic import BaseModel, Field

Outcome = Literal["patch_written", "declined", "failed"]
FailureKind = Literal["agent", "budget", "infra", "none"]


class RunRequest(BaseModel):
    """Workflow input. Week 1 supports bench mode only."""

    task_id: str
    run_id: str


class IssueTask(BaseModel):
    task_id: str
    run_id: str
    repo: str
    title: str
    body: str
    mode: Literal["bench"] = "bench"


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
    declined: bool = Field(
        default=False, description="True if the issue is not actionable."
    )
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
