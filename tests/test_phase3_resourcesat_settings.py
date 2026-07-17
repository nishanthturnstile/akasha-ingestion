from __future__ import annotations

from pathlib import Path

import pytest

from akasha.config import Settings, validate_resourcesat_runtime_roots

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_resourcesat_settings_defaults_are_safe_and_gated() -> None:
    settings = Settings()

    assert settings.bhoonidhi_user_id == ""
    assert settings.bhoonidhi_password.get_secret_value() == ""
    assert settings.bhoonidhi_max_downloads_per_run == 1
    assert settings.bhoonidhi_approved_runtime_required is True
    assert settings.live_provider_tests is False

    assert settings.resourcesat_profile_version == "resourcesat-phase3-v1"
    assert settings.resourcesat_liss3_preload_source_id == "resourcesat-2a-liss3-boa"
    assert settings.resourcesat_liss4_preload_source_id == "resourcesat-2a-liss4-mx70-l2"
    assert settings.resourcesat_awifs_preload_source_id == "resourcesat-2a-awifs-boa"
    assert settings.resourcesat_liss3_preload_provider_route == (
        "bhoonidhi:ResourceSat-2A_LISS3_BOA"
    )
    assert settings.resourcesat_liss4_preload_provider_route == (
        "bhoonidhi:ResourceSat-2A_LISS4-MX70_L2"
    )
    assert settings.resourcesat_awifs_preload_provider_route == (
        "bhoonidhi:ResourceSat-2A_AWIFS_BOA"
    )
    assert settings.resourcesat_liss3_preload_schedule_enabled is False
    assert settings.resourcesat_liss4_preload_schedule_enabled is False
    assert settings.resourcesat_awifs_preload_schedule_enabled is False
    assert settings.resourcesat_liss3_composite_min_coverage_percent == 95.0
    assert settings.resourcesat_liss4_composite_min_coverage_percent == 10.0
    assert settings.resourcesat_awifs_composite_min_coverage_percent == 60.0


def test_custom_resourcesat_settings_accept_constructor_values(tmp_path: Path) -> None:
    settings = Settings(
        bhoonidhi_user_id="operator",
        bhoonidhi_password="secret",
        bhoonidhi_search_rps=1.5,
        bhoonidhi_max_downloads_per_run=3,
        resourcesat_approved_data_root=tmp_path,
        resourcesat_liss3_preload_schedule_enabled=True,
        resourcesat_liss3_processing_resolution_m=23.5,
        resourcesat_liss3_composite_min_coverage_percent=90.0,
    )

    assert settings.bhoonidhi_user_id == "operator"
    assert settings.bhoonidhi_password.get_secret_value() == "secret"
    assert settings.bhoonidhi_search_rps == 1.5
    assert settings.bhoonidhi_max_downloads_per_run == 3
    assert settings.resourcesat_approved_data_root == tmp_path
    assert settings.resourcesat_liss3_preload_schedule_enabled is True
    assert settings.resourcesat_liss3_processing_resolution_m == 23.5
    assert settings.resourcesat_liss3_composite_min_coverage_percent == 90.0


def test_empty_strings_normalize_for_optional_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AKASHA_AWS_REQUEST_PAYER", "")
    monkeypatch.setenv("AKASHA_SOURCE_MIRROR_MAX_BYTES_PER_RUN", "")
    monkeypatch.setenv("AKASHA_RESOURCESAT_APPROVED_DATA_ROOT", "")
    monkeypatch.setenv("AKASHA_RESOURCESAT_LISS3_PROCESSING_RESOLUTION_M", "")
    monkeypatch.setenv("AKASHA_RESOURCESAT_LISS4_PROCESSING_RESOLUTION_M", "")
    monkeypatch.setenv("AKASHA_RESOURCESAT_AWIFS_PROCESSING_RESOLUTION_M", "")

    from_env = Settings()
    from_constructor = Settings(
        aws_request_payer="",
        source_mirror_max_bytes_per_run="",
        resourcesat_approved_data_root="",
        resourcesat_liss3_processing_resolution_m="",
        resourcesat_liss4_processing_resolution_m="",
        resourcesat_awifs_processing_resolution_m="",
    )

    for settings in (from_env, from_constructor):
        assert settings.aws_request_payer is None
        assert settings.source_mirror_max_bytes_per_run is None
        assert settings.resourcesat_approved_data_root is None
        assert settings.resourcesat_liss3_processing_resolution_m is None
        assert settings.resourcesat_liss4_processing_resolution_m is None
        assert settings.resourcesat_awifs_processing_resolution_m is None


def test_resourcesat_runtime_root_preflight_allows_dry_run_default() -> None:
    validate_resourcesat_runtime_roots(Settings(), dry_run=True)


@pytest.mark.parametrize(
    "unsafe_root",
    [
        "/",
        "/tmp",
        "/tmp/akasha",
        "/var/tmp/akasha",
        "/var/lib/docker/volumes/akasha",
        "/data/coolify/applications/akasha",
    ],
)
def test_resourcesat_runtime_root_preflight_rejects_unsafe_roots(unsafe_root: str) -> None:
    settings = Settings(
        scratch_dir=unsafe_root,
        resourcesat_approved_data_root="/srv/akasha",
    )

    with pytest.raises(ValueError, match="unsafe ResourceSat runtime root"):
        validate_resourcesat_runtime_roots(settings, dry_run=False)


def test_resourcesat_runtime_root_preflight_requires_approved_root() -> None:
    settings = Settings(scratch_dir="/opt/akasha/scratch")

    with pytest.raises(ValueError, match="must be under an approved data root"):
        validate_resourcesat_runtime_roots(settings, dry_run=False)


def test_resourcesat_runtime_root_preflight_accepts_configured_approved_root() -> None:
    approved_root = Path("/srv/akasha/resourcesat-live")
    settings = Settings(
        scratch_dir=approved_root / "scratch",
        resourcesat_approved_data_root=approved_root,
    )

    validate_resourcesat_runtime_roots(settings, dry_run=False)


def test_staging_compose_wires_safe_bounded_resourcesat_runtime() -> None:
    compose = (REPO_ROOT / "deploy" / "compose.staging.yml").read_text(encoding="utf-8")

    data_root = "${AKASHA_DATA_ROOT:-/srv/akasha/ingestion-platform}"
    assert f"AKASHA_SCRATCH_DIR: {data_root}/scratch" in compose
    assert f"AKASHA_RESOURCESAT_APPROVED_DATA_ROOT: {data_root}" in compose
    assert f"- {data_root}/scratch:{data_root}/scratch" in compose
    assert (
        "AKASHA_BHOONIDHI_APPROVED_RUNTIME: "
        "${AKASHA_BHOONIDHI_APPROVED_RUNTIME:-false}" in compose
    )
    assert (
        "AKASHA_BHOONIDHI_MAX_DOWNLOADS_PER_RUN: "
        "${AKASHA_BHOONIDHI_MAX_DOWNLOADS_PER_RUN:-7}" in compose
    )
    assert (
        "AKASHA_SOURCE_MIRROR_REQUIRED_HEADROOM_BYTES: "
        "${AKASHA_SOURCE_MIRROR_REQUIRED_HEADROOM_BYTES:-21474836480}" in compose
    )
    for source in ("LISS3", "LISS4", "AWIFS"):
        assert (
            f"AKASHA_RESOURCESAT_{source}_PRELOAD_SCHEDULE_ENABLED: "
            f"${{AKASHA_RESOURCESAT_{source}_PRELOAD_SCHEDULE_ENABLED:-true}}" in compose
        )
        assert (
            f"AKASHA_RESOURCESAT_{source}_READINESS_ENABLED: "
            f"${{AKASHA_RESOURCESAT_{source}_READINESS_ENABLED:-true}}" in compose
        )

    assert (
        "AKASHA_SENTINEL2_PRELOAD_SCHEDULE_ENABLED: "
        "${AKASHA_SENTINEL2_PRELOAD_SCHEDULE_ENABLED:-true}" in compose
    )


def test_base_compose_propagates_every_resourcesat_source_contract() -> None:
    compose = (REPO_ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")

    for source in ("LISS3", "LISS4", "AWIFS"):
        for setting in (
            "PRELOAD_SOURCE_ID",
            "PRELOAD_AOI_ID",
            "PRELOAD_PROVIDER_ROUTE",
            "PRELOAD_DATE_WINDOW_DAYS",
            "PRELOAD_REFRESH_DAYS",
            "PRELOAD_FRESHNESS_MAX_AGE_HOURS",
            "PRELOAD_SCHEDULE_ENABLED",
            "READINESS_ENABLED",
            "READINESS_REQUIRED_INDICES",
            "PROCESSING_RESOLUTION_M",
            "COMPOSITE_MIN_COVERAGE_PERCENT",
        ):
            key = f"AKASHA_RESOURCESAT_{source}_{setting}"
            assert f"  {key}: ${{{key}:-" in compose


def test_staging_resourcesat_heavy_worker_has_provider_egress() -> None:
    compose = (REPO_ROOT / "deploy" / "compose.staging.yml").read_text(encoding="utf-8")
    worker_start = compose.index("  worker-heavy:")
    worker_end = compose.index("\n  postgres:", worker_start)
    worker_heavy = compose[worker_start:worker_end]

    assert "    networks: [edge, internal]" in worker_heavy
