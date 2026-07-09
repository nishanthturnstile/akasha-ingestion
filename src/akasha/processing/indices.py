from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from akasha.processing.sentinel2 import Sentinel2OutputProfile, output_profile


@dataclass(frozen=True, slots=True)
class IndexOutputProfile:
    index_name: str
    formula_version: str
    dtype: str
    scale_factor: float | None
    nodata_value: int | float
    clip_min: float | None
    clip_max: float | None
    processing_resolution: int | float


OutputProfile = IndexOutputProfile | Sentinel2OutputProfile


def calculate_index(
    index_name: str,
    first: NDArray[np.floating],
    second: NDArray[np.floating],
    *,
    valid_mask: NDArray[np.bool_] | None = None,
) -> NDArray[np.float32]:
    normalized = index_name.lower()
    mask = _base_mask(first, second, valid_mask)
    output = np.full(first.shape, np.nan, dtype="float32")

    if normalized in {"ndvi", "ndmi", "ndbi", "ndre", "ndwi_green_nir"}:
        denominator = first + second
        formula_mask = mask & (np.abs(denominator) > 1e-6)
        output[formula_mask] = (first[formula_mask] - second[formula_mask]) / denominator[
            formula_mask
        ]
    elif normalized == "msavi":
        radicand = (2 * first + 1) ** 2 - 8 * (first - second)
        formula_mask = mask & (radicand >= 0)
        output[formula_mask] = (
            2 * first[formula_mask]
            + 1
            - np.sqrt(radicand[formula_mask], dtype="float32")
        ) / 2
    elif normalized == "reci":
        denominator_floor = 1e-4
        formula_mask = mask & (second > denominator_floor)
        output[formula_mask] = (first[formula_mask] / second[formula_mask]) - 1
    else:
        raise ValueError(f"unsupported index: {index_name}")

    return output.astype("float32")


def encode_index_output(
    index_name: str,
    values: NDArray[np.floating],
    *,
    profile: OutputProfile | None = None,
) -> tuple[NDArray[np.integer] | NDArray[np.floating], OutputProfile]:
    profile = profile or output_profile(index_name)
    valid = np.isfinite(values)
    if profile.clip_min is not None and profile.clip_max is not None:
        values = np.clip(values, profile.clip_min, profile.clip_max)
    if profile.scale_factor is None:
        encoded = np.full(values.shape, profile.nodata_value, dtype="float32")
        encoded[valid] = values[valid].astype("float32")
        return encoded, profile

    encoded = np.full(values.shape, int(profile.nodata_value), dtype="int16")
    encoded[valid] = np.rint(values[valid] * profile.scale_factor).astype("int16")
    return encoded, profile


def _base_mask(
    first: NDArray[np.floating],
    second: NDArray[np.floating],
    valid_mask: NDArray[np.bool_] | None,
) -> NDArray[np.bool_]:
    mask = np.isfinite(first) & np.isfinite(second)
    if valid_mask is not None:
        mask &= valid_mask
    return mask
