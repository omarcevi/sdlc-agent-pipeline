"""Issue intake and sandbox provisioning (bench mode)."""

import re
import tempfile
from pathlib import Path

from google.adk.events.event import Event

from app.environment import registry
from app.environment.base import WORKDIR, InfraError
from app.environment.factory import start_environment
from app.schemas import IssueTask, RunRequest
from app.task_store import load_task, materialize, test_files

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


def fetch_issue(node_input: RunRequest):
    spec = load_task(node_input.task_id)
    issue = IssueTask(
        task_id=spec.task_id,
        run_id=node_input.run_id,
        repo=spec.repo,
        title=spec.title,
        body=spec.body,
    )
    yield Event(message=f"issue: {spec.title}")
    yield Event(
        output=issue,
        state={
            "issue": issue.model_dump(),
            "issue_text": f"Title: {spec.title}\n\n{spec.body}",
            "test_attempts": 0,
            "review_rounds": 0,
            "failure": None,
            "outcome": None,
        },
    )


async def provision_sandbox(node_input: IssueTask):
    spec = load_task(node_input.task_id)
    with tempfile.TemporaryDirectory() as tmp:
        repo_dir = materialize(spec, Path(tmp) / "repo")
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
        except Exception:
            await registry.release(env.env_id)
            raise
    yield Event(message=f"sandbox {env.env_id} ready")
    yield Event(
        output=node_input,
        state={
            "sandbox_id": env.env_id,
            "protected_paths": protected,
            "baseline_sha": baseline_sha,
        },
    )
