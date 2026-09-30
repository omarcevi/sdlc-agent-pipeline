"""Chooses the sandbox backend from ENVIRONMENT_BACKEND."""

import os

from app.environment.base import Environment, InfraError
from app.environment.docker import DockerEnvironment


async def start_environment() -> Environment:
    backend = os.environ.get("ENVIRONMENT_BACKEND", "docker")
    if backend == "docker":
        return await DockerEnvironment.start()
    raise InfraError(
        f"unsupported ENVIRONMENT_BACKEND={backend!r}; only 'docker' exists in week 1"
    )
