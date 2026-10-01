"""Issue intake and sandbox provisioning (bench and live mode)."""

import json
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from google.adk.events.event import Event

from app.archive import ArchiveError, extract_tarball
from app.environment import registry
from app.environment.base import WORKDIR, InfraError
from app.environment.factory import start_environment
from app.github_client import GitHubClient, GitHubError
from app.live_config import TRIGGER_LABEL, allowed_users, live_repos
from app.nodes.finish import runs_dir
from app.schemas import IssueTask, RunRequest
from app.task_store import load_task, materialize, test_files
from app.textfold import normalised

# The pipeline's git directory lives outside the worktree, and every pipeline git
# command names it explicitly. The worktree keeps a `.git` file so the coder's own
# git commands work, but nothing the coder does to that file (or to HEAD, by
# committing) can hide changes from collect_diff.
PIPELINE_GIT_DIR = "/workspace/.pipeline-git"
GIT = f"git --git-dir={PIPELINE_GIT_DIR} --work-tree={WORKDIR}"
GIT_BASELINE = (
    f"git init -q --separate-git-dir {PIPELINE_GIT_DIR} && "
    f"printf '__pycache__/\\n.pytest_cache/\\n*.pyc\\n' >> {PIPELINE_GIT_DIR}/info/exclude && "
    f"{GIT} add -A && "
    f"{GIT} -c user.name=pipeline -c user.email=pipeline@localhost commit -q -m baseline"
)
BASELINE_SHA_CMD = f"{GIT} rev-parse HEAD"
_SHA = re.compile(r"[0-9a-f]{40,64}")


class RunRefused(Exception):
    """A precondition of the run is not met. The message is a fixed reason that
    carries no issue content; the driver records outcome `refused`."""


MAX_TITLE_CHARS = 500
MAX_BODY_CHARS = 20_000
TRUNCATED = "\n[issue text truncated]"
# The opening fragment of an issue tag: `<`, or `&lt;` / `&#60;` / `&#x3c;` (also
# escaped again, or without the semicolon), optional space, optional `/` (also
# JSON-escaped as `\/`), the word `issue`. What follows (attributes, the closing
# `>`) does not matter: without its opening fragment a tag is inert.
_ISSUE_TAG = re.compile(
    r"(?:<|&(?:amp;)*(?:lt|#0*60|#x0*3c);?)\s*(\\?\s*/)?\s*issue\b", re.IGNORECASE
)
PLANNER_PROMPT = "Plan the change for the issue in your instructions."


def _defuse(text: str) -> str:
    """Replace the opening fragment of every issue tag in `text`, however it is
    spelled, by `[issue` or `[/issue`."""
    folded, origin = normalised(text)
    out: list[str] = []
    position = 0
    for match in _ISSUE_TAG.finditer(folded):
        start = origin[match.start()]
        end = origin[match.end() - 1] + 1
        if start < position:
            continue
        out.append(text[position:start])
        out.append("[/issue" if match.group(1) else "[issue")
        position = end
    out.append(text[position:])
    return "".join(out)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + TRUNCATED


def format_issue_text(title: str, body: str) -> str:
    """The issue as the agents see it: between <issue> tags, with the opening
    fragment of any issue tag in the text neutralised so it cannot close the block,
    and with the length capped. The neutralising runs once on the assembled text, so
    a tag cannot be split across the title and the body."""
    inner = f"Title: {_clip(title, MAX_TITLE_CHARS)}\n\n{_clip(body, MAX_BODY_CHARS)}"
    return f"<issue>\n{_defuse(inner)}\n</issue>"


def _initial_state(issue: IssueTask) -> dict:
    return {
        "issue": issue.model_dump(),
        "issue_text": format_issue_text(issue.title, issue.body),
        "test_attempts": 0,
        "review_rounds": 0,
        "failure": None,
        "outcome": None,
    }


def fetch_issue(node_input: RunRequest):
    if node_input.mode == "live":
        raise RunRefused("live requests need the live graph")
    spec = load_task(node_input.task_id)
    issue = IssueTask(
        task_id=spec.task_id,
        run_id=node_input.run_id,
        repo=spec.repo,
        title=spec.title,
        body=spec.body,
    )
    yield Event(message=f"issue: {spec.title}")
    yield Event(output=issue, state=_initial_state(issue))


def _is_pull_request(html_url: str) -> bool:
    # GET /issues/{n} also answers for pull requests; their html_url is
    # https://<host>/<owner>/<repo>/pull/<n>. Only the third path segment counts: a
    # repository or owner may be named "pull".
    segments = urlparse(html_url).path.split("/")
    return len(segments) > 3 and segments[3] == "pull"


def _refused(exc: GitHubError) -> RunRefused:
    if "archive exceeds" in exc.message:
        return RunRefused("source archive is too large")
    if exc.status is None:
        return RunRefused("GitHub response could not be read safely")
    return RunRefused(f"GitHub refused the request: {exc.status}")


async def fetch_live_issue(node_input: RunRequest):
    """Live-graph `fetch_issue`: check every precondition, pin the base commit,
    save the source archive. Reads no comments and writes nothing to GitHub."""
    if node_input.mode != "live":
        raise RunRefused("bench requests need the bench graph")
    repo, number = node_input.repo, node_input.issue_number
    if repo.lower() not in live_repos():
        raise RunRefused("repository is not in LIVE_REPOS")
    try:
        client = GitHubClient.from_token_file()
    except GitHubError:
        raise RunRefused("no usable GitHub token") from None
    users = allowed_users()
    archive = runs_dir() / node_input.run_id / "source.tar.gz"
    async with client:
        try:
            # Asked first so that a repository or token problem is reported as
            # such, and a 404 below can only mean a missing issue.
            default_branch = await client.default_branch(repo)
            try:
                issue = await client.get_issue(repo, number)
            except GitHubError as exc:
                if exc.status == 404:
                    raise RunRefused("issue does not exist") from None
                raise
            if _is_pull_request(issue.html_url):
                raise RunRefused("number is a pull request, not an issue")
            if issue.state != "open":
                raise RunRefused("issue is not open")
            if issue.author.lower() not in users:
                raise RunRefused("issue author is not allowed")
            actor = (
                await client.last_label_actor(repo, number, TRIGGER_LABEL)
                if TRIGGER_LABEL in issue.labels
                else None
            )
            if actor is None or actor.lower() not in users:
                raise RunRefused("issue is not labelled agent-ok by an allowed user")
            prs = await client.open_pipeline_prs(repo, number)
            if prs:
                raise RunRefused(
                    f"issue already has an open pipeline pull request #{prs[0].number}"
                )
            base_ref = node_input.base_ref or default_branch
            try:
                base_sha, base_tree_sha = await client.branch_head(repo, base_ref)
            except json.JSONDecodeError:
                # A bad response, not a bad branch name. No body text in the error.
                raise InfraError("GitHub sent a response that is not JSON") from None
            except ValueError:
                raise RunRefused("base branch name is not valid") from None
            await client.download_tarball(repo, base_sha, archive)
        except GitHubError as exc:
            raise _refused(exc) from None
    task = IssueTask(
        task_id=f"{repo}#{number}",
        run_id=node_input.run_id,
        repo=repo,
        title=issue.title,
        body=issue.body,
        mode="live",
        issue_number=number,
        base_ref=base_ref,
        base_sha=base_sha,
        base_tree_sha=base_tree_sha,
        html_url=issue.html_url,
    )
    yield Event(message=f"issue: {issue.title}")
    yield Event(
        output=task, state={**_initial_state(task), "source_archive": str(archive)}
    )


def _source_tree(node_input: IssueTask, source_archive: str | None, tmp: str) -> Path:
    """The working copy to upload: the planted bench repo, or the live archive."""
    if node_input.mode == "bench":
        return materialize(load_task(node_input.task_id), Path(tmp) / "repo")
    if not source_archive:
        raise InfraError("live run has no source archive in state")
    try:
        return extract_tarball(Path(source_archive), Path(tmp) / "repo")
    except ArchiveError as exc:
        raise RunRefused(f"source archive refused: {exc}") from None


async def provision_sandbox(node_input: IssueTask, source_archive: str | None = None):
    with tempfile.TemporaryDirectory() as tmp:
        repo_dir = _source_tree(node_input, source_archive, tmp)
        protected = test_files(repo_dir)
        env = await start_environment()
        registry.register(env)
        try:
            await env.upload_dir(repo_dir, WORKDIR)
            baseline = await env.exec(GIT_BASELINE)
            if baseline.exit_code != 0:
                raise InfraError(f"git baseline failed: {baseline.stderr.strip()}")
            baseline_sha = (await env.exec(BASELINE_SHA_CMD)).stdout.strip()
            if not _SHA.fullmatch(baseline_sha):
                raise InfraError(f"git baseline has no commit id: {baseline_sha!r}")
        except BaseException:
            # BaseException: a cancellation (the run's wall-clock cap, Ctrl-C) must
            # not leave the registered sandbox behind; sandbox_id is not in state yet.
            await registry.release(env.env_id)
            raise
    yield Event(message=f"sandbox {env.env_id} ready")
    yield Event(
        output=PLANNER_PROMPT,
        state={
            "sandbox_id": env.env_id,
            "protected_paths": protected,
            "baseline_sha": baseline_sha,
        },
    )
