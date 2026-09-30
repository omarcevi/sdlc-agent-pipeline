"""Model presets for benchmark runs."""

from app.models import RoleModels

FLASH = "gemini-3.8-flash"
PRO = "gemini-3.1-pro-preview"

PRESETS: dict[str, dict[str, str]] = {
    "flash": {"planner": FLASH, "coder": FLASH, "reviewer": FLASH},
    "pro": {"planner": PRO, "coder": PRO, "reviewer": PRO},
    "mixed": {"planner": PRO, "coder": FLASH, "reviewer": PRO},
}


def _preset(name: str) -> dict[str, str]:
    try:
        return PRESETS[name]
    except KeyError:
        valid = ", ".join(sorted(PRESETS))
        raise ValueError(f"unknown preset {name!r}; valid presets: {valid}") from None


def role_models(preset: str) -> RoleModels:
    return RoleModels.from_names(**_preset(preset))


def solo_model_name(preset: str) -> str:
    """The single model for a one-agent run; the mixed preset has no such model."""
    if preset == "mixed":
        raise ValueError(
            "the mixed preset needs roles; use flash or pro with --system single"
        )
    return _preset(preset)["coder"]
