"""Backend selection and the configuration check (spec §4.2): ENVIRONMENT_BACKEND
picks the sandbox backend, and a bad setting of the cloud backend is a configuration
error before any run starts (exit 2 at the entry points), never an infra failure."""

import importlib
import io

import pytest

from app.driver import run_pipeline
from app.environment.agent_runtime import AgentRuntimeEnvironment
from app.environment.base import InfraError
from app.environment.docker import DockerEnvironment
from app.environment.factory import (
    BACKENDS,
    check_environment_config,
    start_environment,
)
from app.models import RoleModels
from app.nodes import intake
from app.pipeline import build_workflow
from app.schemas import RunRequest
from tests.fakes import FakeEnvironment, FakeLlm
from tests.unit.sandbox_fakes import CLOUD_ENV, ENGINE, TEMPLATE

# The fixed messages, spelled out: the tests pin the exact text.
MISSING = "{name} must be set for ENVIRONMENT_BACKEND=agent_runtime"
ENGINE_FORM = (
    "SANDBOX_ENGINE must look like projects/<p>/locations/<l>/reasoningEngines/<id>"
)
TEMPLATE_FORM = "SANDBOX_TEMPLATE must be a template under SANDBOX_ENGINE"
CALLER_FORM = (
    "SANDBOX_CALLER_SA must be a service account email ending in "
    ".iam.gserviceaccount.com"
)
READY = "SANDBOX_READY_TIMEOUT_S must be a positive number below RUN_TIMEOUT_S"


def unsupported(value: str) -> str:
    return f"unsupported ENVIRONMENT_BACKEND={value}; use docker or agent_runtime"


def cloud(**changes: str | None) -> dict[str, str]:
    """A complete agent_runtime configuration; a change of None removes the name."""
    environ = {"ENVIRONMENT_BACKEND": "agent_runtime", **CLOUD_ENV, **changes}
    return {name: value for name, value in environ.items() if value is not None}


def refusal(environ: dict[str, str] | None = None) -> str:
    with pytest.raises(ValueError) as refused:
        check_environment_config(environ)
    return str(refused.value)


def test_backends_are_docker_and_agent_runtime():
    assert BACKENDS == ("docker", "agent_runtime")


def test_docker_needs_nothing_new(monkeypatch):
    junk = {
        "SANDBOX_ENGINE": "nonsense",
        "SANDBOX_TEMPLATE": "nonsense",
        "SANDBOX_CALLER_SA": "nobody",
        "SANDBOX_READY_TIMEOUT_S": "-5",
        "RUN_TIMEOUT_S": "abc",  # the driver's own check, not this one
    }
    assert check_environment_config({}) is None
    assert check_environment_config({"ENVIRONMENT_BACKEND": "docker"}) is None
    assert check_environment_config(junk) is None
    assert check_environment_config({**junk, "ENVIRONMENT_BACKEND": "docker"}) is None
    # Without a mapping it reads the process environment.
    monkeypatch.delenv("ENVIRONMENT_BACKEND", raising=False)
    for name, value in junk.items():
        monkeypatch.setenv(name, value)
    assert check_environment_config() is None


@pytest.mark.parametrize(
    "value", ["kubernetes", "", "Docker", "agent-runtime", " docker", "docker "]
)
def test_unknown_backend_is_refused(monkeypatch, value):
    assert refusal({"ENVIRONMENT_BACKEND": value}) == unsupported(value)
    # A complete cloud configuration changes nothing about an unknown name.
    assert refusal(cloud(ENVIRONMENT_BACKEND=value)) == unsupported(value)
    monkeypatch.setenv("ENVIRONMENT_BACKEND", value)
    assert refusal() == unsupported(value)


def test_a_complete_cloud_configuration_passes(monkeypatch):
    assert check_environment_config(cloud()) is None
    for name, value in cloud().items():
        monkeypatch.setenv(name, value)
    assert check_environment_config() is None


@pytest.mark.parametrize(
    "name",
    ["SANDBOX_ENGINE", "SANDBOX_TEMPLATE", "SANDBOX_CALLER_SA", "GOOGLE_CLOUD_PROJECT"],
)
def test_agent_runtime_requires_engine_template_caller_and_project(name):
    assert refusal(cloud(**{name: None})) == MISSING.format(name=name)
    assert refusal(cloud(**{name: ""})) == MISSING.format(name=name)
    assert refusal(cloud(**{name: "  "})) == MISSING.format(name=name)


def test_missing_names_are_reported_in_a_fixed_order():
    assert refusal({"ENVIRONMENT_BACKEND": "agent_runtime"}) == MISSING.format(
        name="SANDBOX_ENGINE"
    )
    assert refusal(
        cloud(SANDBOX_TEMPLATE=None, SANDBOX_CALLER_SA=None, GOOGLE_CLOUD_PROJECT=None)
    ) == MISSING.format(name="SANDBOX_TEMPLATE")


@pytest.mark.parametrize(
    "engine",
    [
        "4242",
        "reasoningEngines/4242",
        "locations/us-central1/reasoningEngines/4242",
        "projects/p/locations/us-central1/reasoningEngines",
        "projects/p/locations/us-central1/reasoningEngines/",
        "projects/p/locations/us-central1/reasoningEngines/42/extra",
        "projects/p/locations/us-central1/agents/42",
        "projects//locations/us-central1/reasoningEngines/42",
        "https://x/projects/p/locations/us-central1/reasoningEngines/42",
    ],
)
def test_engine_must_have_the_engine_form(engine):
    template = f"{engine}/sandboxEnvironmentTemplates/77"
    assert refusal(cloud(SANDBOX_ENGINE=engine, SANDBOX_TEMPLATE=template)) == (
        ENGINE_FORM
    )


@pytest.mark.parametrize(
    "template",
    [
        "77",
        ENGINE,
        f"{ENGINE}/sandboxEnvironmentTemplates",
        f"{ENGINE}/sandboxEnvironmentTemplates/",
        f"{ENGINE}/sandboxEnvironmentTemplates/77/extra",
        f"{ENGINE}/templates/77",
        f"{ENGINE}0/sandboxEnvironmentTemplates/77",
        TEMPLATE.replace("reasoningEngines/4242", "reasoningEngines/4243"),
        f"{TEMPLATE} ",
    ],
)
def test_template_must_lie_under_the_engine(template):
    assert refusal(cloud(SANDBOX_TEMPLATE=template)) == TEMPLATE_FORM


@pytest.mark.parametrize(
    "caller",
    [
        "sandbox-caller",
        "sandbox-caller@demo-project",
        "someone@gmail.com",
        "@demo-project.iam.gserviceaccount.com",
        "sandbox-caller@demo-project.iam.gserviceaccount.com.example",
        "sandbox caller@demo-project.iam.gserviceaccount.com",
    ],
)
def test_caller_must_be_a_service_account_email(caller):
    assert refusal(cloud(SANDBOX_CALLER_SA=caller)) == CALLER_FORM


def test_ready_timeout_must_be_positive_and_below_run_timeout(monkeypatch):
    for value in ("0", "-1", "abc", "", "nan", "inf", "-inf"):
        assert refusal(cloud(SANDBOX_READY_TIMEOUT_S=value)) == READY, value
    # Against RUN_TIMEOUT_S's default, 1500 s.
    assert refusal(cloud(SANDBOX_READY_TIMEOUT_S="1500")) == READY
    assert check_environment_config(cloud(SANDBOX_READY_TIMEOUT_S="1499.5")) is None
    assert check_environment_config(cloud()) is None  # the default, 240 s
    # Against the RUN_TIMEOUT_S actually set, not the default.
    short = cloud(RUN_TIMEOUT_S="600")
    assert refusal({**short, "SANDBOX_READY_TIMEOUT_S": "600"}) == READY
    assert check_environment_config({**short, "SANDBOX_READY_TIMEOUT_S": "599"}) is None
    for name, setting in cloud(SANDBOX_READY_TIMEOUT_S="400").items():
        monkeypatch.setenv(name, setting)
    monkeypatch.setenv("RUN_TIMEOUT_S", "300")
    assert refusal() == READY


def test_a_bad_run_timeout_is_reported_as_itself_on_the_cloud_backend():
    assert "RUN_TIMEOUT_S must be a positive number" in refusal(
        cloud(RUN_TIMEOUT_S="abc")
    )
    assert "must stay below SANDBOX_TTL_S" in refusal(
        cloud(RUN_TIMEOUT_S="1900", SANDBOX_TTL_S="1800")
    )


async def test_factory_starts_the_chosen_backend(monkeypatch):
    started: list[str] = []

    async def docker_start():
        started.append("docker")
        return "docker sandbox"

    async def cloud_start():
        started.append("agent_runtime")
        return "cloud sandbox"

    monkeypatch.setattr(DockerEnvironment, "start", docker_start)
    monkeypatch.setattr(AgentRuntimeEnvironment, "start", cloud_start)

    monkeypatch.delenv("ENVIRONMENT_BACKEND", raising=False)
    assert await start_environment() == "docker sandbox"
    monkeypatch.setenv("ENVIRONMENT_BACKEND", "docker")
    assert await start_environment() == "docker sandbox"
    monkeypatch.setenv("ENVIRONMENT_BACKEND", "agent_runtime")
    assert await start_environment() == "cloud sandbox"
    assert started == ["docker", "docker", "agent_runtime"]

    monkeypatch.setenv("ENVIRONMENT_BACKEND", "kubernetes")
    with pytest.raises(InfraError) as refused:
        await start_environment()
    assert str(refused.value) == unsupported("kubernetes")
    assert started == ["docker", "docker", "agent_runtime"]


async def test_the_driver_checks_the_environment_config_before_the_run(
    bench, monkeypatch
):
    for name, value in cloud(SANDBOX_CALLER_SA="nobody").items():
        monkeypatch.setenv(name, value)
    started = []

    async def fake_start():
        started.append(1)
        return FakeEnvironment()

    monkeypatch.setattr(intake, "start_environment", fake_start)
    models = RoleModels(planner=FakeLlm([]), coder=FakeLlm([]), reviewer=FakeLlm([]))
    with pytest.raises(ValueError) as refused:
        await run_pipeline(
            RunRequest(task_id="t-1", run_id="r-1"), workflow=build_workflow(models)
        )
    assert str(refused.value) == CALLER_FORM
    assert started == []
    assert not (bench / "runs" / "r-1").exists()


# --- the entry points ---------------------------------------------------------

LIVE_ARGS = ["--repo", "owner/name", "--issue", "1", "--approver", "owner"]
ENTRY_POINTS = {
    "bench.run": ("bench.run", ["--tasks", "tc-001"]),
    "bench.probes validate": ("bench.probes", ["validate"]),
    "bench.review_probe": ("bench.review_probe", []),
    "app.live": ("app.live", LIVE_ARGS),
}
BAD_CONFIGS = {
    "unknown backend": (
        {"ENVIRONMENT_BACKEND": "kubernetes"},
        unsupported("kubernetes"),
    ),
    "no engine": (cloud(SANDBOX_ENGINE=None), MISSING.format(name="SANDBOX_ENGINE")),
    "slow readiness": (cloud(SANDBOX_READY_TIMEOUT_S="1500"), READY),
}


@pytest.fixture
def nothing_may_run(monkeypatch) -> list[str]:
    """Every way an entry point could start a sandbox, validate or run, recorded."""
    calls: list[str] = []

    def forbid(name: str):
        async def forbidden(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} ran before the configuration was checked")

        return forbidden

    def forbid_sync(name: str):
        def forbidden(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} ran before the configuration was checked")

        return forbidden

    monkeypatch.setattr(DockerEnvironment, "start", forbid("docker start"))
    monkeypatch.setattr(AgentRuntimeEnvironment, "start", forbid("cloud start"))
    for module in ("bench.run", "bench.review_probe"):
        monkeypatch.setattr(f"{module}.run_matrix", forbid(f"{module} run_matrix"))
    for module in ("bench.matrix", "app.live"):
        monkeypatch.setattr(f"{module}.run_pipeline", forbid(f"{module} run_pipeline"))
    monkeypatch.setattr("bench.run.validate_task", forbid_sync("validate_task"))
    for module in ("bench.probes", "bench.review_probe"):
        monkeypatch.setattr(
            f"{module}.validate_probe", forbid_sync(f"{module} validate_probe")
        )
    return calls


@pytest.mark.parametrize("config", sorted(BAD_CONFIGS))
@pytest.mark.parametrize("entry_point", sorted(ENTRY_POINTS))
def test_entry_points_check_the_environment_config(
    entry_point, config, nothing_may_run, monkeypatch, tmp_path, capsys
):
    module_name, argv = ENTRY_POINTS[entry_point]
    settings, message = BAD_CONFIGS[config]
    module = importlib.import_module(module_name)
    loaded: list[bool] = []

    def load_dotenv():
        # The bad settings come from .env: the check must run after it is loaded.
        loaded.append(True)
        for name, value in settings.items():
            monkeypatch.setenv(name, value)

    monkeypatch.setattr(module, "load_dotenv", load_dotenv)
    monkeypatch.chdir(tmp_path)  # anything written would land here
    if module_name == "app.live":
        code = module.main(argv, stdin=io.StringIO(), stdout=io.StringIO())
    else:
        code = module.main(argv)
    out, err = capsys.readouterr()
    assert code == 2
    assert err == f"error: {message}\n"
    assert out == ""
    assert loaded == [True]
    assert nothing_may_run == []
    assert list(tmp_path.iterdir()) == []
