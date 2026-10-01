"""Public text: pull request titles, bodies, commit messages and comments.

Everything here is published on GitHub. Real data (counts, ids, hashes, paths) is
rendered from fields the pipeline computed. Model-written text appears only inside
fenced code blocks whose fence the text cannot close, so GitHub renders no
mentions, issue references, closing keywords, links, images or HTML from it.
The pipeline names itself openly in the footer (design decision 5A).
"""

from __future__ import annotations

import re

AUTHOR_NAME = "issue-to-pr pipeline"
AUTHOR_EMAIL = "issue-to-pr@example.invalid"
FOOTER_TEMPLATE = (
    "Opened by the issue-to-pr pipeline (run `{run_id}`, {label} `{model}`){approval}"
)

BLOCK_LIMIT = 2_000
BODY_LIMIT = 20_000
TITLE_LIMIT = 120
COMMIT_LIMIT = 72
BRANCH_SLUG_LIMIT = 40
CODE_SPAN_LIMIT = 200
MAX_LISTED_FILES = 25
MAX_LISTED_TESTS = 10
MAX_LISTED_MODELS = 5
_BLOCK_LIMITS = (BLOCK_LIMIT, 1_000, 500, 250)

# Bidi overrides and zero-width characters can make a title or a path read as
# something else (Trojan source); they are never published.
_INVISIBLE = re.compile(
    r"[\u00ad\u200b-\u200f\u202a-\u202e\u061c\u2060-\u2064\u2066-\u2069\ufeff\U000e0000-\U000e007f]"
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_FENCE_RUN = re.compile(r"`+|~+")  # a closing fence is one character type
_LONG_RUN = re.compile(r"([`~])\1{15,}")
_BACKTICKS = re.compile(r"`+")
_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_SAFE_ID = re.compile(r"[^A-Za-z0-9_.:/+-]")


def _one_line(text: str) -> str:
    return " ".join(_CONTROL.sub(" ", _INVISIBLE.sub(" ", text)).split())


_ISSUE_URL = re.compile(
    r"https?://(?:www\.)?github\.com/\S*/(?:issues|pull)/\d+\S*", re.IGNORECASE
)


def _defuse(text: str) -> str:
    """One line with no issue reference, mention or issue link, for a title: a
    squash merge makes the pull request title a commit subject, where a closing
    keyword followed by a reference would close that issue."""
    text = _ISSUE_URL.sub("link", _one_line(text))
    text = re.sub(r"\bGH-(?=\d)", "GH ", text, flags=re.IGNORECASE)
    # What GitHub links: "#12" (also "owner/repo#12") and a mention. "C#", an
    # e-mail address or a bare "#" are left alone.
    text = re.sub(r"#(?=\d)", "", text)
    return re.sub(r"(?<!\w)@(?=\w)", "", text)


def _identifier(text: str, limit: int = 80) -> str:
    """A run id or model name reduced to characters that cannot break markdown."""
    return _SAFE_ID.sub("-", str(text))[:limit]


def marker(run_id: str, kind: str) -> str:
    return (
        f"<!-- issue-to-pr run={_identifier(run_id)} kind={_identifier(kind, 20)} -->"
    )


def fence(text: str, *, limit: int = BLOCK_LIMIT) -> str:
    """`text` as a fenced code block that the text itself cannot close.

    The fence is longer than any run of backticks or tilde in the (capped) text.
    Text beyond `limit` characters is dropped with a note inside the block.
    """
    text = _INVISIBLE.sub("", str(text))
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = _LONG_RUN.sub(lambda m: m.group(1) * 16, text)
    if len(text) > limit:
        text = f"{text[:limit]}\n[truncated: {len(text) - limit} more characters]"
    longest = max((len(run) for run in _FENCE_RUN.findall(text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}\n{text.rstrip()}\n{ticks}"


def code_span(text: str) -> str:
    """`text` as one inline code span: one line, capped, backticks safe."""
    text = _one_line(str(text))[:CODE_SPAN_LIMIT] or " "
    runs = {len(run) for run in _BACKTICKS.findall(text)}
    size = next(n for n in range(1, len(text) + 3) if n not in runs)
    ticks = "`" * size
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


def pr_title(issue_title: str) -> str:
    return f"[issue-to-pr] {_defuse(issue_title)}"[:TITLE_LIMIT].rstrip()


def branch_name(issue_number: int, run_id: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", str(run_id).lower()).strip("-")
    slug = slug[:BRANCH_SLUG_LIMIT].strip("-") or "run"
    return f"issue-to-pr/{int(issue_number)}-{slug}"


def commit_message(issue_number: int, issue_title: str) -> str:
    # A reference in the title ("#99", "owner/repo#4", "GH-9") could close another
    # issue when the commit reaches the default branch.
    title = _defuse(issue_title)
    return f"Fix #{int(issue_number)}: {title}"[:COMMIT_LIMIT].rstrip()


def _models_text(budget: dict | None) -> str:
    models = [_identifier(m, 60) for m in (budget or {}).get("models") or []]
    models = [m for m in models if m][:MAX_LISTED_MODELS]
    return ", ".join(models) or "unknown"


def _footer(run_id: str, budget: dict | None, approver: str | None) -> str:
    models = _models_text(budget)
    approval = ""
    if approver:
        who = f"@{approver}" if _LOGIN.fullmatch(approver) else code_span(approver)
        approval = f", approved by {who}"
    return FOOTER_TEMPLATE.format(
        run_id=_identifier(run_id),
        label="models" if "," in models else "model",
        model=models,
        approval=approval,
    )


def _files_line(diff: dict) -> str:
    files = list(diff.get("files") or [])
    shown = ", ".join(code_span(path) for path in files[:MAX_LISTED_FILES])
    extra = len(files) - MAX_LISTED_FILES
    if extra > 0:
        shown += f", and {extra} more"
    return (
        f"**Changes:** {len(files)} files, +{int(diff.get('insertions') or 0)} "
        f"-{int(diff.get('deletions') or 0)}" + (f"\n\n{shown}" if files else "")
    )


def _tests_line(report: dict | None) -> str | None:
    if not report:
        return None
    state = "passed" if report.get("passed") else "failed"
    code = report.get("exit_code")
    shown_code = (
        f"exit code {int(code)}" if isinstance(code, int) else "exit code unknown"
    )
    line = f"**Tests:** {state}, {shown_code}"
    failed = list(report.get("failed_tests") or [])
    if failed:
        shown = ", ".join(code_span(t) for t in failed[:MAX_LISTED_TESTS])
        extra = len(failed) - MAX_LISTED_TESTS
        line += f"\n\nFailed: {shown}" + (f", and {extra} more" if extra > 0 else "")
    return line


def _review_blocks(review: dict | None, limit: int = BLOCK_LIMIT) -> list[str]:
    if not review:
        return []
    blocks = [f"**Review verdict:** {code_span(review.get('verdict', ''))}"]
    comments = [
        f"[{c.get('severity', '')}] {c.get('file', '')}"
        + (f":{c['line']}" if c.get("line") else "")
        + f" {c.get('issue', '')}"
        for c in review.get("comments") or []
    ]
    if comments:
        blocks += [
            "Reviewer comments (model-written):",
            fence("\n".join(comments), limit=limit),
        ]
    must_fix = [f"- {item}" for item in review.get("must_fix") or []]
    if must_fix:
        blocks += [
            "Reviewer must-fix items (model-written):",
            fence("\n".join(must_fix), limit=limit),
        ]
    return blocks


def _run_line(budget: dict | None) -> str | None:
    if not budget:
        return None
    models = _models_text(budget)
    return (
        f"**Run:** cost ${float(budget.get('cost_usd') or 0):.4f}, "
        f"{int(budget.get('tool_calls') or 0)} tool calls, models {code_span(models)}"
    )


def pr_body(
    *,
    run_id: str,
    issue_number: int | None,
    diff: dict,
    plan: dict | None = None,
    patch: dict | None = None,
    test_report: dict | None = None,
    review: dict | None = None,
    budget: dict | None = None,
    approver: str | None = None,
    patch_sha256: str = "",
    subject: str | None = None,
) -> str:
    """The pull request description. `subject` names the task when there is no issue
    number (bench mode)."""
    reference = (
        f"#{int(issue_number)}"
        if issue_number is not None
        else code_span(subject or "task")
    )

    def build(files_in_body: bool, limit: int) -> str:
        parts = [marker(run_id, "pr"), f"Proposed fix for {reference}"]
        parts.append(
            _files_line(diff) if files_in_body else _files_line({**diff, "files": []})
        )
        if line := _tests_line(test_report):
            parts.append(line)
        if plan and plan.get("summary"):
            parts += [
                "Plan summary (model-written):",
                fence(plan["summary"], limit=limit),
            ]
        if patch and patch.get("summary"):
            parts += [
                "Coder summary (model-written):",
                fence(patch["summary"], limit=limit),
            ]
        if review_parts := _review_blocks(review, limit):
            parts.append("\n\n".join(review_parts))
        if line := _run_line(budget):
            parts.append(line)
        if patch_sha256:
            parts.append(f"**Patch SHA-256:** {code_span(patch_sha256)}")
        parts.append(f"---\n{_footer(run_id, budget, approver)}")
        return "\n\n".join(parts) + "\n"

    # Shrink the blocks, then drop the file list, until the body fits.
    for files_in_body in (True, False):
        for limit in _BLOCK_LIMITS:
            body = build(files_in_body, limit)
            if len(body) <= BODY_LIMIT:
                return body
    return "\n\n".join(
        [
            marker(run_id, "pr"),
            f"Proposed fix for {reference}",
            f"---\n{_footer(run_id, budget, approver)}",
        ]
    )


def failure_comment(
    *,
    run_id: str,
    outcome: str,
    reason: str,
    plan: dict | None = None,
    test_report: dict | None = None,
    approver: str | None = None,
    budget: dict | None = None,
) -> str:
    parts = [
        marker(run_id, "failure"),
        f"The issue-to-pr pipeline did not open a pull request. Outcome: {code_span(outcome)}.",
        "Reason (may be model-written):",
        fence(reason or "no reason recorded"),
    ]
    if plan and plan.get("summary"):
        parts += ["Plan summary (model-written):", fence(plan["summary"])]
    if line := _tests_line(test_report):
        parts.append(line)
    if approver:
        who = f"@{approver}" if _LOGIN.fullmatch(approver) else code_span(approver)
        parts.append(f"Decision by {who}.")
    parts.append(f"---\n{_footer(run_id, budget, None)}")
    return "\n\n".join(parts) + "\n"


__all__ = [
    "AUTHOR_EMAIL",
    "AUTHOR_NAME",
    "FOOTER_TEMPLATE",
    "branch_name",
    "code_span",
    "commit_message",
    "failure_comment",
    "fence",
    "marker",
    "pr_body",
    "pr_title",
]
