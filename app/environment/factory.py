"""Chooses the sandbox backend from ENVIRONMENT_BACKEND and checks its settings.

`docker` (the default) runs a local container; `agent_runtime` runs an Agent
Runtime sandbox (spec §4). `check_environment_config()` is called by every entry
point before any run starts, so a bad setting is a configuration error (exit 2),
never an infra failure that the matrix would run again.
"""

import os
from collections.abc import Mapping

from app.environment.agent_runtime import (
    READY_TIMEOUT_ERROR,
    AgentRuntimeEnvironment,
    SandboxSettings,
)
from app.environment.base import Environment, InfraError
from app.environment.docker import DockerEnvironment

BACKENDS = ("docker", "agent_runtime")
DEFAULT_BACKEND = "docker"
UNSUPPORTED_BACKEND = (
    "unsupported ENVIRONMENT_BACKEND={value}; use docker or agent_runtime"
)


def environment_backend(environ: Mapping[str, str] | None = None) -> str:
    """The configured backend name, as set (default `docker`). Not checked here."""
    environ = os.environ if environ is None else environ
    return environ.get("ENVIRONMENT_BACKEND", DEFAULT_BACKEND)


def check_environment_config(environ: Mapping[str, str] | None = None) -> None:
    """Raise ValueError, with a fixed message, when the chosen backend cannot run.

    `docker` needs nothing beyond what its own start checks. `agent_runtime` needs
    every setting `SandboxSettings.from_env` checks, and its readiness bound must be
    below the run's wall-clock cap, RUN_TIMEOUT_S, as it is set."""
    environ = os.environ if environ is None else environ
    backend = environment_backend(environ)
    if backend not in BACKENDS:
        raise ValueError(UNSUPPORTED_BACKEND.format(value=backend))
    if backend == "docker":
        return
    settings = SandboxSettings.from_env(environ)
    # Imported here: app.driver imports the pipeline, whose intake imports this module.
    from app.driver import run_timeout_s

    if settings.ready_timeout_s >= run_timeout_s(environ):
        raise ValueError(READY_TIMEOUT_ERROR)


async def start_environment() -> Environment:
    backend = environment_backend()
    if backend == "docker":
        return await DockerEnvironment.start()
    if backend == "agent_runtime":
        return await AgentRuntimeEnvironment.start()
    # The entry points refuse an unknown name first; a run without them (agents-cli)
    # fails here, as an infra error.
    raise InfraError(UNSUPPORTED_BACKEND.format(value=backend))
