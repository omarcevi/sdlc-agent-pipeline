"""Process-wide map from sandbox id (stored in session state) to live Environment."""

from app.environment.base import Environment, InfraError

_ENVS: dict[str, Environment] = {}


def register(env: Environment) -> str:
    _ENVS[env.env_id] = env
    return env.env_id


def get(env_id: str) -> Environment:
    try:
        return _ENVS[env_id]
    except KeyError:
        raise InfraError(f"unknown or released sandbox: {env_id}") from None


async def release(env_id: str) -> None:
    env = _ENVS.pop(env_id, None)
    if env is not None:
        await env.close()


def clear() -> None:
    """Forget all environments without closing them. Tests only."""
    _ENVS.clear()
