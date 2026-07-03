from __future__ import annotations

import numpy as np
from rasterio.transform import from_origin

from akasha.processing.cog import cog_metadata, validate_cog, write_cog_bytes
from akasha.processing.indices import calculate_index, encode_index_output
from akasha.processing.sentinel2 import reflectance_from_dn, scl_valid_mask


def test_reflectance_conversion_applies_scale_offset_once_and_masks_invalid_pixels() -> None:
    dn = np.array([[0, 1000], [2000, 3000]], dtype="uint16")
    valid_mask = np.array([[False, True], [True, True]])

    reflectance = reflectance_from_dn(dn, scale=0.0001, offset=-0.1, valid_mask=valid_mask)

    assert np.isnan(reflectance[0, 0])
    np.testing.assert_allclose(reflectance[0, 1], 0.0, atol=1e-6)
    np.testing.assert_allclose(reflectance[1, 0], 0.1, atol=1e-6)
    np.testing.assert_allclose(reflectance[1, 1], 0.2, atol=1e-6)


def test_scl_mask_accepts_only_phase2_valid_classes() -> None:
    scl = np.array([[0, 1, 2, 3], [4, 5, 6, 7], [8, 9, 10, 11]], dtype="uint8")

    mask = scl_valid_mask(scl)

    assert mask.tolist() == [
        [False, False, False, False],
        [True, True, True, False],
        [False, False, False, False],
    ]


def test_index_formula_and_int16_encoding_match_expected_ndvi() -> None:
    nir = np.array([[0.6, 0.2], [0.1, 0.5]], dtype="float32")
    red = np.array([[0.2, 0.2], [0.1, 0.0]], dtype="float32")
    valid_mask = np.array([[True, True], [True, True]])

    ndvi = calculate_index("ndvi", nir, red, valid_mask=valid_mask)
    encoded, profile = encode_index_output("ndvi", ndvi)

    np.testing.assert_allclose(ndvi[0, 0], 0.5, atol=1e-6)
    assert encoded.dtype == np.int16
    assert encoded[0, 0] == 5000
    assert encoded[1, 0] == 0
    assert profile.formula_version == "ndvi-s2-v1"


def test_reci_masks_small_denominator_and_uses_float32_output() -> None:
    nir08 = np.array([[0.6, 0.2]], dtype="float32")
    rededge1 = np.array([[0.3, 0.00001]], dtype="float32")

    reci = calculate_index("reci", nir08, rededge1)
    encoded, profile = encode_index_output("reci", reci)

    np.testing.assert_allclose(encoded[0, 0], 1.0, atol=1e-6)
    assert encoded[0, 1] == -9999.0
    assert encoded.dtype == np.float32
    assert profile.formula_version == "reci-s2-v1"


def test_write_cog_bytes_outputs_valid_cog(tmp_path) -> None:
    values = np.array([[100, 200], [300, -32768]], dtype="int16")

    payload = write_cog_bytes(
        values,
        transform=from_origin(77.0, 13.0, 10, 10),
        crs="EPSG:32643",
        nodata=-32768,
        tags={"akasha:formula_version": "ndvi-s2-v1"},
    )
    cog_path = tmp_path / "ndvi.cog.tif"
    cog_path.write_bytes(payload)

    is_valid, errors, _ = validate_cog(cog_path)

    assert is_valid is True
    assert errors == []


def test_cog_metadata_ignores_encoded_nodata_value() -> None:
    values = np.array([[100, 200], [300, -32768]], dtype="int16")

    metadata = cog_metadata(values, crs="EPSG:32643", resolution=10, nodata=-32768)

    assert metadata["min_value"] == 100
    assert metadata["max_value"] == 300
