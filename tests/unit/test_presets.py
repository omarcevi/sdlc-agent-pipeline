import pytest

from app.models import ResponseToolGemini, RoleModels
from bench.presets import FLASH, PRESETS, PRO, role_models, solo_model_name


@pytest.mark.parametrize("name", sorted(PRESETS))
def test_each_preset_resolves_to_expected_models(name):
    models = role_models(name)
    for role, expected in PRESETS[name].items():
        model = getattr(models, role)
        assert isinstance(model, ResponseToolGemini)
        assert model.model == expected


def test_mixed_preset_roles():
    models = role_models("mixed")
    assert (models.planner.model, models.coder.model, models.reviewer.model) == (
        PRO,
        FLASH,
        PRO,
    )


def test_unknown_preset_lists_valid_names():
    with pytest.raises(ValueError) as exc:
        role_models("nope")
    for name in PRESETS:
        assert name in str(exc.value)


def test_solo_model_name():
    assert solo_model_name("pro") == PRO
    assert solo_model_name("flash") == FLASH
    with pytest.raises(ValueError, match="mixed preset needs roles"):
        solo_model_name("mixed")
    with pytest.raises(ValueError):
        solo_model_name("nope")


def test_role_models_from_names():
    models = RoleModels.from_names(FLASH, PRO, FLASH)
    assert (models.planner.model, models.coder.model, models.reviewer.model) == (
        FLASH,
        PRO,
        FLASH,
    )
