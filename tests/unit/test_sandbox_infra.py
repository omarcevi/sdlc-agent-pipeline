"""scripts/sandbox_infra.py against a fake platform and a recording fake for subprocess
calls. No GCP, no network, no SDK import."""

import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

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


def tmpl(name="t1", image=IMAGE, state="ACTIVE", **kw):
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
        tmpl("a", state="DELETED"),
        tmpl("b", state="PROVISIONING"),
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
    env = config["custom_container_environment"]
    assert env["custom_container_spec"] == {"image_uri": IMAGE}
    assert env["resources"] == {
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
    config["custom_container_environment"]["resources"]["limits"]["cpu"] = "9"
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


def test_template_config_validates_against_the_sdk_model():
    """Key paths must match the SDK's own config model (extra fields are forbidden)."""
    common = pytest.importorskip("agentplatform._genai.types.common")
    config = infra.template_config(IMAGE, "n")
    body = {k: v for k, v in config.items() if k != "display_name"}
    parsed = common.CreateSandboxEnvironmentTemplateConfig.model_validate(body)
    env = parsed.custom_container_environment
    assert env.custom_container_spec.image_uri == IMAGE
    assert env.resources.limits == {"cpu": "2", "memory": "2Gi"}
    assert env.resources.requests == {"cpu": "2", "memory": "2Gi"}
    assert [p.port for p in env.ports] == [8080]
    assert parsed.egress_control_config.internet_access is False


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
            tmpl("gone", image="o", state="DELETED"),
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


def test_prune_refuses_without_a_template_for_the_current_image(capsys):
    platform = FakePlatform([tmpl("old", image="other")])
    code, _, _ = invoke(["prune-templates"], platform=platform)
    assert code == 1
    assert platform.deleted_templates == []
    assert "no active template for the current image" in capsys.readouterr().err
    # --all is the teardown path and needs no current template
    code, _, _ = invoke(["prune-templates", "--all"], platform=platform)
    assert code == 0 and len(platform.deleted_templates) == 1


def test_prune_never_deletes_the_template_named_in_sandbox_template():
    in_use = f"{ENGINE}/sandboxEnvironmentTemplates/inuse"
    platform = FakePlatform(
        [tmpl("cur"), tmpl("inuse", image="other"), tmpl("x", image="o")]
    )
    code, _, _ = invoke(
        ["prune-templates"], platform=platform, environ={"SANDBOX_TEMPLATE": in_use}
    )
    assert code == 0
    assert platform.deleted_templates == [f"{ENGINE}/sandboxEnvironmentTemplates/x"]


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


def test_default_sweep_skips_deleted_and_deprovisioning_and_counts_nothing():
    platform = FakePlatform(
        sandboxes=[
            sbx("a", state="STATE_DELETED", age_s=99999),
            sbx("b", state="STATE_DEPROVISIONING", age_s=99999),
        ]
    )
    _, lines, _ = invoke(["sweep"], platform=platform)
    assert platform.deleted_sandboxes == []
    assert lines[-1] == "swept 0"


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
        templates=[tmpl("cur"), tmpl("old", image="other")],
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


# ---- the SDK adapter, fed objects shaped like the SDK's ----------------------


class SdkState(str, Enum):  # noqa: UP042 - str() must carry the class prefix, as the SDK's
    STATE_RUNNING = "STATE_RUNNING"
    STATE_DELETED = "STATE_DELETED"


def sdk_template(name="t", image=IMAGE, state="ACTIVE", ports=(8080,), internet=False):
    return SimpleNamespace(
        name=f"{ENGINE}/sandboxEnvironmentTemplates/{name}",
        state=state,
        custom_container_environment=SimpleNamespace(
            custom_container_spec=SimpleNamespace(image_uri=image),
            ports=[SimpleNamespace(port=p, protocol="TCP") for p in ports],
        ),
        egress_control_config=SimpleNamespace(internet_access=internet),
    )


def sdk_sandbox(name, state=SdkState.STATE_RUNNING, age_s=0):
    return SimpleNamespace(
        name=f"{ENGINE}/sandboxEnvironments/{name}",
        state=state,
        create_time=NOW - timedelta(seconds=age_s),
        connection_info=SimpleNamespace(
            load_balancer_hostname="lb.example", routing_token="rt"
        ),
    )


class FakeClient:
    def __init__(self, templates=(), sandboxes=()):
        self.calls = []
        client = self

        class Templates:
            def list(self, *, name):
                client.calls.append(("templates.list", name))
                return [SimpleNamespace(name=t.name) for t in templates]

            def get(self, *, name):
                return next(t for t in templates if t.name == name)

            def create(self, **kw):
                client.calls.append(("templates.create", kw))
                return SimpleNamespace(response=SimpleNamespace(name="new"))

            def delete(self, *, name):
                client.calls.append(("templates.delete", name))

        class Sandboxes:
            templates = Templates()

            def list(self, *, name):
                return list(sandboxes)

            def get(self, *, name):
                return next(s for s in sandboxes if s.name == name)

            def delete(self, *, name):
                client.calls.append(("sandboxes.delete", name))

        class Engines:
            sandboxes = Sandboxes()

            def get(self, *, name):
                res = SimpleNamespace(name=name, display_name="issue-to-pr")
                return SimpleNamespace(api_resource=res)

            def delete(self, *, name, force):
                client.calls.append(("engines.delete", name, force))

        self.agent_engines = Engines()


def test_sdk_platform_maps_sdk_shaped_templates():
    client = FakeClient(
        [sdk_template("a"), sdk_template("b", state="DELETED", ports=())]
    )
    platform = infra.SdkPlatform("p", "l", client=client)
    a, b = platform.list_templates(ENGINE)
    assert (a.state, a.image_uri, a.ports, a.internet_access) == (
        "ACTIVE",
        IMAGE,
        [8080],
        False,
    )
    assert b.state == "DELETED" and b.ports is None
    assert infra.matching_template([a, b], IMAGE) == a.name


def test_sdk_platform_normalises_enum_sandbox_states_and_skips_dead_ones():
    client = FakeClient(
        sandboxes=[
            sdk_sandbox("live", age_s=99999),
            sdk_sandbox("dead", state=SdkState.STATE_DELETED, age_s=99999),
        ]
    )
    platform = infra.SdkPlatform("p", "l", client=client)
    live, dead = platform.list_sandboxes(ENGINE)
    assert (live.state, dead.state) == ("STATE_RUNNING", "STATE_DELETED")
    assert live.load_balancer_hostname == "lb.example" and live.routing_token == "rt"
    assert "rt" not in repr(live)
    code, lines, _ = invoke(["sweep"], platform=platform)
    assert code == 0
    assert client.calls == [("sandboxes.delete", live.name)]
    assert lines[-1] == "swept 1"


def test_sdk_platform_prune_skips_deleted_templates():
    client = FakeClient(
        [
            sdk_template("cur"),
            sdk_template("old", image="other"),
            sdk_template("gone", image="other", state="DELETED"),
        ]
    )
    platform = infra.SdkPlatform("p", "l", client=client)
    code, _, _ = invoke(["prune-templates"], platform=platform)
    assert code == 0
    assert [c for c in client.calls if c[0] == "templates.delete"] == [
        ("templates.delete", f"{ENGINE}/sandboxEnvironmentTemplates/old")
    ]


def test_sdk_platform_create_template_passes_name_display_name_and_config():
    client = FakeClient()
    platform = infra.SdkPlatform("p", "l", client=client)
    config = infra.template_config(IMAGE, "disp")
    assert platform.create_template(ENGINE, config) == "new"
    ((_, kw),) = [c for c in client.calls if c[0] == "templates.create"]
    assert kw["name"] == ENGINE and kw["display_name"] == "disp"
    assert "display_name" not in kw["config"]
    assert kw["config"]["egress_control_config"] == {"internet_access": False}


def test_sdk_platform_engine_and_forced_delete():
    client = FakeClient()
    platform = infra.SdkPlatform("p", "l", client=client)
    assert platform.get_engine(ENGINE) == infra.Engine(ENGINE, "issue-to-pr")
    platform.delete_engine(ENGINE, force=True)
    assert ("engines.delete", ENGINE, True) in client.calls


def test_a_sandbox_without_create_time_is_never_expired():
    box = infra.Sandbox("n", "STATE_RUNNING", None)
    assert infra.expired([box], now=NOW, ttl_s=1) == []


def test_unexpected_errors_exit_with_a_one_line_message(capsys):
    class Boom(FakePlatform):
        def list_sandboxes(self, engine):
            raise RuntimeError("secret detail")

    code, _, _ = invoke(["sweep"], platform=Boom())
    err = capsys.readouterr().err
    assert code == 1 and "RuntimeError" in err and "secret" not in err


def test_a_missing_binary_exits_2(capsys):
    def run(cmd, **kw):
        raise FileNotFoundError(2, "no such file", cmd[0])

    code, _, _ = invoke(["sweep"], platform=FakePlatform(), run=run)
    assert code == 2 and "terraform" in capsys.readouterr().err
