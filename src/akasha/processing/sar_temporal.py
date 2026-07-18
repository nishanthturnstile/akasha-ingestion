from __future__ import annotations

from statistics import median
from typing import Any

from akasha.processing.sar_comparability import comparison_reason_codes

_MAD_EPSILON = 1e-6
_ROBUST_Z_SCALE = 0.67448975


def build_sar_temporal_analysis(
    *,
    current_metadata: dict[str, Any],
    current_stats: dict[str, Any],
    prior_observations: list[dict[str, Any]],
    minimum_baseline_observations: int,
) -> dict[str, Any]:
    comparable: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for observation in prior_observations:
        reasons = comparison_reason_codes(
            current_metadata,
            dict(observation.get("comparisonMetadata") or {}),
        )
        if reasons:
            exclusions.append(
                {
                    "acquisitionDate": observation["acquisitionDate"],
                    "reasonCodes": reasons,
                }
            )
        else:
            comparable.append(observation)

    change = _change(current_stats, comparable[0]) if comparable else {"status": "UNAVAILABLE"}
    baseline = _baseline(
        current_stats,
        comparable,
        minimum_observations=minimum_baseline_observations,
    )
    if not current_metadata.get("complete"):
        comparison_status = "METADATA_INCOMPLETE"
    elif not comparable:
        comparison_status = "NO_COMPARABLE_HISTORY"
    elif baseline["status"] == "INSUFFICIENT_OBSERVATIONS":
        comparison_status = "INSUFFICIENT_BASELINE"
    elif baseline["status"] == "DEGENERATE_BASELINE":
        comparison_status = "DEGENERATE_BASELINE"
    else:
        comparison_status = "AVAILABLE"
    return {
        "comparison": {
            "status": comparison_status,
            "policyVersion": current_metadata.get("policyVersion"),
            "currentKeyHash": current_metadata.get("keyHash"),
            "previousComparableDate": (
                comparable[0]["acquisitionDate"] if comparable else None
            ),
            "comparableObservationCount": 1 + len(comparable),
            "excludedObservationCount": len(exclusions),
            "exclusions": exclusions,
        },
        "history": [_public_observation(value) for value in comparable],
        "change": change,
        "baseline": baseline,
    }


def _change(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    prior_stats = dict(previous["stats"])
    current_bands = _bands_by_name(current)
    previous_bands = _bands_by_name(prior_stats)
    bands = []
    for polarization in current_bands.keys() & previous_bands.keys():
        current_median = current_bands[polarization].get("median")
        prior_median = previous_bands[polarization].get("median")
        if current_median is None or prior_median is None:
            continue
        bands.append(
            {
                "polarization": polarization,
                "currentMedianDb": float(current_median),
                "referenceMedianDb": float(prior_median),
                "medianDeltaDb": float(current_median) - float(prior_median),
            }
        )
    feature_deltas = {}
    for name in current.get("features", {}).keys() & prior_stats.get("features", {}).keys():
        feature_deltas[f"{name}_DELTA"] = float(current["features"][name]) - float(
            prior_stats["features"][name]
        )
    return {
        "status": "AVAILABLE" if bands or feature_deltas else "UNAVAILABLE",
        "referenceDate": previous["acquisitionDate"],
        "bands": sorted(bands, key=lambda value: value["polarization"]),
        "features": feature_deltas,
    }


def _baseline(
    current: dict[str, Any],
    observations: list[dict[str, Any]],
    *,
    minimum_observations: int,
) -> dict[str, Any]:
    base = {
        "requiredPriorObservations": minimum_observations,
        "priorObservationCount": len(observations),
        "windowStart": observations[-1]["acquisitionDate"] if observations else None,
        "windowEnd": observations[0]["acquisitionDate"] if observations else None,
        "bands": [],
        "features": {},
    }
    if len(observations) < minimum_observations:
        return {"status": "INSUFFICIENT_OBSERVATIONS", **base}

    has_score = False
    current_bands = _bands_by_name(current)
    for polarization, current_band in sorted(current_bands.items()):
        values = [
            _bands_by_name(dict(observation["stats"])).get(polarization, {}).get("median")
            for observation in observations
        ]
        numeric = [float(value) for value in values if value is not None]
        current_value = current_band.get("median")
        if len(numeric) < minimum_observations or current_value is None:
            continue
        result = _robust_deviation(float(current_value), numeric)
        has_score = has_score or result["robustDeviation"] is not None
        base["bands"].append({"polarization": polarization, **result})

    for name, current_value in sorted(current.get("features", {}).items()):
        values = [
            observation["stats"].get("features", {}).get(name)
            for observation in observations
        ]
        numeric = [float(value) for value in values if value is not None]
        if len(numeric) < minimum_observations:
            continue
        result = _robust_deviation(float(current_value), numeric)
        has_score = has_score or result["robustDeviation"] is not None
        base["features"][name] = result
    status = "AVAILABLE" if has_score else "DEGENERATE_BASELINE"
    return {"status": status, **base}


def _robust_deviation(current: float, baseline_values: list[float]) -> dict[str, float | None]:
    center = float(median(baseline_values))
    spread = float(median([abs(value - center) for value in baseline_values]))
    score = None if spread <= _MAD_EPSILON else _ROBUST_Z_SCALE * (current - center) / spread
    return {
        "currentValue": current,
        "baselineMedian": center,
        "mad": spread,
        "robustDeviation": score,
    }


def _bands_by_name(stats: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(value["polarization"]): dict(value) for value in stats.get("bands", [])}


def _public_observation(observation: dict[str, Any]) -> dict[str, Any]:
    stats = dict(observation["stats"])
    return {
        "acquisitionDate": observation["acquisitionDate"],
        "coveragePercent": stats["coveragePercent"],
        "validPixelCount": stats["validPixelCount"],
        "fieldPixelCount": stats["fieldPixelCount"],
        "bands": stats["bands"],
        "features": stats.get("features", {}),
        "quality": observation.get("quality", {}),
        "comparableToCurrent": True,
    }
