"""Checks shared by the Docker-only and the cloud sandbox tests."""

import asyncio
import re

CREDENTIAL_NAME = re.compile(r"TOKEN|SECRET|KEY|CREDENTIAL|PASSWORD|GOOGLE_")
# Set by the python base image: the public fingerprint of the key that signs CPython
# releases. It is not a credential; any other value under this name is a leak.
IMAGE_PUBLIC_VALUES = {"GPG_KEY": "7169605F62C751356D054A26A821E680E5FA6305"}
_VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def credential_like_variables(env_output: str) -> list[str]:
    """Names (never values) of the variables in `env` output whose name looks like a
    credential, except an image variable that still holds its public value."""
    found = []
    for line in env_output.splitlines():
        name, _, value = line.partition("=")
        if not _VARIABLE.fullmatch(name):
            continue  # the continuation of a multi-line value
        if CREDENTIAL_NAME.search(name) and IMAGE_PUBLIC_VALUES.get(name) != value:
            found.append(name)
    return sorted(found)


TEMPLATE_LEFT_BEHIND = (
    "a test template may be left behind; run scripts/sandbox_infra.py prune-templates"
)


SECOND_PORT_NOT_ROUTED = (
    "platform did not route to a second port; the header question stays open"
)


def second_port_failure(status_code: int | None, body: object) -> str:
    """Why the echo server never answered on the second port, from one answer of
    the shim's `GET /processes/{id}/status` (None: no answer): a process that is
    still running means the platform did not route the port; anything else means
    the spawn failed. Fixed text and numbers only."""
    if status_code is None:
        return "spawn failed: its status check got no answer"
    if status_code != 200 or not isinstance(body, dict):
        return f"spawn failed: its status answered HTTP {status_code}"
    if body.get("is_running") is True:
        return SECOND_PORT_NOT_ROUTED
    exit_code = body.get("exit_code")
    shown = exit_code if isinstance(exit_code, int) else "unknown"
    return f"spawn failed: the echo server is not running (exit code {shown})"


def is_not_found(error: Exception) -> bool:
    return getattr(error, "code", None) == 404 or "NOT_FOUND" in str(
        getattr(error, "status", "")
    )


async def delete_template_with_retry(
    platform,
    template: str,
    *,
    interval_s: float = 10,
    timeout_s: float = 300,
    sleep=asyncio.sleep,
) -> None:
    """Delete a test template, retrying while the platform refuses (a sandbox that
    is still being deleted holds it). Stops on success or not-found. After
    `timeout_s` it raises with a fixed message and no resource name."""
    waited = 0.0
    while True:
        try:
            await asyncio.to_thread(platform.delete_template, template)
            return
        except Exception as error:
            if is_not_found(error):
                return
        if waited >= timeout_s:
            raise AssertionError(TEMPLATE_LEFT_BEHIND)
        await sleep(interval_s)
        waited += interval_s
