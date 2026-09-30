"""Issue intake and sandbox provisioning (bench mode)."""

import tempfile
from pathlib import Path

from google.adk.events.event import Event

from app.environment import registry
from app.environment.base import WORKDIR, InfraError
from app.environment.factory import start_environment
from app.schemas import IssueTask, RunRequest
from app.task_store import load_task, materialize, test_files

GIT_BASELINE = (
    "git init -q && "
    "printf '__pycache__/\\n.pytest_cache/\\n*.pyc\\n' >> .git/info/exclude && "
    "git add -A && "
    "git -c user.name=pipeline -c user.email=pipeline@localhost commit -q -m baseline"
)


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
        except Exception:
            await registry.release(env.env_id)
            raise
    yield Event(message=f"sandbox {env.env_id} ready")
    yield Event(
        output=node_input,
        state={"sandbox_id": env.env_id, "protected_paths": protected},
    )
