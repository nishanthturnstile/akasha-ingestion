from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RenderProfileName = Literal["standard", "contrast"]

CONTRAST_PALETTE_V1: tuple[str, ...] = (
    "#6e3b1f",
    "#b86b2c",
    "#e7c64b",
    "#9bcf53",
    "#3f9f4a",
    "#0b5d37",
)

CONTRAST_ELIGIBLE_INDICES = frozenset(
    {"ndvi", "ndmi", "ndwi_green_nir", "msavi", "ndbi", "ndre", "reci"}
)


@dataclass(frozen=True, slots=True)
class ResolvedRenderProfile:
    requested: RenderProfileName
    applied: RenderProfileName
    version: str
    thresholds: tuple[float, ...]
    palette: tuple[str, ...]
    fallback_reason: str | None = None


def resolve_render_profile(
    *,
    index_name: str,
    requested: RenderProfileName,
    scene_min: float | None,
    scene_max: float | None,
) -> ResolvedRenderProfile:
    normalized = index_name.lower()
    if requested == "standard":
        return ResolvedRenderProfile("standard", "standard", "standard-v1", (), ())
    if normalized not in CONTRAST_ELIGIBLE_INDICES:
        return ResolvedRenderProfile(
            "contrast", "standard", "standard-v1", (), (), "unsupported_index"
        )
    if scene_min is None or scene_max is None:
        return ResolvedRenderProfile(
            "contrast", "standard", "standard-v1", (), (), "missing_statistics"
        )
    if scene_min == scene_max:
        return ResolvedRenderProfile(
            "contrast", "standard", "standard-v1", (), (), "constant_scene"
        )
    span = scene_max - scene_min
    thresholds = tuple(scene_min + n * span / 5 for n in range(1, 6))
    return ResolvedRenderProfile(
        "contrast",
        "contrast",
        "equal-bands-v1",
        thresholds,
        CONTRAST_PALETTE_V1,
    )
