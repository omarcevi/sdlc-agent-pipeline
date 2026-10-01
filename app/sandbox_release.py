"""Releases the sandbox of a run that is not driven by `app.driver`.

`app/driver.py` releases its sandbox in a `finally`. A run started by agents-cli
(`run`, `eval generate`, the playground) goes through the runner directly, so
nothing releases the sandbox but its TTL. This plugin does it at the end of the
run. The driver does not install it.

Only the sandbox the run itself started is released: the id the session state
holds when the run ends, provided it was not already there when the run began. The
state is the run's own session, so another run's sandbox cannot be named, and the
registry is keyed by that id.
"""

import logging
from typing import Any

from google.adk.plugins.base_plugin import BasePlugin

from app.environment import registry

logger = logging.getLogger(__name__)
MAX_TRACKED_RUNS = 1024


class SandboxReleasePlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(name="sandbox_release")
        # session id -> the sandbox id the session already held when the run began.
        self._inherited: dict[str, str | None] = {}

    async def before_run_callback(self, *, invocation_context: Any) -> None:
        session = invocation_context.session
        # A session that starts again replaces its own entry. Entries of runs whose
        # end was never seen are bounded too: the oldest go first, and a run that
        # lost its entry releases nothing (the sandbox's TTL cleans it up).
        self._inherited.pop(session.id, None)
        self._inherited[session.id] = session.state.get("sandbox_id")
        while len(self._inherited) > MAX_TRACKED_RUNS:
            del self._inherited[next(iter(self._inherited))]
        return None

    async def after_run_callback(self, *, invocation_context: Any) -> None:
        await self._release(invocation_context.session)

    async def on_run_error_callback(
        self, *, invocation_context: Any, error: Exception
    ) -> None:
        await self._release(invocation_context.session)

    async def _release(self, session: Any) -> None:
        if session.id not in self._inherited:
            return  # the run's start was not seen: nothing here is known to be ours
        inherited = self._inherited.pop(session.id)
        sandbox_id = session.state.get("sandbox_id")
        if not sandbox_id or sandbox_id == inherited:
            return
        # A failed release must not replace the run's real result. The sandbox
        # removes itself at its TTL.
        try:
            await registry.release(sandbox_id)
        except Exception as exc:
            logger.warning(
                "could not release sandbox %s: %s: %s",
                sandbox_id,
                type(exc).__name__,
                exc,
            )
