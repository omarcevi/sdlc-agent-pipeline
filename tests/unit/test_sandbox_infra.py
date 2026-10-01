"""scripts/sandbox_infra.py against a fake platform and a recording fake for subprocess
calls. No GCP, no network, no SDK import."""

import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sandbox_infra.py"
_spec = importlib.util.spec_from_file_location("sandbox_infra", SCRIPT)
infra = importlib.util.module_from_spec(_spec)
sys.modules["sandbox_infra"] = infra
_spec.loader.exec_module(infra)

ENGINE = "projects/123/locations/us-central1/reasoningEngines/456"
REPOSITORY = "us-central1-docker.pkg.dev/proj/issue-to-pr"
CALLER = "sandbox-caller@proj.iam.gserviceaccount.com"
TREE = "abcdef0123456789abcdef0123456789abcdef01"
TAG = TREE[:12]
IMAGE = f"{REPOSITORY}/sandbox:{TAG}"
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
TF_OUTPUTS = {
    "agent_runtime_resource_name": ENGINE,
    "sandbox_image_repository": REPOSITORY,
    "sandbox_caller_email": CALLER,
}


def done(stdout="", code=0):
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr="")


class FakeRun:
    """Records every command; answers by prefix. ``overrides`` maps a prefix string
    (the joined leading words) to a CompletedProcess."""

    def __init__(self, *, dirty=False, image_exists=False, tf_ok=True):
        self.calls: list[list[str]] = []
        self.dirty = dirty
        self.image_exists = image_exists
        self.tf_ok = tf_ok

    def __call__(self, cmd, **kwargs):
        cmd = list(cmd)
        self.calls.append(cmd)
        joined = " ".join(cmd)
        if "status --porcelain" in joined:
            return done(" M sandbox_image/Dockerfile\n" if self.dirty else "")
        if "rev-parse HEAD:sandbox_image" in joined:
            return done(TREE + "\n")
        if cmd[0] == "terraform":
            payload = {k: {"value": v} for k, v in TF_OUTPUTS.items()}
            return done(json.dumps(payload), 0 if self.tf_ok else 1)
        if "images describe" in joined:
            return done("", 0 if self.image_exists else 1)
        if "builds submit" in joined:
            return done()
        raise AssertionError(f"unexpected command: {joined}")

    def gcloud(self, word):
        return [c for c in self.calls if c[0] == "gcloud" and word in c]


class FakePlatform:
    def __init__(self, templates=(), sandboxes=(), engines=None):
        self.templates = list(templates)
        self.sandboxes = list(sandboxes)
        self.engines = engines or {}
        self.created: list[tuple[str, dict]] = []
        self.deleted_templates: list[str] = []
        self.deleted_sandboxes: list[str] = []
        self.deleted_engines: list[tuple[str, bool]] = []

    def list_templates(self, engine):
        return list(self.templates)

    def get_template(self, name):
        return next(t for t in self.templates if t.name == name)

    def create_template(self, engine, config):
        self.created.append((engine, config))
        return f"{engine}/sandboxEnvironmentTemplates/new"

    def delete_template(self, name):
        self.deleted_templates.append(name)

    def list_sandboxes(self, engine):
        return list(self.sandboxes)

    def get_sandbox(self, name):
        return next(s for s in self.sandboxes if s.name == name)

    def delete_sandbox(self, name):
        self.deleted_sandboxes.append(name)

    def get_engine(self, name):
        return self.engines[name]

    def delete_engine(self, name, *, force):
        self.deleted_engines.append((name, force))


def tmpl(name="t1", image=IMAGE, state="STATE_ACTIVE", **kw):
    return infra.Template(
        name=f"{ENGINE}/sandboxEnvironmentTemplates/{name}",
        image_uri=image,
        state=state,
        **kw,
    )


def sbx(name, state="STATE_RUNNING", age_s=0):
    return infra.Sandbox(
        name=f"{ENGINE}/sandboxEnvironments/{name}",
        state=state,
        create_time=NOW - timedelta(seconds=age_s),
    )


def invoke(argv, *, platform=None, run=None, environ=None, repo_root=Path("/repo")):
    lines: list[str] = []
    run = run or FakeRun()
    env = {"GOOGLE_CLOUD_PROJECT": "proj", **(environ or {})}
    code = infra.main(
        argv,
        platform=platform,
        run=run,
        environ=env,
        repo_root=repo_root,
        now=NOW,
        out=lines.append,
    )
    return code, lines, run


# ---- image ------------------------------------------------------------------


def test_image_tag_is_the_sandbox_image_tree_id():
    run = FakeRun()
    assert infra.image_tag(Path("/repo"), run) == TAG
    assert ["git", "-C", "/repo", "rev-parse", "HEAD:sandbox_image"] in run.calls
    assert infra.image_uri(REPOSITORY, TAG) == IMAGE


def test_image_refuses_uncommitted_changes(capsys):
    code, _, run = invoke(["image"], run=FakeRun(dirty=True))
    assert code == 1
    assert "sandbox_image/ has uncommitted changes" in capsys.readouterr().err
    assert not run.gcloud("submit")


def test_image_skips_an_existing_tag():
    code, lines, run = invoke(["image"], run=FakeRun(image_exists=True))
    assert code == 0
    assert not run.gcloud("submit")
    assert lines[-1] == IMAGE


def test_image_builds_a_missing_tag():
    code, lines, run = invoke(["image"])
    assert code == 0
    (build,) = run.gcloud("submit")
    assert build[:3] == ["gcloud", "builds", "submit"]
    assert build[build.index("--tag") + 1] == IMAGE
    assert build[build.index("--project") + 1] == "proj"
    assert lines[-1] == IMAGE


def test_image_needs_the_project(capsys):
    code, _, _ = invoke(["image"], environ={"GOOGLE_CLOUD_PROJECT": ""})
    assert code == 2


# ---- template ---------------------------------------------------------------


def test_template_reuses_an_active_match():
    platform = FakePlatform([tmpl("old", image="other"), tmpl("good")])
    code, lines, _ = invoke(["template"], platform=platform)
    assert code == 0
    assert platform.created == []
    assert lines == [f"SANDBOX_TEMPLATE={ENGINE}/sandboxEnvironmentTemplates/good"]


def test_template_ignores_inactive_or_other_images():
    templates = [
        tmpl("a", state="STATE_DELETED"),
        tmpl("b", state="STATE_CREATING"),
        tmpl("c", image=f"{REPOSITORY}/sandbox:other"),
    ]
    assert infra.matching_template(templates, IMAGE) is None
    code, lines, _ = invoke(["template"], platform=FakePlatform(templates))
    assert code == 0
    assert lines == [f"SANDBOX_TEMPLATE={ENGINE}/sandboxEnvironmentTemplates/new"]


def test_template_requires_port_8080_only_when_ports_are_returned():
    assert infra.matching_template([tmpl(ports=[8080])], IMAGE)
    assert infra.matching_template([tmpl(ports=None)], IMAGE)
    assert infra.matching_template([tmpl(ports=[8080, 22])], IMAGE) is None
    assert infra.matching_template([tmpl(ports=[9000])], IMAGE) is None


def test_template_refuses_a_match_with_internet(capsys):
    platform = FakePlatform([tmpl(internet_access=True)])
    code, lines, _ = invoke(["template"], platform=platform)
    assert code == 1
    assert "matching template allows the internet: refusing" in capsys.readouterr().err
    assert platform.created == [] and lines == []
    # a returned False, or a field not returned, is fine
    assert infra.find_template(
        FakePlatform([tmpl(internet_access=False)]), ENGINE, IMAGE
    )


def test_template_creates_with_2_cpu_2gi_port_8080_no_internet():
    platform = FakePlatform()
    code, _, _ = invoke(["template"], platform=platform)
    assert code == 0
    ((engine, config),) = platform.created
    assert engine == ENGINE
    assert config == infra.template_config(IMAGE, f"issue-to-pr-sandbox-{TAG}")
    spec = config["custom_container_environment"]["custom_container_spec"]
    assert spec["image_uri"] == IMAGE
    assert spec["resources"] == {
        "limits": {"cpu": "2", "memory": "2Gi"},
        "requests": {"cpu": "2", "memory": "2Gi"},
    }
    assert config["custom_container_environment"]["ports"] == [
        {"port": 8080, "protocol": "TCP"}
    ]
    assert config["egress_control_config"] == {"internet_access": False}
    assert config["display_name"] == f"issue-to-pr-sandbox-{TAG}"


def test_template_config_does_not_share_the_module_constant():
    config = infra.template_config(IMAGE, "n")
    config["custom_container_environment"]["custom_container_spec"]["resources"][
        "limits"
    ]["cpu"] = "9"
    assert infra.TEMPLATE_RESOURCES["limits"]["cpu"] == "2"


def test_find_only_never_creates_and_fails_without_a_match(capsys):
    platform = FakePlatform([tmpl("x", image="other")])
    code, lines, _ = invoke(["template", "--find-only"], platform=platform)
    assert code == 1
    assert platform.created == [] and lines == []
    code, lines, _ = invoke(
        ["template", "--find-only"], platform=FakePlatform([tmpl("g")])
    )
    assert code == 0
    assert lines == [f"SANDBOX_TEMPLATE={ENGINE}/sandboxEnvironmentTemplates/g"]


# ---- engine configuration ---------------------------------------------------


def test_engine_comes_from_the_flag_then_the_environment_then_terraform():
    other = "projects/1/locations/europe-west4/reasoningEngines/9"
    engines = []

    class Spy(FakePlatform):
        def list_sandboxes(self, engine):
            engines.append(engine)
            return []

    invoke(
        ["sweep", "--engine", other], platform=Spy(), environ={"SANDBOX_ENGINE": ENGINE}
    )
    invoke(["sweep"], platform=Spy(), environ={"SANDBOX_ENGINE": ENGINE})
    invoke(["sweep"], platform=Spy())
    assert engines == [other, ENGINE, ENGINE]


@pytest.mark.parametrize("bad", ["456", "projects/p/reasoningEngines/1", ""])
def test_a_malformed_engine_name_exits_2(bad, capsys):
    argv = ["sweep", "--engine", bad] if bad else ["sweep"]
    environ = {"SANDBOX_ENGINE": "nonsense"} if not bad else None
    code, _, _ = invoke(argv, platform=FakePlatform(), environ=environ)
    assert code == 2
    assert "reasoningEngines/<id>" in capsys.readouterr().err


def test_terraform_failure_exits_2():
    code, _, _ = invoke(["sweep"], platform=FakePlatform(), run=FakeRun(tf_ok=False))
    assert code == 2


def test_terraform_outputs_unwraps_values():
    assert infra.terraform_outputs("x", FakeRun()) == TF_OUTPUTS


def test_unknown_subcommand_exits_2():
    assert infra.main(["nope"]) == 2


# ---- prune ------------------------------------------------------------------


def test_prune_keeps_the_current_image():
    platform = FakePlatform(
        [
            tmpl("cur"),
            tmpl("old", image="other"),
            tmpl("gone", image="o", state="STATE_DELETED"),
        ]
    )
    code, _, _ = invoke(["prune-templates"], platform=platform)
    assert code == 0
    assert platform.deleted_templates == [f"{ENGINE}/sandboxEnvironmentTemplates/old"]


def test_prune_all_deletes_every_template():
    platform = FakePlatform([tmpl("cur"), tmpl("old", image="other")])
    code, _, _ = invoke(["prune-templates", "--all"], platform=platform)
    assert code == 0
    assert len(platform.deleted_templates) == 2


# ---- sweep ------------------------------------------------------------------


def test_sweep_never_deletes_a_sandbox_younger_than_ttl_plus_grace():
    limit = 1800 + infra.SWEEP_GRACE_S
    platform = FakePlatform(
        sandboxes=[sbx("young", age_s=limit - 1), sbx("old", age_s=limit + 1)]
    )
    code, lines, _ = invoke(["sweep"], platform=platform)
    assert code == 0
    assert platform.deleted_sandboxes == [f"{ENGINE}/sandboxEnvironments/old"]
    assert lines[-1] == "swept 1"
    assert len(lines) == 3  # one line per sandbox, then the count


def test_sweep_uses_the_configured_ttl():
    platform = FakePlatform(sandboxes=[sbx("a", age_s=200)])
    invoke(["sweep"], platform=platform, environ={"SANDBOX_TTL_S": "100"})
    assert len(platform.deleted_sandboxes) == 1


def test_sweep_skips_deleted_and_deprovisioning():
    platform = FakePlatform(
        sandboxes=[
            sbx("a", state="STATE_DELETED", age_s=99999),
            sbx("b", state="STATE_DEPROVISIONING", age_s=99999),
            sbx("c", state="STATE_RUNNING", age_s=99999),
        ]
    )
    _, lines, _ = invoke(["sweep", "--all"], platform=platform)
    assert platform.deleted_sandboxes == [f"{ENGINE}/sandboxEnvironments/c"]
    assert any("STATE_RUNNING" in line for line in lines)  # each line shows the state
    assert any("STATE_DELETED" in line for line in lines)


def test_sweep_all_ignores_age():
    platform = FakePlatform(sandboxes=[sbx("fresh", age_s=1), sbx("old", age_s=99999)])
    invoke(["sweep", "--all"], platform=platform)
    assert len(platform.deleted_sandboxes) == 2


def test_expired_is_pure():
    boxes = [sbx("a", age_s=5000), sbx("b", age_s=10), sbx("c", "STATE_DELETED", 9999)]
    assert infra.expired(boxes, now=NOW, ttl_s=1800) == [boxes[0].name]


@pytest.mark.parametrize(
    "argv",
    [
        ["prune-templates"],
        ["prune-templates", "--all"],
        ["sweep"],
        ["sweep", "--all"],
        ["delete-engine", "--name", ENGINE, "--expect-display-name", "issue-to-pr"],
    ],
)
def test_dry_run_deletes_nothing(argv):
    platform = FakePlatform(
        templates=[tmpl("old", image="other")],
        sandboxes=[sbx("old", age_s=99999)],
        engines={ENGINE: infra.Engine(ENGINE, "issue-to-pr")},
    )
    code, lines, _ = invoke([*argv, "--dry-run"], platform=platform)
    assert code == 0
    assert platform.deleted_templates == []
    assert platform.deleted_sandboxes == []
    assert platform.deleted_engines == []
    assert any("would" in line for line in lines)


# ---- delete-engine ----------------------------------------------------------


def test_delete_engine_requires_the_exact_display_name(capsys):
    platform = FakePlatform(engines={ENGINE: infra.Engine(ENGINE, "issue-to-pr-x")})
    argv = ["delete-engine", "--name", ENGINE, "--expect-display-name", "issue-to-pr"]
    code, _, _ = invoke(argv, platform=platform)
    assert code == 1
    assert platform.deleted_engines == []
    assert "display name" in capsys.readouterr().err


def test_delete_engine_forces_children():
    platform = FakePlatform(engines={ENGINE: infra.Engine(ENGINE, "issue-to-pr")})
    argv = ["delete-engine", "--name", ENGINE, "--expect-display-name", "issue-to-pr"]
    code, _, _ = invoke(argv, platform=platform)
    assert code == 0
    assert platform.deleted_engines == [(ENGINE, True)]


def test_delete_engine_checks_the_name_form():
    argv = ["delete-engine", "--name", "short", "--expect-display-name", "x"]
    assert invoke(argv, platform=FakePlatform())[0] == 2


# ---- env --------------------------------------------------------------------


def test_env_prints_the_four_lines_without_secrets():
    platform = FakePlatform([tmpl("g")])
    code, lines, _ = invoke(["env"], platform=platform)
    assert code == 0
    assert lines == [
        f"SANDBOX_ENGINE={ENGINE}",
        f"SANDBOX_TEMPLATE={ENGINE}/sandboxEnvironmentTemplates/g",
        f"SANDBOX_CALLER_SA={CALLER}",
        "# ENVIRONMENT_BACKEND=agent_runtime",
    ]
    assert platform.created == []
    assert not any("token" in line.lower() for line in lines)


def test_env_fails_without_a_template():
    code, lines, _ = invoke(["env"], platform=FakePlatform())
    assert code == 1 and lines == []
