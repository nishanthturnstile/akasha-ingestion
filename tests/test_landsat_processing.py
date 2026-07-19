from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import numpy as np
import pytest

from akasha.processing.landsat import (
    LANDSAT_MASK_CLOUD,
    LANDSAT_MASK_NODATA,
    LANDSAT_MASK_SHADOW,
    LANDSAT_MASK_SNOW,
    LANDSAT_MASK_VALID_LAND,
    LANDSAT_MASK_WATER,
    LANDSAT_PROCESSING_PROFILE_VERSION,
    LANDSAT_REQUIRED_ASSETS,
    decode_qa_mask,
    index_valid_mask,
    output_profile,
    reflectance_from_dn,
    validate_item,
)
from akasha.providers.contracts import NormalizedAsset, NormalizedStacItem


def test_validate_item_accepts_landsat_8_and_9_tier_1_products() -> None:
    landsat8 = validate_item(_item(platform="landsat-8"))
    landsat9 = validate_item(
        _item(
            platform="landsat-9",
            item_id="LC09_L2SR_143052_20260707_02_T1",
            correction="L2SR",
        )
    )

    assert landsat8.wrs_path == "143"
    assert landsat8.wrs_row == "052"
    assert landsat8.product_type == "L2SP"
    assert landsat9.platform == "landsat-9"
    assert landsat9.product_type == "L2SR"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"platform": "landsat-7"}, "unsupported Landsat platform"),
        ({"item_id": "LC09_L2SP_143052_20260707_02_T1"}, "conflicts with platform"),
        ({"item_id": "LC08_L2SP_143052_20260707_02_T2"}, "Tier 1"),
        ({"collection_number": "01"}, "Collection 2"),
        ({"category": "T2"}, "category T1"),
    ],
)
def test_validate_item_rejects_wrong_identity(change: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_item(_item(**change))


def test_validate_item_rejects_missing_or_invalid_reflectance_metadata() -> None:
    item = _item()
    item.assets.pop("swir22")
    with pytest.raises(ValueError, match="missing required assets: swir22"):
        validate_item(item)

    item = _item()
    item.assets["red"] = replace(item.assets["red"], offset=0.0)
    with pytest.raises(ValueError, match="invalid reflectance offset"):
        validate_item(item)


def test_reflectance_scaling_preserves_negative_values_and_excludes_zero() -> None:
    values = reflectance_from_dn(np.array([[0, 1, 7273, 18639]], dtype="uint16"))

    assert np.isnan(values[0, 0])
    assert values[0, 1] == pytest.approx(-0.1999725)
    assert values[0, 2] == pytest.approx(0.0000075, abs=1e-8)
    assert values[0, 3] == pytest.approx(0.3125725, abs=1e-7)


def test_decode_qa_mask_decodes_classes_and_priority() -> None:
    qa_pixel = np.array(
        [[0, 1 << 7, 1 << 5, 1 << 4, 1 << 3, (1 << 7) | (1 << 3), 1]],
        dtype="uint16",
    )
    qa_radsat = np.zeros_like(qa_pixel)

    result = decode_qa_mask(qa_pixel, qa_radsat)

    assert result.tolist() == [[
        LANDSAT_MASK_VALID_LAND,
        LANDSAT_MASK_WATER,
        LANDSAT_MASK_SNOW,
        LANDSAT_MASK_SHADOW,
        LANDSAT_MASK_CLOUD,
        LANDSAT_MASK_CLOUD,
        LANDSAT_MASK_NODATA,
    ]]


@pytest.mark.parametrize("bit", [1, 2, 3, 4, 5, 6, 11])
def test_decode_qa_mask_rejects_selected_band_saturation_and_occlusion(bit: int) -> None:
    result = decode_qa_mask(
        np.zeros((1, 1), dtype="uint16"),
        np.array([[1 << bit]], dtype="uint16"),
    )
    assert result[0, 0] == LANDSAT_MASK_NODATA


def test_decode_qa_mask_does_not_reject_unselected_band_1_saturation() -> None:
    result = decode_qa_mask(
        np.zeros((1, 1), dtype="uint16"),
        np.array([[1]], dtype="uint16"),
    )
    assert result[0, 0] == LANDSAT_MASK_VALID_LAND


def test_index_valid_mask_keeps_land_and_water_only() -> None:
    mask = np.array([[1, 4, 2, 3, 5, 0]], dtype="uint8")
    first = np.ones(mask.shape, dtype="float32")
    second = np.ones(mask.shape, dtype="float32")
    second[0, 1] = np.nan

    result = index_valid_mask(mask, first, second)

    assert result.tolist() == [[True, False, False, False, False, False]]


def test_output_profiles_are_source_versioned_and_30m() -> None:
    profile = output_profile("NDVI")

    assert profile.formula_version == "ndvi-landsat-c2-v1"
    assert profile.processing_resolution == 30
    assert LANDSAT_PROCESSING_PROFILE_VERSION == "landsat-8-9-c2-l2-sr-qa-v1"


def _item(
    *,
    platform: str = "landsat-8",
    item_id: str = "LC08_L2SP_143052_20260707_02_T1",
    correction: str = "L2SP",
    collection_number: str = "02",
    category: str = "T1",
) -> NormalizedStacItem:
    assets = {
        asset_key: _asset(asset_key)
        for asset_key in LANDSAT_REQUIRED_ASSETS
    }
    return NormalizedStacItem(
        provider_adapter="planetary-computer",
        provider_collection="landsat-c2-l2",
        source_id="landsat-c2-l2",
        stac_item_id=item_id,
        logical_scene_key=f"landsat-c2-l2:{item_id}",
        acquisition_at=datetime(2026, 7, 7, 5, 4, tzinfo=UTC),
        platform=platform,
        constellation="landsat",
        instrument="oli",
        mgrs_tile=None,
        footprint={"type": "Polygon", "coordinates": []},
        bbox=[77.0, 12.5, 78.0, 13.5],
        cloud_percent=10.0,
        assets=assets,
        raw_item={
            "properties": {
                "landsat:collection_number": collection_number,
                "landsat:collection_category": category,
                "landsat:correction": correction,
            }
        },
    )


def _asset(asset_key: str) -> NormalizedAsset:
    reflectance = asset_key not in {"qa_pixel", "qa_radsat"}
    return NormalizedAsset(
        asset_key=asset_key,
        href=f"https://landsat.test/{asset_key}.tif",
        media_type="image/tiff; application=geotiff; profile=cloud-optimized",
        roles=["data"],
        band_common_name=asset_key if reflectance else None,
        scale=0.0000275 if reflectance else None,
        offset=-0.2 if reflectance else 0.0,
        nodata=0 if reflectance else 1,
        spatial_resolution=30,
        storage_backend="https",
        selected_access_mode="signed_https",
    )
