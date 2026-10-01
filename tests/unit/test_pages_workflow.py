"""Static checks of the GitHub Pages workflow (no network, no GitHub)."""

import re
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "pages.yaml"
SITE_PATHS = ["web/**", "bench/replay_check.py", ".github/workflows/pages.yaml"]


def _wf() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _triggers(wf: dict) -> dict:
    # PyYAML reads the bare `on` key as boolean True.
    return wf[True] if True in wf else wf["on"]


def _steps(job: str) -> list[dict]:
    return _wf()["jobs"][job]["steps"]


def _step_using(job: str, action: str) -> dict:
    return next(s for s in _steps(job) if s.get("uses", "").startswith(action + "@"))


def test_triggers_are_main_pushes_pull_requests_and_dispatch_on_the_site_paths():
    on = _triggers(_wf())
    assert set(on) == {"push", "pull_request", "workflow_dispatch"}
    assert on["push"]["branches"] == ["main"]
    assert on["push"]["paths"] == SITE_PATHS
    assert on["pull_request"]["paths"] == SITE_PATHS


def test_only_the_deploy_job_has_pages_and_id_token():
    wf = _wf()
    assert wf["permissions"] == {"contents": "read"}
    assert "permissions" not in wf["jobs"]["build"]
    assert wf["jobs"]["deploy"]["permissions"] == {
        "pages": "write",
        "id-token": "write",
    }
    assert set(wf["jobs"]) == {"build", "deploy"}


def test_no_job_has_contents_write():
    wf = _wf()
    perms = [wf.get("permissions", {})]
    perms += [j.get("permissions", {}) for j in wf["jobs"].values()]
    for p in perms:
        assert isinstance(p, dict)
        assert p.get("contents") != "write"


def test_pull_requests_never_deploy():
    wf = _wf()
    deploy_if = wf["jobs"]["deploy"]["if"]
    assert "github.event_name != 'pull_request'" in deploy_if
    assert "github.ref == 'refs/heads/main'" in deploy_if
    assert wf["jobs"]["deploy"]["needs"] == "build"
    upload = _step_using("build", "actions/upload-pages-artifact")
    assert upload["if"] == "github.event_name != 'pull_request'"


def test_every_action_is_pinned_to_a_full_sha():
    uses = [
        s["uses"] for job in _wf()["jobs"].values() for s in job["steps"] if "uses" in s
    ]
    assert len(uses) >= 5
    for u in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u), u
    for line in WORKFLOW.read_text().splitlines():
        if "uses:" in line:
            assert re.search(r"@[0-9a-f]{40} # v\d+\.\d+\.\d+$", line), line


def test_checkout_does_not_persist_credentials():
    step = _step_using("build", "actions/checkout")
    assert step["with"]["persist-credentials"] is False


def test_the_leak_check_runs_before_npm_and_receives_replay_redact():
    steps = _steps("build")
    leak = next(i for i, s in enumerate(steps) if "replay_check.py" in s.get("run", ""))
    npm = next(i for i, s in enumerate(steps) if "npm" in s.get("run", ""))
    assert leak < npm
    assert steps[leak]["run"] == "python3 bench/replay_check.py web/public/replays"
    assert steps[leak]["env"] == {"REPLAY_REDACT": "${{ secrets.REPLAY_REDACT }}"}


def test_the_artifact_is_web_dist():
    upload = _step_using("build", "actions/upload-pages-artifact")
    assert upload["with"]["path"] == "web/dist"
    assert _step_using("deploy", "actions/deploy-pages")["id"] == "deployment"
