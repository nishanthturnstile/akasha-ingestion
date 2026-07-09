from __future__ import annotations

import pytest

from akasha.config import Settings
from akasha.processing.resourcesat import (
    AWIFS_PROFILE,
    GREEN,
    LISS3_PROFILE,
    LISS4_PROFILE,
    NDMI,
    NDVI,
    NDWI_GREEN_NIR,
    NIR,
    RED,
    RESOURCESAT_AWIFS_BOA_SOURCE_ID,
    RESOURCESAT_LISS3_BOA_SOURCE_ID,
    RESOURCESAT_LISS4_MX70_L2_SOURCE_ID,
    RESOURCESAT_MASK_CLASS_BY_VALUE,
    RESOURCESAT_MASK_CLASSES,
    RESOURCESAT_MASK_METHOD,
    RESOURCESAT_VALID_MASK_CLASSES,
    SWIR1,
    profile_for_collection,
    profile_for_source,
)


def test_profile_lookup_maps_sources_and_collections() -> None:
    assert profile_for_source(RESOURCESAT_LISS3_BOA_SOURCE_ID) is LISS3_PROFILE
    assert profile_for_source(RESOURCESAT_LISS4_MX70_L2_SOURCE_ID) is LISS4_PROFILE
    assert profile_for_source(RESOURCESAT_AWIFS_BOA_SOURCE_ID) is AWIFS_PROFILE
    assert profile_for_collection(LISS3_PROFILE.collection_id) is LISS3_PROFILE

    with pytest.raises(ValueError, match="unsupported ResourceSat source"):
        profile_for_source("sentinel-2-l2a")
    with pytest.raises(ValueError, match="unsupported ResourceSat collection"):
        profile_for_collection("sentinel-2-l2a")


def test_profiles_pin_raw_band_order_and_role_mapping() -> None:
    assert LISS3_PROFILE.band_order == ("BAND2", "BAND3", "BAND4", "BAND5")
    assert dict(LISS3_PROFILE.band_roles) == {
        GREEN: "BAND2",
        RED: "BAND3",
        NIR: "BAND4",
        SWIR1: "BAND5",
    }
    assert LISS3_PROFILE.band_names_for_index(NDVI) == ("BAND4", "BAND3")
    assert LISS3_PROFILE.band_names_for_index(NDMI) == ("BAND4", "BAND5")
    assert LISS3_PROFILE.band_names_for_index(NDWI_GREEN_NIR) == ("BAND2", "BAND4")

    assert LISS4_PROFILE.band_order == ("BAND2", "BAND3", "BAND4")
    assert SWIR1 not in LISS4_PROFILE.band_roles
    assert LISS4_PROFILE.supported_indices == ("ndvi", "msavi", "ndwi_green_nir")


def test_supported_index_matrix_rejects_unvalidated_resourcesat_indices() -> None:
    for profile in (LISS3_PROFILE, LISS4_PROFILE, AWIFS_PROFILE):
        for unsupported in ("ndre", "reci", "ndbi", "ndwi", "savi", "gndvi"):
            with pytest.raises(ValueError, match="unsupported ResourceSat index"):
                profile.band_roles_for_index(unsupported)

    with pytest.raises(ValueError, match="unsupported ResourceSat index"):
        LISS4_PROFILE.band_roles_for_index(NDMI)


def test_processing_resolution_uses_source_specific_settings_override() -> None:
    settings = Settings(
        resourcesat_liss3_processing_resolution_m=24.0,
        resourcesat_liss4_processing_resolution_m=6.0,
        resourcesat_awifs_processing_resolution_m=60.0,
    )

    assert LISS3_PROFILE.native_resolution_m == 23.5
    assert LISS3_PROFILE.native_resolution_tolerance_m == 2.0
    assert LISS3_PROFILE.processing_resolution_m() == 23.5
    assert LISS3_PROFILE.processing_resolution_m(settings) == 24.0
    assert LISS4_PROFILE.processing_resolution_m(settings) == 6.0
    assert AWIFS_PROFILE.processing_resolution_m(settings) == 60.0


def test_mask_class_constants_match_akasha_threshold_mask_v1() -> None:
    assert RESOURCESAT_MASK_METHOD == "akasha-threshold-mask-v1"
    assert RESOURCESAT_VALID_MASK_CLASSES == (1, 4)
    assert [mask_class.value for mask_class in RESOURCESAT_MASK_CLASSES] == [0, 1, 2, 3, 4]
    assert RESOURCESAT_MASK_CLASS_BY_VALUE[0].label == "nodata"
    assert RESOURCESAT_MASK_CLASS_BY_VALUE[1].valid_for_analytics is True
    assert RESOURCESAT_MASK_CLASS_BY_VALUE[2].label == "cloud"
    assert RESOURCESAT_MASK_CLASS_BY_VALUE[3].label == "shadow"
    assert RESOURCESAT_MASK_CLASS_BY_VALUE[4].valid_for_analytics is True
