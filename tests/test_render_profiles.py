import pytest

from akasha.processing.render_profiles import CONTRAST_PALETTE_V1, resolve_render_profile


def test_contrast_uses_five_equal_breaks_and_six_configured_colours() -> None:
    descriptor = resolve_render_profile(
        index_name="ndvi", requested="contrast", scene_min=-0.5, scene_max=1.0
    )

    assert descriptor.applied == "contrast"
    assert descriptor.thresholds == pytest.approx((-0.2, 0.1, 0.4, 0.7, 1.0))
    assert descriptor.palette == CONTRAST_PALETTE_V1
    assert len(descriptor.palette) == 6
    assert descriptor.fallback_reason is None


def test_contrast_falls_back_without_changing_standard_values() -> None:
    missing = resolve_render_profile(
        index_name="ndmi", requested="contrast", scene_min=None, scene_max=None
    )
    constant = resolve_render_profile(
        index_name="ndvi", requested="contrast", scene_min=0.3, scene_max=0.3
    )
    unsupported = resolve_render_profile(
        index_name="custom", requested="contrast", scene_min=-1, scene_max=1
    )

    assert (missing.applied, missing.fallback_reason) == ("standard", "missing_statistics")
    assert (constant.applied, constant.fallback_reason) == ("standard", "constant_scene")
    assert (unsupported.applied, unsupported.fallback_reason) == (
        "standard",
        "unsupported_index",
    )
