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
    main_push = "github.event_name != 'pull_request' && github.ref == 'refs/heads/main'"
    assert wf["jobs"]["deploy"]["if"] == main_push
    assert wf["jobs"]["deploy"]["needs"] == "build"
    upload = _step_using("build", "actions/upload-pages-artifact")
    assert upload["if"] == main_push


def test_the_leak_check_runs_before_the_upload():
    steps = _steps("build")
    leak = next(i for i, s in enumerate(steps) if "replay_check.py" in s.get("run", ""))
    upload = next(
        i for i, s in enumerate(steps) if s.get("uses", "").startswith("actions/upload-pages-artifact@")
    )
    assert leak < upload


def test_every_job_has_a_timeout():
    for name, job in _wf()["jobs"].items():
        assert job["timeout-minutes"] == 20, name


def test_npm_ci_skips_install_scripts():
    runs = [s["run"] for s in _steps("build") if "npm ci" in s.get("run", "")]
    assert runs == ["npm ci --ignore-scripts"]


def test_off_pull_requests_an_empty_redaction_secret_fails_before_the_leak_check():
    steps = _steps("build")
    gate = next(i for i, s in enumerate(steps) if s.get("name") == "Require the redaction secret")
    leak = next(i for i, s in enumerate(steps) if "replay_check.py" in s.get("run", ""))
    assert gate < leak
    assert steps[gate]["if"] == "github.event_name != 'pull_request'"
    assert steps[gate]["env"] == {"REPLAY_REDACT": "${{ secrets.REPLAY_REDACT }}"}
    assert 'test -n "$REPLAY_REDACT"' in steps[gate]["run"]


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
