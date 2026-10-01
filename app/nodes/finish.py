"""Terminal nodes: write the patch, open the pull request, report a failure.

`deliver_patch` renders the public pull request text in both modes. In live mode
`open_pr` publishes it through the GitHub Git Data API (no clone, no push), and
`report_failure` also leaves one comment on the issue. The GitHub token is read
only by the GitHubClient built inside `open_pr` and `post_failure_comment`.
"""

import asyncio
import hashlib
import logging
import os
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from google.adk.events.event import Event

from app import pr_text
from app.environment.base import InfraError
from app.github_client import FileChange, GitHubClient, GitHubError

logger = logging.getLogger(__name__)

_GIT_TIMEOUT_S = 60


def runs_dir() -> Path:
    return Path(os.environ.get("RUNS_DIR", "runs"))


def _issue_number(issue: dict) -> int | None:
    number = issue.get("issue_number")
    return int(number) if number is not None else None


def _patch_hash(diff: dict) -> str:
    return hashlib.sha256(diff["unified_diff"].encode("utf-8")).hexdigest()


def deliver_patch(
    node_input: Any,
    issue: dict,
    diff: dict,
    plan: dict | None = None,
    patch: dict | None = None,
    test_report: dict | None = None,
    review: dict | None = None,
    budget: dict | None = None,
):
    run_dir = runs_dir() / issue["run_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "patch.diff"
    path.write_text(diff["unified_diff"])
    digest = _patch_hash(diff)
    body = pr_text.pr_body(
        run_id=issue["run_id"],
        issue_number=_issue_number(issue),
        subject=issue.get("task_id"),
        diff=diff,
        plan=plan,
        patch=patch,
        test_report=test_report,
        review=review,
        budget=budget,
        patch_sha256=digest,
    )
    (run_dir / "pr_body.md").write_text(body)
    outcome = {
        "outcome": "patch_written",
        "failure_kind": "none",
        "reason": "",
        "patch_path": str(path),
    }
    yield Event(message=f"patch written to {path}\n\n{body}")
    # The commit date: open_pr passes it on, so a re-run makes the same commit.
    created = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    yield Event(
        output=outcome,
        state={"outcome": outcome, "patch_sha256": digest, "patch_created_at": created},
    )


# --- open_pr ---------------------------------------------------------------------------


def extract_tarball(
    archive: Path,
    dest: Path,
    *,
    max_files: int = 5000,
    max_bytes: int = 50_000_000,
) -> Path:
    """Extract a GitHub source archive into `dest`, stripping its single top-level
    directory. Refuses links, devices, too many files and too many bytes.

    A local stand-in with the signature of Task 2's `app.archive.extract_tarball`;
    replace this definition with that import when the two are merged.
    """
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        if len(members) > max_files:
            raise RuntimeError("the source archive has too many files")
        if sum(m.size for m in members if m.isfile()) > max_bytes:
            raise RuntimeError("the source archive is too large")
        tops = {m.name.split("/", 1)[0] for m in members}
        if len(tops) != 1:
            raise RuntimeError("the source archive must have one top-level directory")
        seen: set[str] = set()
        for member in members:
            if not (member.isfile() or member.isdir()):
                raise RuntimeError("the source archive contains a link or special file")
            if "/" not in member.name:
                continue  # the top-level directory itself
            parts = member.name.split("/")[1:]
            if any(part.lower() == ".git" for part in parts):
                raise RuntimeError("the source archive contains a .git path")
            folded = "/".join(p.lower() for p in parts if p not in ("", "."))
            if folded in seen:
                raise RuntimeError("the source archive has duplicate member names")
            seen.add(folded)
            stripped = member.replace(name=member.name.split("/", 1)[1], deep=False)
            tar.extract(stripped, dest, filter="data")
    return dest


def _git(args: list[str], cwd: Path, home: Path, stdin: bytes | None = None):
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CEILING_DIRECTORIES": str(home),
        "LC_ALL": "C",
    }
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        input=stdin,
        capture_output=True,
        timeout=_GIT_TIMEOUT_S,
    )


def _patch_paths(raw: bytes) -> list[str]:
    """Paths a patch touches, as git itself reads them (`apply --numstat -z`)."""
    fields = raw.decode("utf-8", errors="strict").split("\0")
    paths: list[str] = []
    index = 0
    while index < len(fields) and fields[index]:
        _added, _deleted, path = fields[index].split("\t", 2)
        if not path:
            raise RuntimeError("the patch contains a rename or copy")
        paths.append(path)
        index += 1
    return paths


def _is_github_path(path: str) -> bool:
    parts = [p for p in path.lower().split("/") if p not in ("", ".")]
    return bool(parts) and parts[0] == ".github"


def _check_paths(paths: list[str], protected: set[str]) -> None:
    for path in paths:
        parts = path.split("/")
        if path.startswith("/") or ".." in parts or "\\" in path:
            raise RuntimeError("refusing a patch with an unsafe path")
        if _is_github_path(path):
            raise RuntimeError("refusing a patch that touches .github")
        if path in protected:
            raise RuntimeError("refusing a patch that touches a protected path")
        if ".git" in path.lower().split("/"):
            raise RuntimeError("refusing a patch that touches a .git path")


def _build_changes(unified_diff: str, archive: Path, protected: set[str]):
    """Apply the patch to the pinned source with host git and read the result."""
    with tempfile.TemporaryDirectory(prefix="issue-to-pr-apply-") as tmp:
        home = Path(tmp).resolve()
        repo = home / "repo"
        extract_tarball(archive, repo)
        # A .git directory or file would make git treat the tree as a repository:
        # its config could name filter commands, a gitdir file another repository.
        if os.path.lexists(repo / ".git"):
            raise RuntimeError("the source archive contains a .git path")
        patch_bytes = unified_diff.encode("utf-8")
        listing = _git(["apply", "--numstat", "-z", "-"], repo, home, patch_bytes)
        if listing.returncode != 0:
            raise RuntimeError("the patch does not apply to the source archive")
        paths = _patch_paths(listing.stdout)
        _check_paths(paths, protected)
        for step in (["apply", "--check", "-"], ["apply", "-"]):
            result = _git(step, repo, home, patch_bytes)
            if result.returncode != 0:
                raise RuntimeError("the patch does not apply to the source archive")
        changes = []
        for path in paths:
            target = repo / path
            if target.is_symlink():
                raise RuntimeError("refusing a patch that creates a symbolic link")
            if target.is_file():
                mode = target.stat().st_mode
                changes.append(
                    FileChange(path, target.read_bytes(), executable=bool(mode & 0o100))
                )
            elif not target.exists():
                changes.append(FileChange(path, None))
            else:
                raise RuntimeError("the patch leaves something other than a file")
        if not changes:
            raise RuntimeError("the patch changes nothing")
        return changes


async def open_pr(
    node_input: Any,
    issue: dict,
    diff: dict,
    patch_sha256: str,
    source_archive: str,
    protected_paths: list[str] | None = None,
    plan: dict | None = None,
    patch: dict | None = None,
    test_report: dict | None = None,
    review: dict | None = None,
    budget: dict | None = None,
    patch_created_at: str | None = None,
):
    def decided(name: str) -> Any:
        if isinstance(node_input, dict):
            return node_input.get(name)
        return getattr(node_input, name, None)

    if decided("approved") is not True:
        raise RuntimeError("refusing to open a pull request: not approved")
    digest = _patch_hash(diff)
    if not (digest == patch_sha256 == decided("patch_sha256")):
        raise RuntimeError("refusing to open a pull request: the patch hash differs")
    protected = set(protected_paths or [])
    _check_paths(list(diff.get("files") or []), protected)
    changes = await asyncio.to_thread(
        _build_changes, diff["unified_diff"], Path(source_archive), protected
    )

    repo = issue["repo"]
    number = int(issue["issue_number"])
    run_id = issue["run_id"]
    approver = decided("approver")
    body = pr_text.pr_body(
        run_id=run_id,
        issue_number=number,
        diff=diff,
        plan=plan,
        patch=patch,
        test_report=test_report,
        review=review,
        budget=budget,
        approver=approver,
        patch_sha256=digest,
    )
    async with GitHubClient.from_token_file() as client:
        commit_sha = await client.create_commit(
            repo,
            parent_sha=issue["base_sha"],
            base_tree_sha=issue["base_tree_sha"],
            changes=changes,
            message=pr_text.commit_message(number, issue["title"]),
            author_name=pr_text.AUTHOR_NAME,
            author_email=pr_text.AUTHOR_EMAIL,
            date=patch_created_at,
        )
        branch = pr_text.branch_name(number, run_id)
        await client.ensure_branch(repo, branch, commit_sha)
        pull = await client.open_pull_request(
            repo,
            head=branch,
            base=issue["base_ref"],
            title=pr_text.pr_title(issue["title"]),
            body=body,
        )
    outcome = {
        "outcome": "pr_opened",
        "failure_kind": "none",
        "reason": "",
        "patch_path": str(runs_dir() / run_id / "patch.diff"),
        "pr_url": pull.html_url,
    }
    yield Event(message=f"pull request opened: {pull.html_url}")
    yield Event(output=outcome, state={"outcome": outcome})


# --- failures ---------------------------------------------------------------------------


async def post_failure_comment(
    issue: dict,
    *,
    run_id: str,
    outcome: str,
    reason: str,
    plan: dict | None = None,
    test_report: dict | None = None,
    approver: str | None = None,
    budget: dict | None = None,
) -> bool:
    """Leave one failure comment on the issue (once per run). True when a comment
    with this run's marker exists afterwards; GitHub errors are logged, not raised."""
    try:
        body = pr_text.failure_comment(
            run_id=run_id,
            outcome=outcome,
            reason=reason,
            plan=plan,
            test_report=test_report,
            approver=approver,
            budget=budget,
        )
        async with GitHubClient.from_token_file() as client:
            await client.comment_once(
                issue["repo"],
                int(issue["issue_number"]),
                body=body,
                marker=pr_text.marker(run_id, "failure"),
            )
        return True
    except (
        GitHubError,
        InfraError,
        httpx.HTTPError,
        OSError,
    ) as exc:
        logger.warning("could not post the failure comment: %s", type(exc).__name__)
        return False


_LIVE_OUTCOMES = {
    "declined": ("declined", "none"),
    "agent": ("failed", "agent"),
    "rejected": ("rejected", "none"),
}


async def report_failure(
    node_input: Any,
    failure: dict,
    issue: dict | None = None,
    plan: dict | None = None,
    test_report: dict | None = None,
    budget: dict | None = None,
    approver: str | None = None,
):
    live = bool(issue) and issue.get("mode") == "live"
    if live:
        name, failure_kind = _LIVE_OUTCOMES.get(failure["kind"], ("failed", "agent"))
    else:
        declined = failure["kind"] == "declined"
        name = "declined" if declined else "failed"
        failure_kind = "none" if declined else "agent"
    outcome = {
        "outcome": name,
        "failure_kind": failure_kind,
        "reason": failure["reason"],
        "patch_path": None,
    }
    if live:
        outcome["comment_posted"] = await post_failure_comment(
            issue,
            run_id=issue["run_id"],
            outcome=name,
            reason=failure["reason"],
            plan=plan,
            test_report=test_report,
            approver=approver,
            budget=budget,
        )
    yield Event(message=f"{name}: {failure['reason']}")
    yield Event(output=outcome, state={"outcome": outcome})
