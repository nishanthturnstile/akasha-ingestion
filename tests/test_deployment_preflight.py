from __future__ import annotations

from akasha.deployment.four_source_config import (
    PRODUCTION_SOURCES,
    RUNTIME_SERVICES,
    validate_config,
)


def _valid_config() -> dict:
    environment = {
        flag: "true"
        for flags in PRODUCTION_SOURCES.values()
        for flag in flags
    }
    environment.update(
        {
            "AKASHA_API_KEY_HASHES": "sha256:configured",
            "AKASHA_BHOONIDHI_USER_ID": "service-user",
            "AKASHA_BHOONIDHI_PASSWORD": "secret",
            "AKASHA_BHOONIDHI_APPROVED_RUNTIME": "true",
            "AKASHA_SIGNING_SECRET": "x" * 32,
            "AKASHA_PUBLIC_BASE_URL": "https://ingestion.internal.example",
        }
    )
    return {"services": {name: {"environment": dict(environment)} for name in RUNTIME_SERVICES}}


def test_four_source_preflight_accepts_complete_runtime() -> None:
    assert validate_config(_valid_config()) == []


def test_four_source_preflight_reports_disabled_source_and_missing_secret() -> None:
    config = _valid_config()
    config["services"]["scheduler"]["environment"][
        "AKASHA_RESOURCESAT_AWIFS_PRELOAD_SCHEDULE_ENABLED"
    ] = "false"
    config["services"]["api"]["environment"]["AKASHA_SIGNING_SECRET"] = "change-me"

    errors = validate_config(config)

    assert any("resourcesat-2a-awifs-boa" in error for error in errors)
    assert any("AKASHA_SIGNING_SECRET" in error for error in errors)
