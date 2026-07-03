from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from akasha.providers.contracts import NormalizedStacItem

SENTINEL2_REQUIRED_ASSETS: tuple[str, ...] = (
    "blue",
    "green",
    "red",
    "nir",
    "nir08",
    "rededge1",
    "swir16",
    "swir22",
    "scl",
)

SENTINEL2_INDEX_ASSETS: dict[str, tuple[str, ...]] = {
    "ndvi": ("nir", "red"),
    "msavi": ("nir", "red"),
    "ndmi": ("nir08", "swir16"),
    "ndbi": ("swir16", "nir08"),
    "ndre": ("nir08", "rededge1"),
    "reci": ("nir08", "rededge1"),
}

SENTINEL2_PROCESSING_RESOLUTION: dict[str, int] = {
    "ndvi": 10,
    "msavi": 10,
    "ndmi": 20,
    "ndbi": 20,
    "ndre": 20,
    "reci": 20,
}

SENTINEL2_FORMULA_VERSION: dict[str, str] = {
    "ndvi": "ndvi-s2-v1",
    "msavi": "msavi-s2-v1",
    "ndmi": "ndmi-s2-v1",
    "ndbi": "ndbi-s2-v1",
    "ndre": "ndre-s2-v1",
    "reci": "reci-s2-v1",
}


@dataclass(frozen=True, slots=True)
class Sentinel2OutputProfile:
    index_name: str
    formula_version: str
    dtype: str
    scale_factor: float | None
    nodata_value: int | float
    clip_min: float | None
    clip_max: float | None
    processing_resolution: int


def output_profile(index_name: str) -> Sentinel2OutputProfile:
    normalized = index_name.lower()
    if normalized == "reci":
        return Sentinel2OutputProfile(
            index_name=normalized,
            formula_version=SENTINEL2_FORMULA_VERSION[normalized],
            dtype="float32",
            scale_factor=None,
            nodata_value=-9999.0,
            clip_min=None,
            clip_max=None,
            processing_resolution=SENTINEL2_PROCESSING_RESOLUTION[normalized],
        )
    if normalized not in SENTINEL2_FORMULA_VERSION:
        raise ValueError(f"unsupported Sentinel-2 index: {index_name}")
    return Sentinel2OutputProfile(
        index_name=normalized,
        formula_version=SENTINEL2_FORMULA_VERSION[normalized],
        dtype="int16",
        scale_factor=10000,
        nodata_value=-32768,
        clip_min=-1.0,
        clip_max=1.0,
        processing_resolution=SENTINEL2_PROCESSING_RESOLUTION[normalized],
    )


def validate_required_assets(item: NormalizedStacItem) -> None:
    missing = sorted(set(SENTINEL2_REQUIRED_ASSETS) - set(item.assets))
    if missing:
        raise ValueError(f"Sentinel-2 item missing required assets: {', '.join(missing)}")
    invalid_scale_assets = [
        asset_key
        for asset_key, asset in item.assets.items()
        if asset_key != "scl" and asset_key in SENTINEL2_REQUIRED_ASSETS and asset.scale is None
    ]
    if invalid_scale_assets:
        raise ValueError(
            "Sentinel-2 reflectance assets missing raster scale: "
            + ", ".join(sorted(invalid_scale_assets))
        )


def reflectance_from_dn(
    dn: NDArray[np.number],
    *,
    scale: float,
    offset: float = 0.0,
    valid_mask: NDArray[np.bool_] | None = None,
) -> NDArray[np.float32]:
    values = dn.astype("float32")
    output = np.full(values.shape, np.nan, dtype="float32")
    mask = np.isfinite(values) if valid_mask is None else valid_mask & np.isfinite(values)
    output[mask] = values[mask] * scale + offset
    return output


def scl_valid_mask(scl: NDArray[np.integer]) -> NDArray[np.bool_]:
    valid_classes = np.isin(scl, np.array([4, 5, 6], dtype=scl.dtype))
    return valid_classes.astype(bool)

