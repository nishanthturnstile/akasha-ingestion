from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from json import dumps
from math import isfinite
from typing import Any

EOS04_COMPARABILITY_POLICY_VERSION = "eos04-comparability-v1"
EOS04_INCIDENCE_TOLERANCE_DEGREES = 1.0
EOS04_RESOLUTION_TOLERANCE_METERS = 0.5

_MISSING_TEXT = {"", "NA", "N/A", "NONE", "NULL", "UNKNOWN", "-9999"}
_RAW_COMPARISON_KEYS = (
    "ProductType",
    "SatID",
    "Sensor",
    "ImagingMode",
    "ProcessingLevel",
    "SOFTWARE_VERSION",
    "RTC_Apply_Flag",
    "DEMCorrection",
    "DEMSource",
    "DEMSource_Grid",
    "NoOfPolarizations",
    "TxRxPol1",
    "TxRxPol2",
    "TxRxPol3",
    "TxRxPol4",
    "SensorOrientation",
    "PassType",
    "SatelliteHeadingAngle",
    "ImageTrace_HeadingAngle",
    "ImagingDirection",
    "SceneNumber",
    "Path",
    "Row",
    "StripNumber",
    "Cycle_Number",
    "ImagingOrbitNo",
    "IncidenceAngle",
    "OutputPixelSpacing",
    "OutputLineSpacing",
    "SceneStartTime",
    "SceneCenterTime",
    "SceneEndTime",
)


def normalize_eos04_comparison_metadata(
    metadata: dict[str, str],
    *,
    polarizations: tuple[str, ...],
    processing_profile_version: str,
    calibration_formula: str,
    output_scale: str,
    resolution_meters: float,
) -> dict[str, Any]:
    """Normalize the bounded EOS-04 metadata used for temporal comparison."""

    pass_type = _text(metadata.get("PassType"))
    heading = _number(metadata.get("SatelliteHeadingAngle"))
    orbit_state, orbit_state_source = _orbit_state(pass_type, heading)
    scene_number = _positive_integer(metadata.get("SceneNumber"))
    track_key = f"scene:{scene_number}" if scene_number is not None else None
    start = _provider_datetime(metadata.get("SceneStartTime"))
    end = _provider_datetime(metadata.get("SceneEndTime"))
    incidence = _number(metadata.get("IncidenceAngle"))
    spacing = _number(metadata.get("OutputPixelSpacing")) or resolution_meters
    rtc_applied = _flag(metadata.get("RTC_Apply_Flag"))
    dem_correction = _yes_no(metadata.get("DEMCorrection"))
    dem_source = _text(metadata.get("DEMSource_Grid")) or _text(metadata.get("DEMSource"))

    normalized: dict[str, Any] = {
        "policyVersion": EOS04_COMPARABILITY_POLICY_VERSION,
        "platform": _text(metadata.get("SatID")),
        "sensor": _text(metadata.get("Sensor")),
        "frequencyBand": "C",
        "instrumentMode": _text(metadata.get("ImagingMode")),
        "productType": _text(metadata.get("ProductType")),
        "processingLevel": _text(metadata.get("ProcessingLevel")),
        "processingProfileVersion": processing_profile_version,
        "providerSoftwareVersion": _text(metadata.get("SOFTWARE_VERSION")),
        "calibrationFormula": calibration_formula,
        "outputScale": output_scale,
        "rtcApplied": rtc_applied,
        "demCorrection": dem_correction,
        "demSource": dem_source,
        "polarizations": list(polarizations),
        "sensorOrientation": _text(metadata.get("SensorOrientation")),
        "orbitState": orbit_state,
        "orbitStateSource": orbit_state_source,
        "trackKey": track_key,
        "sceneNumber": scene_number,
        "incidenceAngleDegrees": incidence,
        "pixelSpacingMeters": spacing,
        "acquisitionStart": start,
        "acquisitionEnd": end,
        "absoluteOrbitNumber": _positive_integer(metadata.get("ImagingOrbitNo")),
        "cycleNumber": _positive_integer(metadata.get("Cycle_Number")),
        "stripNumber": _positive_integer(metadata.get("StripNumber")),
        "rawComparisonMetadata": {
            key: str(metadata[key])[:256]
            for key in _RAW_COMPARISON_KEYS
            if key in metadata and len(str(metadata[key])) <= 256
        },
    }
    required = (
        "platform",
        "sensor",
        "instrumentMode",
        "productType",
        "processingProfileVersion",
        "providerSoftwareVersion",
        "calibrationFormula",
        "outputScale",
        "rtcApplied",
        "demCorrection",
        "demSource",
        "polarizations",
        "sensorOrientation",
        "orbitState",
        "trackKey",
        "incidenceAngleDegrees",
        "pixelSpacingMeters",
        "acquisitionStart",
        "acquisitionEnd",
    )
    missing = [key for key in required if normalized.get(key) is None or normalized.get(key) == []]
    normalized["complete"] = not missing
    normalized["missing"] = missing
    normalized["keyHash"] = comparison_key_hash(normalized)
    return normalized


def comparison_reason_codes(
    current: dict[str, Any],
    candidate: dict[str, Any],
    *,
    incidence_tolerance_degrees: float = EOS04_INCIDENCE_TOLERANCE_DEGREES,
    resolution_tolerance_meters: float = EOS04_RESOLUTION_TOLERANCE_METERS,
) -> list[str]:
    if not current.get("complete") or not candidate.get("complete"):
        return ["METADATA_INCOMPLETE"]

    reasons: list[str] = []
    processing_fields = (
        "platform",
        "sensor",
        "frequencyBand",
        "instrumentMode",
        "productType",
        "processingLevel",
        "processingProfileVersion",
        "calibrationFormula",
        "outputScale",
    )
    if any(current.get(key) != candidate.get(key) for key in processing_fields):
        reasons.append("PROCESSING_PROFILE_MISMATCH")
    if current.get("providerSoftwareVersion") != candidate.get("providerSoftwareVersion"):
        reasons.append("PROCESSING_SOFTWARE_MISMATCH")
    if (
        current.get("rtcApplied") != candidate.get("rtcApplied")
        or current.get("demCorrection") != candidate.get("demCorrection")
        or current.get("demSource") != candidate.get("demSource")
    ):
        reasons.append("RTC_OR_DEM_MISMATCH")
    if current.get("polarizations") != candidate.get("polarizations"):
        reasons.append("POLARIZATION_MISMATCH")
    if current.get("sensorOrientation") != candidate.get("sensorOrientation"):
        reasons.append("LOOK_DIRECTION_MISMATCH")
    if current.get("orbitState") != candidate.get("orbitState"):
        reasons.append("ORBIT_STATE_MISMATCH")
    if current.get("trackKey") != candidate.get("trackKey"):
        reasons.append("TRACK_MISMATCH")
    if abs(
        float(current["incidenceAngleDegrees"])
        - float(candidate["incidenceAngleDegrees"])
    ) > incidence_tolerance_degrees:
        reasons.append("INCIDENCE_ANGLE_MISMATCH")
    if abs(float(current["pixelSpacingMeters"]) - float(candidate["pixelSpacingMeters"])) > (
        resolution_tolerance_meters
    ):
        reasons.append("RESOLUTION_MISMATCH")
    return reasons


def comparison_key_hash(metadata: dict[str, Any]) -> str:
    fields = (
        "policyVersion",
        "platform",
        "sensor",
        "frequencyBand",
        "instrumentMode",
        "productType",
        "processingLevel",
        "processingProfileVersion",
        "providerSoftwareVersion",
        "calibrationFormula",
        "outputScale",
        "rtcApplied",
        "demCorrection",
        "demSource",
        "polarizations",
        "sensorOrientation",
        "orbitState",
        "trackKey",
        "incidenceAngleDegrees",
        "pixelSpacingMeters",
    )
    canonical = {key: metadata.get(key) for key in fields}
    return sha256(dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return None if text.upper() in _MISSING_TEXT else text.upper()


def _number(value: Any) -> float | None:
    try:
        result = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) and result > -9999 else None


def _positive_integer(value: Any) -> int | None:
    number = _number(value)
    if number is None or number <= 0 or not number.is_integer():
        return None
    return int(number)


def _flag(value: Any) -> bool | None:
    text = str(value or "").strip()
    if text == "1":
        return True
    if text == "0":
        return False
    return None


def _yes_no(value: Any) -> bool | None:
    text = str(value or "").strip().upper()
    if text in {"YES", "Y", "TRUE", "1"}:
        return True
    if text in {"NO", "N", "FALSE", "0"}:
        return False
    return None


def _orbit_state(pass_type: str | None, heading: float | None) -> tuple[str | None, str | None]:
    if pass_type:
        if pass_type.startswith("ASC") or pass_type == "A":
            return "ASCENDING", "provider_pass_type"
        if pass_type.startswith("DES") or pass_type == "D":
            return "DESCENDING", "provider_pass_type"
    if heading is None or heading < 0 or heading >= 360 or heading in {90.0, 270.0}:
        return None, None
    state = "ASCENDING" if heading < 90 or heading > 270 else "DESCENDING"
    return state, "satellite_heading"


def _provider_datetime(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or text.upper() in _MISSING_TEXT:
        return None
    if "." in text:
        prefix, fraction = text.split(".", 1)
        text = f"{prefix}.{fraction[:6]}"
        pattern = "%d-%b-%Y %H:%M:%S.%f"
    else:
        pattern = "%d-%b-%Y %H:%M:%S"
    try:
        parsed = datetime.strptime(text, pattern).replace(tzinfo=UTC)
    except ValueError:
        return None
    return parsed.isoformat().replace("+00:00", "Z")
