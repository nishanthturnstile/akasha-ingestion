from __future__ import annotations

from copy import deepcopy

from akasha.processing.sar_temporal import build_sar_temporal_analysis


def _metadata() -> dict:
    return {
        "complete": True,
        "policyVersion": "eos04-comparability-v1",
        "keyHash": "key",
        "platform": "EOS-04",
        "sensor": "SAR",
        "frequencyBand": "C",
        "instrumentMode": "MRS",
        "productType": "L2B-ARD-PRODUCT",
        "processingLevel": "STD",
        "processingProfileVersion": "v2",
        "providerSoftwareVersion": "1.2.00",
        "calibrationFormula": "formula",
        "outputScale": "db",
        "rtcApplied": True,
        "demCorrection": True,
        "demSource": "COPERNICUS30",
        "polarizations": ["HH", "HV"],
        "sensorOrientation": "RIGHT",
        "orbitState": "DESCENDING",
        "trackKey": "scene:22",
        "incidenceAngleDegrees": 37.8,
        "pixelSpacingMeters": 18.0,
    }


def _stats(hh: float, hv: float) -> dict:
    return {
        "coveragePercent": 100.0,
        "validPixelCount": 100,
        "fieldPixelCount": 100,
        "bands": [
            {"polarization": "HH", "median": hh},
            {"polarization": "HV", "median": hv},
        ],
        "features": {"HH_MINUS_HV_DB": hh - hv},
    }


def _observation(index: int, hh: float, hv: float) -> dict:
    return {
        "acquisitionDate": f"2026-0{index}-01",
        "comparisonMetadata": _metadata(),
        "stats": _stats(hh, hv),
        "quality": {"confidence": "high"},
    }


def test_returns_previous_pass_delta_with_insufficient_baseline() -> None:
    result = build_sar_temporal_analysis(
        current_metadata=_metadata(),
        current_stats=_stats(-8, -17),
        prior_observations=[_observation(6, -10, -18)],
        minimum_baseline_observations=5,
    )

    assert result["comparison"]["status"] == "INSUFFICIENT_BASELINE"
    assert result["change"]["referenceDate"] == "2026-06-01"
    assert result["change"]["bands"][0]["medianDeltaDb"] == 2.0
    assert result["baseline"]["priorObservationCount"] == 1


def test_robust_baseline_excludes_current_observation() -> None:
    history = [
        _observation(6, -10.0, -18.0),
        _observation(5, -10.5, -18.5),
        _observation(4, -9.5, -17.5),
        _observation(3, -11.0, -19.0),
        _observation(2, -9.0, -17.0),
    ]
    result = build_sar_temporal_analysis(
        current_metadata=_metadata(),
        current_stats=_stats(-8, -16),
        prior_observations=history,
        minimum_baseline_observations=5,
    )

    assert result["comparison"]["status"] == "AVAILABLE"
    hh = next(value for value in result["baseline"]["bands"] if value["polarization"] == "HH")
    assert hh["baselineMedian"] == -10.0
    assert hh["mad"] == 0.5
    assert hh["robustDeviation"] > 2.6


def test_degenerate_baseline_omits_score() -> None:
    result = build_sar_temporal_analysis(
        current_metadata=_metadata(),
        current_stats=_stats(-8, -16),
        prior_observations=[_observation(index, -10, -18) for index in range(2, 7)],
        minimum_baseline_observations=5,
    )

    assert result["comparison"]["status"] == "DEGENERATE_BASELINE"
    assert result["baseline"]["bands"][0]["robustDeviation"] is None


def test_excludes_non_comparable_observation() -> None:
    incompatible = _observation(6, -10, -18)
    incompatible["comparisonMetadata"] = deepcopy(_metadata())
    incompatible["comparisonMetadata"]["trackKey"] = "scene:23"

    result = build_sar_temporal_analysis(
        current_metadata=_metadata(),
        current_stats=_stats(-8, -16),
        prior_observations=[incompatible],
        minimum_baseline_observations=5,
    )

    assert result["comparison"]["status"] == "NO_COMPARABLE_HISTORY"
    assert result["comparison"]["exclusions"][0]["reasonCodes"] == ["TRACK_MISMATCH"]
    assert result["history"] == []
