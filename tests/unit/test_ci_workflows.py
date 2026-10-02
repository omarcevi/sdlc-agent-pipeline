"""Static checks of the CI, paid-run and release workflows (no network, no GitHub)."""

import re
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
NAMES = ["ci", "paid", "release"]
ALL_FILES = sorted(WORKFLOWS.glob("*.yaml"))


_CONCURRENCY = (
    "${{ inputs.kind == 'bench' && needs.guard.outputs.preset == 'flash' "
    "&& '2' || '1' }}"
)


def _wf(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / f"{name}.yaml").read_text())


def _on(wf: dict) -> dict:
    # PyYAML reads the bare `on` key as boolean True.
    return wf[True] if True in wf else wf["on"]


def _runs(job: dict) -> list[str]:
    return [s["run"] for s in job["steps"] if "run" in s]


def _index(job: dict, needle: str, key: str = "run") -> int:
    return next(i for i, s in enumerate(job["steps"]) if needle in s.get(key, ""))


def test_every_action_is_pinned_to_a_sha_with_a_tag_comment():
    seen = 0
    for path in ALL_FILES:
        for line in path.read_text().splitlines():
            if re.match(r"\s*-?\s*uses:", line):
                seen += 1
                assert re.search(
                    r"uses: [\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+(\.\d+)*$", line
                ), (path.name, line)
    assert seen >= 10


def test_default_permissions_are_read_only_and_id_token_only_where_wif_is_used():
    for name in NAMES:
        assert _wf(name)["permissions"] == {"contents": "read"}, name
    for path in ALL_FILES:
        if path.stem == "pages":
            continue
        wf = yaml.safe_load(path.read_text())
        for job_name, job in wf["jobs"].items():
            perms = job.get("permissions", {})
            where = (path.stem, job_name)
            if perms.get("id-token") == "write":
                assert where in {("paid", "run"), ("release", "deploy")}, where
            if perms.get("contents") == "write":
                assert where == ("release", "release"), where
    assert _wf("paid")["jobs"]["run"]["permissions"] == {
        "contents": "read",
        "id-token": "write",
    }
    assert _wf("release")["jobs"]["deploy"]["permissions"] == {
        "contents": "read",
        "id-token": "write",
    }
    assert _wf("release")["jobs"]["release"]["permissions"] == {"contents": "write"}


def test_every_job_has_a_timeout():
    expected = {
        ("ci", "lint"): 10,
        ("ci", "unit"): 20,
        ("ci", "docker"): 40,
        ("paid", "guard"): 5,
        ("paid", "run"): "${{ inputs.kind == 'bench' && 360 || 90 }}",
        ("release", "check"): 5,
        ("release", "deploy"): 45,
        ("release", "release"): 5,
    }
    for path in ALL_FILES:
        for name, job in yaml.safe_load(path.read_text())["jobs"].items():
            minutes = job["timeout-minutes"]
            assert isinstance(minutes, int) or (path.stem, name) == ("paid", "run")
    for (wf, job), minutes in expected.items():
        assert _wf(wf)["jobs"][job]["timeout-minutes"] == minutes, (wf, job)
    assert {(w, j) for w in NAMES for j in _wf(w)["jobs"]} == set(expected)


def test_no_workflow_uses_pull_request_target():
    for path in ALL_FILES:
        assert "pull_request_target" not in path.read_text(), path.name


def test_ci_has_no_credentials():
    text = (WORKFLOWS / "ci.yaml").read_text()
    for forbidden in (
        "secrets.",
        "vars.",
        "id-token",
        "google-github-actions/auth",
        "GOOGLE_CLOUD_PROJECT",
        "GITHUB_TOKEN",
    ):
        assert forbidden not in text, forbidden


def test_ci_triggers_paths_and_concurrency():
    wf = _wf("ci")
    assert wf["name"] == "CI"
    on = _on(wf)
    assert set(on) == {"pull_request", "push", "workflow_dispatch"}
    # `*.md` is the repository root only: bench/repos/*/README.md is bench input.
    ignore = ["docs/**", "*.md", "web/**"]
    assert on["push"]["branches"] == ["main"]
    assert on["push"]["paths-ignore"] == ignore
    assert on["pull_request"]["paths-ignore"] == ignore
    assert wf["concurrency"] == {
        "group": "ci-${{ github.ref }}",
        "cancel-in-progress": True,
    }


def test_ci_jobs_and_commands():
    jobs = _wf("ci")["jobs"]
    assert set(jobs) == {"lint", "unit", "docker"}
    sync = "uv sync --locked"
    assert _runs(jobs["lint"]) == [
        sync,
        "uv tool install google-agents-cli==1.7.0",
        "agents-cli lint",
    ]
    assert _runs(jobs["unit"]) == [
        sync,
        'uv run pytest -q --tb=no -rf -m "not docker"',
    ]
    assert _runs(jobs["docker"]) == [
        sync,
        "make sandbox-image",
        "uv run pytest -q --tb=no -rf -m docker",
        "uv run python -m bench.validate",
    ]
    for job in jobs.values():
        assert job["steps"][0]["uses"].startswith("actions/checkout@")
        assert job["steps"][0]["with"]["persist-credentials"] is False
        assert any(
            s.get("uses", "").startswith("astral-sh/setup-uv@") for s in job["steps"]
        )


def test_paid_is_dispatch_only_guarded_and_serial():
    wf = _wf("paid")
    assert wf["name"] == "Paid run"
    on = _on(wf)
    assert set(on) == {"workflow_dispatch"}
    inputs = on["workflow_dispatch"]["inputs"]
    assert inputs["kind"]["type"] == "choice"
    assert inputs["kind"]["options"] == ["smoke", "eval", "bench"]
    assert inputs["kind"]["default"] == "smoke"
    assert inputs["system"]["options"] == ["multi", "single"]
    assert inputs["system"]["default"] == "multi"
    assert inputs["preset"]["options"] == ["flash", "pro", "mixed"]
    assert inputs["preset"]["default"] == "flash"
    assert inputs["tasks"]["type"] == "string"
    assert inputs["tasks"]["default"] == ""
    assert inputs["repeats"]["type"] == "string"
    assert inputs["repeats"]["default"] == "1"
    assert inputs["accept_over_25"]["type"] == "boolean"
    assert inputs["accept_over_25"]["default"] is False
    assert wf["concurrency"] == {"group": "paid", "cancel-in-progress": False}
    jobs = wf["jobs"]
    assert set(jobs) == {"guard", "run"}
    assert jobs["run"]["needs"] == "guard"
    guard = jobs["guard"]
    assert any(
        s.get("uses", "").startswith("actions/setup-python@") for s in guard["steps"]
    )
    assert set(guard["outputs"]) == {"tasks", "system", "preset", "repeats", "runs"}
    step = next(s for s in guard["steps"] if "ci_guard.py" in s.get("run", ""))
    assert "python scripts/ci_guard.py" in step["run"]
    assert step["env"]["KIND"] == "${{ inputs.kind }}"
    assert step["env"]["REF"] == "${{ github.ref }}"
    assert "--ref" in step["run"] and "--kind" in step["run"]
    assert "--accept-over-25" in step["run"] and "--repeats" in step["run"]
    assert 'if [ "$KIND" = bench ]' in step["run"]
    for job in jobs.values():
        for run in _runs(job):
            assert "${{" not in run, run


def test_paid_smoke_gate_and_artifact():
    job = _wf("paid")["jobs"]["run"]
    auth = next(
        s
        for s in job["steps"]
        if s.get("uses", "").startswith("google-github-actions/auth@")
    )
    assert auth["with"] == {
        "workload_identity_provider": "${{ vars.WIF_PROVIDER }}",
        "service_account": "${{ vars.CI_RUNNER_SA }}",
    }
    assert job["env"] == {
        "GOOGLE_CLOUD_PROJECT": "${{ vars.GCP_PROJECT_ID }}",
        "GOOGLE_CLOUD_LOCATION": "global",
        "GOOGLE_GENAI_USE_VERTEXAI": "true",
        "ENVIRONMENT_BACKEND": "docker",
    }
    runs = _runs(job)
    assert "make sandbox-image" in runs
    assert "make eval" in runs
    # credentials go live only after every install, right before the first use
    auth_at = _index(job, "google-github-actions/auth", "uses")
    for installed in ("uv sync --locked", "agents-cli==1.7.0", "make sandbox-image"):
        assert _index(job, installed) < auth_at, installed
    assert auth_at + 1 == _index(job, "bench.run")
    bench = next(s for s in job["steps"] if "bench.run" in s.get("run", ""))
    assert (
        '--concurrency "$CONCURRENCY"' in bench["run"]
        and "--out results/ci" in bench["run"]
    )
    assert bench["if"] == "inputs.kind != 'eval'"
    gate = next(s for s in job["steps"] if "resolved" in s.get("run", ""))
    assert gate["if"] == "inputs.kind == 'smoke'"
    assert job["steps"].index(gate) > job["steps"].index(bench)
    assert ">= 2" in gate["run"]
    upload = job["steps"][-1]
    assert upload["uses"].startswith("actions/upload-artifact@")
    assert upload["if"] == "always()"
    assert upload["with"]["retention-days"] == 14
    assert "results/ci/**" in upload["with"]["path"]
    assert "artifacts/grade_results/**" in upload["with"]["path"]
    assert "artifacts/traces" not in upload["with"]["path"]
    assert "runs/*/record.json" in upload["with"]["path"]
    assert _index(job, "google-github-actions/auth", "uses") < _index(job, "bench.run")
    assert upload["with"]["name"] == (
        "paid-run-${{ github.run_id }}-${{ github.run_attempt }}"
    )


def test_paid_timeout_covers_a_bench_and_smoke_stays_serial():
    job = _wf("paid")["jobs"]["run"]
    assert job["timeout-minutes"] == ("${{ inputs.kind == 'bench' && 360 || 90 }}")
    smoke = next(s for s in job["steps"] if s.get("name") == "Bench run")
    # smoke keeps one worker; bench uses two for flash and one for pro and mixed
    assert smoke["env"]["CONCURRENCY"] == _CONCURRENCY


def test_paid_bench_step_takes_its_values_from_the_guard_outputs_only():
    job = _wf("paid")["jobs"]["run"]
    step = next(s for s in job["steps"] if "bench.run" in s.get("run", ""))
    assert step["env"] == {
        "TASKS": "${{ needs.guard.outputs.tasks }}",
        "SYSTEM": "${{ needs.guard.outputs.system }}",
        "PRESET": "${{ needs.guard.outputs.preset }}",
        "REPEATS": "${{ needs.guard.outputs.repeats }}",
        "CONCURRENCY": _CONCURRENCY,
    }
    assert "${{" not in step["run"]
    for flag, var in (
        ("--tasks", "TASKS"),
        ("--system", "SYSTEM"),
        ("--preset", "PRESET"),
        ("--repeats", "REPEATS"),
    ):
        assert f'{flag} "${var}"' in step["run"]
    for s in job["steps"]:
        if "bench.run" in s.get("run", ""):
            assert "inputs." not in s["run"]
            for name in ("TASKS", "SYSTEM", "PRESET", "REPEATS"):
                assert "inputs." not in s["env"][name]


def test_release_checks_main_before_the_approval_and_auths_after_installs():
    wf = _wf("release")
    assert wf["name"] == "Release"
    assert _on(wf) == {"push": {"tags": ["v*"]}}
    assert wf["concurrency"] == {"group": "release", "cancel-in-progress": False}
    # The ancestry check is its own job, outside the environment, so a bad tag
    # fails before the owner is asked to approve.
    check = wf["jobs"]["check"]
    assert "environment" not in check
    assert check["permissions"] == {"contents": "read"}
    assert check["steps"][0]["uses"].startswith("actions/checkout@")
    assert check["steps"][0]["with"]["fetch-depth"] == 0
    assert check["steps"][0]["with"]["persist-credentials"] is False
    assert not any("google-github-actions" in s.get("uses", "") for s in check["steps"])
    run = next(s["run"] for s in check["steps"] if "merge-base" in s.get("run", ""))
    assert (
        'git fetch "https://github.com/$GITHUB_REPOSITORY" '
        "+refs/heads/main:refs/remotes/origin/main"
    ) in run
    assert 'git merge-base --is-ancestor "$GITHUB_SHA" origin/main' in run
    assert "the tag is not on main" in run
    deploy = wf["jobs"]["deploy"]
    assert deploy["needs"] == "check"
    assert deploy["environment"] == "production"
    checkout = deploy["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["persist-credentials"] is False
    assert not any("merge-base" in r for r in _runs(deploy))
    auth = _index(deploy, "google-github-actions/auth", "uses")
    assert deploy["steps"][auth]["with"] == {
        "workload_identity_provider": "${{ vars.WIF_PROVIDER }}",
        "service_account": "${{ vars.DEPLOYER_SA }}",
    }
    assert _runs(deploy) == [
        "uv sync --locked",
        "uv tool install google-agents-cli==1.7.0",
        "uv run python scripts/stage_deploy.py deploy",
        "uv run python scripts/stage_deploy.py smoke",
    ]
    # credentials go live after the installs, right before the first deploy step
    assert _index(deploy, "agents-cli==1.7.0") < auth
    assert auth + 1 == _index(deploy, "stage_deploy.py deploy")
    assert deploy["env"] == {
        "GOOGLE_CLOUD_PROJECT": "${{ vars.GCP_PROJECT_ID }}",
        "ITP_TF_OUTPUTS": "${{ vars.ITP_TF_OUTPUTS }}",
        "GOOGLE_CLOUD_LOCATION": "global",
    }
    release = wf["jobs"]["release"]
    assert release["needs"] == "deploy"
    step = next(s for s in release["steps"] if "gh release create" in s.get("run", ""))
    assert (
        step["run"]
        == 'gh release create "$GITHUB_REF_NAME" --generate-notes --verify-tag'
    )
    # No checkout in this job, so gh needs the repository named.
    assert step["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "GH_REPO": "${{ github.repository }}",
    }


def test_scaffold_workflows_are_gone():
    present = {p.name for p in WORKFLOWS.glob("*.y*ml")}
    assert present == {"ci.yaml", "paid.yaml", "release.yaml", "pages.yaml"}
