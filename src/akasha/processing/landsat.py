from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from akasha.processing.indices import IndexOutputProfile
from akasha.providers.contracts import NormalizedAsset, NormalizedStacItem

LANDSAT_SOURCE_ID = "landsat-c2-l2"
LANDSAT_PROVIDER_COLLECTION = "landsat-c2-l2"
LANDSAT_PRIMARY_PROVIDER_ROUTE = "planetary-computer:landsat-c2-l2"
LANDSAT_PGSTAC_COLLECTION_ID = "akasha-landsat-c2-l2-derived-v1"
LANDSAT_PROCESSING_PROFILE_VERSION = "landsat-8-9-c2-l2-sr-qa-v1"
LANDSAT_MASK_PROFILE_VERSION = "landsat-c2-qa-mask-v1"

LANDSAT_REFLECTANCE_SCALE = 0.0000275
LANDSAT_REFLECTANCE_OFFSET = -0.2
LANDSAT_REFLECTANCE_NODATA = 0
LANDSAT_NATIVE_RESOLUTION_METERS = 30

LANDSAT_REFLECTANCE_ASSETS: tuple[str, ...] = (
    "blue",
    "green",
    "red",
    "nir08",
    "swir16",
    "swir22",
)
LANDSAT_QA_ASSETS: tuple[str, ...] = ("qa_pixel", "qa_radsat")
LANDSAT_REQUIRED_ASSETS = LANDSAT_REFLECTANCE_ASSETS + LANDSAT_QA_ASSETS

LANDSAT_ASSET_COMMON_NAMES: dict[str, str] = {
    "blue": "blue",
    "green": "green",
    "red": "red",
    "nir08": "nir08",
    "swir16": "swir16",
    "swir22": "swir22",
}

LANDSAT_INDEX_ASSETS: dict[str, tuple[str, str]] = {
    "ndvi": ("nir08", "red"),
    "msavi": ("nir08", "red"),
    "ndmi": ("nir08", "swir16"),
    "ndwi_green_nir": ("green", "nir08"),
}

LANDSAT_MASK_NODATA = 0
LANDSAT_MASK_VALID_LAND = 1
LANDSAT_MASK_CLOUD = 2
LANDSAT_MASK_SHADOW = 3
LANDSAT_MASK_WATER = 4
LANDSAT_MASK_SNOW = 5
LANDSAT_USABLE_MASK_CLASSES = (LANDSAT_MASK_VALID_LAND, LANDSAT_MASK_WATER)

_PLATFORM_PREFIX = {"landsat-8": "LC08", "landsat-9": "LC09"}
_PRODUCT_ID = re.compile(
    r"^(?P<prefix>LC0[89])_L2(?P<product>SP|SR)_"
    r"(?P<path>\d{3})(?P<row>\d{3})_(?P<date>\d{8})_02_T1$"
)
_SELECTED_OLI_SATURATION_BITS = sum(1 << bit for bit in range(1, 7))
_TERRAIN_OCCLUSION_BIT = 1 << 11


@dataclass(frozen=True, slots=True)
class LandsatSceneIdentity:
    product_id: str
    platform: str
    product_type: str
    wrs_path: str
    wrs_row: str
    acquisition_date: str
    collection_number: str = "02"
    collection_category: str = "T1"


def output_profile(index_name: str) -> IndexOutputProfile:
    normalized = index_name.lower()
    if normalized not in LANDSAT_INDEX_ASSETS:
        raise ValueError(f"unsupported Landsat index: {index_name}")
    return IndexOutputProfile(
        index_name=normalized,
        formula_version=f"{normalized}-landsat-c2-v1",
        dtype="int16",
        scale_factor=10000,
        nodata_value=-32768,
        clip_min=-1.0,
        clip_max=1.0,
        processing_resolution=LANDSAT_NATIVE_RESOLUTION_METERS,
    )


def validate_item(item: NormalizedStacItem) -> LandsatSceneIdentity:
    if item.source_id != LANDSAT_SOURCE_ID:
        raise ValueError(f"Landsat item requires source_id {LANDSAT_SOURCE_ID}")
    if item.provider_collection != LANDSAT_PROVIDER_COLLECTION:
        raise ValueError(
            f"Landsat item requires provider collection {LANDSAT_PROVIDER_COLLECTION}"
        )
    platform = str(item.platform or "").lower()
    expected_prefix = _PLATFORM_PREFIX.get(platform)
    if expected_prefix is None:
        raise ValueError(f"unsupported Landsat platform: {item.platform}")
    match = _PRODUCT_ID.fullmatch(item.stac_item_id)
    if match is None:
        raise ValueError("Landsat product ID must identify a Collection 2 Tier 1 L2SP/L2SR item")
    if match.group("prefix") != expected_prefix:
        raise ValueError("Landsat product ID conflicts with platform metadata")

    properties = _properties(item)
    collection_number = _normalized_collection_number(
        properties.get("landsat:collection_number")
    )
    if collection_number is not None and collection_number != "02":
        raise ValueError("Landsat item must use Collection 2")
    category = _optional_upper(properties.get("landsat:collection_category"))
    if category is not None and category != "T1":
        raise ValueError("Landsat product exposure requires collection category T1")
    correction = _optional_upper(
        properties.get("landsat:correction")
        or properties.get("landsat:processing_level")
    )
    expected_correction = f"L2{match.group('product')}"
    if correction is not None and correction != expected_correction:
        raise ValueError("Landsat product ID conflicts with correction-level metadata")

    missing = sorted(set(LANDSAT_REQUIRED_ASSETS) - set(item.assets))
    if missing:
        raise ValueError(f"Landsat item missing required assets: {', '.join(missing)}")
    for asset_key in LANDSAT_REFLECTANCE_ASSETS:
        _validate_reflectance_asset(asset_key, item.assets[asset_key])
    for asset_key in LANDSAT_QA_ASSETS:
        _validate_qa_asset(asset_key, item.assets[asset_key])

    return LandsatSceneIdentity(
        product_id=item.stac_item_id,
        platform=platform,
        product_type=expected_correction,
        wrs_path=match.group("path"),
        wrs_row=match.group("row"),
        acquisition_date=match.group("date"),
    )


def reflectance_from_dn(
    dn: NDArray[np.number],
    *,
    valid_mask: NDArray[np.bool_] | None = None,
) -> NDArray[np.float32]:
    values = dn.astype("float32")
    valid = np.isfinite(values) & (values != LANDSAT_REFLECTANCE_NODATA)
    if valid_mask is not None:
        if valid_mask.shape != values.shape:
            raise ValueError("Landsat reflectance valid mask shape must match the source band")
        valid &= valid_mask
    output = np.full(values.shape, np.nan, dtype="float32")
    output[valid] = (
        values[valid] * LANDSAT_REFLECTANCE_SCALE + LANDSAT_REFLECTANCE_OFFSET
    )
    return output


def decode_qa_mask(
    qa_pixel: NDArray[np.integer],
    qa_radsat: NDArray[np.integer],
    *,
    analytic_valid_mask: NDArray[np.bool_] | None = None,
) -> NDArray[np.uint8]:
    if qa_pixel.shape != qa_radsat.shape:
        raise ValueError("Landsat QA_PIXEL and QA_RADSAT grids must match")
    if analytic_valid_mask is not None and analytic_valid_mask.shape != qa_pixel.shape:
        raise ValueError("Landsat analytic validity mask must match the QA grid")

    pixel = qa_pixel.astype("uint16", copy=False)
    radsat = qa_radsat.astype("uint16", copy=False)
    output = np.full(pixel.shape, LANDSAT_MASK_VALID_LAND, dtype="uint8")

    water = (pixel & (1 << 7)) != 0
    snow = (pixel & (1 << 5)) != 0
    shadow = (pixel & (1 << 4)) != 0
    cloud = (pixel & ((1 << 1) | (1 << 2) | (1 << 3))) != 0
    invalid = (pixel & 1) != 0
    invalid |= (radsat & (_SELECTED_OLI_SATURATION_BITS | _TERRAIN_OCCLUSION_BIT)) != 0
    if analytic_valid_mask is not None:
        invalid |= ~analytic_valid_mask

    output[water] = LANDSAT_MASK_WATER
    output[snow] = LANDSAT_MASK_SNOW
    output[shadow] = LANDSAT_MASK_SHADOW
    output[cloud] = LANDSAT_MASK_CLOUD
    output[invalid] = LANDSAT_MASK_NODATA
    return output


def index_valid_mask(
    mask: NDArray[np.integer],
    first: NDArray[np.floating],
    second: NDArray[np.floating],
) -> NDArray[np.bool_]:
    if mask.shape != first.shape or first.shape != second.shape:
        raise ValueError("Landsat mask and index bands must share one grid")
    return (
        np.isin(mask, np.asarray(LANDSAT_USABLE_MASK_CLASSES, dtype=mask.dtype))
        & np.isfinite(first)
        & np.isfinite(second)
    )


def _validate_reflectance_asset(asset_key: str, asset: NormalizedAsset) -> None:
    if asset.band_common_name != LANDSAT_ASSET_COMMON_NAMES[asset_key]:
        raise ValueError(f"Landsat asset {asset_key} has invalid common-name metadata")
    if asset.scale is None or not np.isclose(asset.scale, LANDSAT_REFLECTANCE_SCALE):
        raise ValueError(f"Landsat asset {asset_key} has invalid reflectance scale")
    if not np.isclose(asset.offset, LANDSAT_REFLECTANCE_OFFSET):
        raise ValueError(f"Landsat asset {asset_key} has invalid reflectance offset")
    if asset.nodata != LANDSAT_REFLECTANCE_NODATA:
        raise ValueError(f"Landsat asset {asset_key} has invalid nodata value")
    if (
        asset.spatial_resolution is None
        or not np.isclose(asset.spatial_resolution, LANDSAT_NATIVE_RESOLUTION_METERS)
    ):
        raise ValueError(f"Landsat asset {asset_key} must declare 30 m resolution")
    _require_cog_media_type(asset_key, asset)


def _validate_qa_asset(asset_key: str, asset: NormalizedAsset) -> None:
    _require_cog_media_type(asset_key, asset)
    if (
        asset.spatial_resolution is None
        or not np.isclose(asset.spatial_resolution, LANDSAT_NATIVE_RESOLUTION_METERS)
    ):
        raise ValueError(f"Landsat asset {asset_key} must declare 30 m resolution")


def _require_cog_media_type(asset_key: str, asset: NormalizedAsset) -> None:
    media_type = (asset.media_type or "").lower()
    if "image/tiff" not in media_type or "cloud-optimized" not in media_type:
        raise ValueError(f"Landsat asset {asset_key} must be a Cloud Optimized GeoTIFF")


def _properties(item: NormalizedStacItem) -> dict[str, Any]:
    properties = item.raw_item.get("properties")
    return properties if isinstance(properties, dict) else {}


def _normalized_collection_number(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return f"{int(value):02d}"
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid Landsat collection-number metadata") from exc


def _optional_upper(value: Any) -> str | None:
    return str(value).upper() if value is not None and str(value) else None
