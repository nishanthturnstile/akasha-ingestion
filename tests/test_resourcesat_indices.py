from __future__ import annotations

import numpy as np
import pytest

from akasha.config import Settings
from akasha.processing.indices import encode_index_output
from akasha.processing.resourcesat import (
    GREEN,
    LISS3_PROFILE,
    LISS4_PROFILE,
    NDMI,
    NDVI,
    NDWI_GREEN_NIR,
    NIR,
    RED,
    SWIR1,
    calculate_resourcesat_index,
    reflectance_from_dn,
    resourcesat_output_profile,
    resourcesat_valid_mask,
)


def test_resourcesat_reflectance_conversion_uses_zero_offset_and_masks_invalid_pixels() -> None:
    dn = np.array([[0, 1000], [2000, 3000]], dtype="uint16")
    valid_mask = np.array([[False, True], [True, True]])

    reflectance = reflectance_from_dn(dn, valid_mask)

    assert np.isnan(reflectance[0, 0])
    np.testing.assert_allclose(reflectance[0, 1], 0.1, atol=1e-6)
    np.testing.assert_allclose(reflectance[1, 0], 0.2, atol=1e-6)
    np.testing.assert_allclose(reflectance[1, 1], 0.3, atol=1e-6)


def test_resourcesat_valid_mask_keeps_valid_and_water_only() -> None:
    mask = np.array([[0, 1, 2], [3, 4, 255]], dtype="uint8")

    valid = resourcesat_valid_mask(mask)

    assert valid.tolist() == [[False, True, False], [False, True, False]]


def test_resourcesat_profile_drives_index_formulas_and_band_orientation() -> None:
    bands = {
        GREEN: np.array([[0.1, 0.0]], dtype="float32"),
        RED: np.array([[0.2, 0.0]], dtype="float32"),
        NIR: np.array([[0.6, 0.0]], dtype="float32"),
        SWIR1: np.array([[0.3, 0.0]], dtype="float32"),
    }
    valid_mask = np.array([[True, True]])

    ndvi = calculate_resourcesat_index(LISS3_PROFILE, NDVI, bands, valid_mask=valid_mask)
    msavi = calculate_resourcesat_index(LISS3_PROFILE, "msavi", bands, valid_mask=valid_mask)
    ndmi = calculate_resourcesat_index(LISS3_PROFILE, NDMI, bands, valid_mask=valid_mask)
    ndwi = calculate_resourcesat_index(
        LISS3_PROFILE,
        NDWI_GREEN_NIR,
        bands,
        valid_mask=valid_mask,
    )

    np.testing.assert_allclose(ndvi[0, 0], 0.5, atol=1e-6)
    expected_msavi = (2 * 0.6 + 1 - np.sqrt((2 * 0.6 + 1) ** 2 - 8 * (0.6 - 0.2))) / 2
    np.testing.assert_allclose(msavi[0, 0], expected_msavi, atol=1e-6)
    np.testing.assert_allclose(ndmi[0, 0], (0.6 - 0.3) / (0.6 + 0.3), atol=1e-6)
    np.testing.assert_allclose(ndwi[0, 0], (0.1 - 0.6) / (0.1 + 0.6), atol=1e-6)
    assert ndwi[0, 0] < 0
    assert np.isnan(ndvi[0, 1])
    assert np.isnan(ndwi[0, 1])


def test_resourcesat_unsupported_indices_raise_before_formula_dispatch() -> None:
    bands = {
        GREEN: np.array([[0.1]], dtype="float32"),
        RED: np.array([[0.2]], dtype="float32"),
        NIR: np.array([[0.6]], dtype="float32"),
        SWIR1: np.array([[0.3]], dtype="float32"),
    }

    with pytest.raises(ValueError, match="unsupported ResourceSat index"):
        calculate_resourcesat_index(LISS4_PROFILE, NDMI, bands)
    with pytest.raises(ValueError, match="unsupported ResourceSat index"):
        calculate_resourcesat_index(LISS3_PROFILE, "ndre", bands)
    with pytest.raises(ValueError, match="unsupported ResourceSat index"):
        calculate_resourcesat_index(LISS3_PROFILE, "reci", bands)


def test_resourcesat_output_profile_encodes_with_resourcesat_provenance() -> None:
    settings = Settings(resourcesat_liss3_processing_resolution_m=24.0)
    values = np.array([[0.5, np.nan, -1.2, 1.2]], dtype="float32")
    profile = resourcesat_output_profile(LISS3_PROFILE, NDWI_GREEN_NIR, settings=settings)

    encoded, resolved_profile = encode_index_output(
        NDWI_GREEN_NIR,
        values,
        profile=profile,
    )

    assert resolved_profile is profile
    assert profile.formula_version == "ndwi-green-nir-default-v1"
    assert "s2" not in profile.formula_version
    assert profile.processing_resolution == 24.0
    assert encoded.dtype == np.int16
    assert encoded.tolist() == [[5000, -32768, -10000, 10000]]


def test_default_ndwi_green_nir_encoding_profile_is_registered() -> None:
    values = np.array([[0.25]], dtype="float32")

    encoded, profile = encode_index_output(NDWI_GREEN_NIR, values)

    assert encoded[0, 0] == 2500
    assert profile.index_name == NDWI_GREEN_NIR
    assert profile.processing_resolution == 10
