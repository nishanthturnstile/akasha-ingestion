from __future__ import annotations

from copy import deepcopy

from akasha.processing.sar_comparability import (
    comparison_reason_codes,
    normalize_eos04_comparison_metadata,
)


def _real_metadata() -> dict[str, str]:
    return {
        "ProductType": "L2B-ARD-PRODUCT",
        "SatID": "EOS-04",
        "Sensor": "SAR",
        "ImagingMode": "MRS",
        "ProcessingLevel": "STD",
        "SOFTWARE_VERSION": "1.2.00",
        "RTC_Apply_Flag": "1",
        "DEMCorrection": "YES",
        "DEMSource": "COPERNICUS30",
        "NoOfPolarizations": "2",
        "TxRxPol1": "HH",
        "TxRxPol2": "HV",
        "SensorOrientation": "RIGHT",
        "PassType": "NA",
        "SatelliteHeadingAngle": "190.5807715",
        "ImagingDirection": "F",
        "SceneNumber": "22",
        "Path": "0",
        "Row": "0",
        "StripNumber": "32457",
        "Cycle_Number": "91",
        "ImagingOrbitNo": "23887",
        "IncidenceAngle": "37.88928",
        "OutputPixelSpacing": "18.00",
        "SceneStartTime": "13-JUN-2026 00:40:56.047000000",
        "SceneEndTime": "13-JUN-2026 00:41:18.677000000",
    }


def _normalized(metadata: dict[str, str] | None = None) -> dict:
    return normalize_eos04_comparison_metadata(
        metadata or _real_metadata(),
        polarizations=("HH", "HV"),
        processing_profile_version="eos04-sar-mrs-l2b-gamma0-v2",
        calibration_formula="formula-v2",
        output_scale="db",
        resolution_meters=18.0,
    )


def test_normalizes_real_eos04_metadata_and_derives_orbit_state() -> None:
    result = _normalized()

    assert result["complete"] is True
    assert result["trackKey"] == "scene:22"
    assert result["orbitState"] == "DESCENDING"
    assert result["orbitStateSource"] == "satellite_heading"
    assert result["incidenceAngleDegrees"] == 37.88928
    assert result["acquisitionStart"] == "2026-06-13T00:40:56.047000Z"
    assert len(result["keyHash"]) == 64


def test_sentinel_provider_values_fail_closed() -> None:
    metadata = _real_metadata()
    metadata.update(
        {
            "SceneNumber": "0",
            "PassType": "NA",
            "SatelliteHeadingAngle": "-9999",
        }
    )

    result = _normalized(metadata)

    assert result["complete"] is False
    assert "trackKey" in result["missing"]
    assert "orbitState" in result["missing"]
    assert comparison_reason_codes(_normalized(), result) == ["METADATA_INCOMPLETE"]


def test_real_repeat_track_is_comparable_despite_absolute_orbit_and_strip_change() -> None:
    current = _normalized()
    candidate = deepcopy(current)
    candidate.update(
        {
            "absoluteOrbitNumber": 24401,
            "cycleNumber": 93,
            "stripNumber": 505,
            "incidenceAngleDegrees": 37.86202,
        }
    )

    assert comparison_reason_codes(current, candidate) == []


def test_returns_typed_comparison_mismatches() -> None:
    current = _normalized()
    candidate = deepcopy(current)
    candidate.update(
        {
            "providerSoftwareVersion": "2.0.00",
            "polarizations": ["VV", "VH"],
            "orbitState": "ASCENDING",
            "trackKey": "scene:23",
            "incidenceAngleDegrees": 39.0,
            "pixelSpacingMeters": 20.0,
        }
    )

    assert comparison_reason_codes(current, candidate) == [
        "PROCESSING_SOFTWARE_MISMATCH",
        "POLARIZATION_MISMATCH",
        "ORBIT_STATE_MISMATCH",
        "TRACK_MISMATCH",
        "INCIDENCE_ANGLE_MISMATCH",
        "RESOLUTION_MISMATCH",
    ]
