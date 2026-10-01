"""Live-mode configuration read from the environment: which repositories the
pipeline may work on, and which GitHub logins may author and label its issues."""

import os

TRIGGER_LABEL = "agent-ok"


def _csv(name: str) -> frozenset[str]:
    raw = os.environ.get(name, "")
    return frozenset(item.strip().lower() for item in raw.split(",") if item.strip())


def live_repos() -> frozenset[str]:
    """LIVE_REPOS: comma-separated 'owner/name' (lowercase). Empty when unset."""
    return _csv("LIVE_REPOS")


def allowed_users() -> frozenset[str]:
    """LIVE_ALLOWED_USERS: comma-separated GitHub logins (lowercase). Empty when unset."""
    return _csv("LIVE_ALLOWED_USERS")
