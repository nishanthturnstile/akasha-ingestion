from __future__ import annotations

from pathlib import Path

import pytest

from akasha.catalog.profile_repository import InMemoryProfileRepository, build_memory_profiles
from akasha.catalog.repository import StaticSourceCatalog
from akasha.catalog.seed_db import (
    PROVIDER_ROUTES,
    THRESHOLD_PROFILES,
    VISUALIZATION_PROFILES,
    build_execution_policies,
)
from akasha.catalog.source_route_repository import (
    InMemorySourceProviderRouteRepository,
    build_memory_routes,
)
from akasha.config import Settings, SourceMirrorMode
from akasha.jobs.stage_store import InMemoryStageStore, StageStatus

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_phase2_settings_defaults_are_safe() -> None:
    settings = Settings()

    assert settings.earthsearch_api_url == "https://earth-search.aws.element84.com/v1"
    assert settings.earthsearch_page_size == 100
    assert settings.planetary_computer_api_url.endswith("/api/stac/v1")
    assert settings.planetary_computer_page_size == 100
    assert settings.source_mirror_mode == SourceMirrorMode.AOI_CLIPPED
    assert settings.enable_landsat_requester_pays is False
    assert settings.live_provider_tests is False
    assert settings.field_usable_pixel_threshold == 0.80
    assert settings.field_max_cloud_percentage == 20.0


def test_static_seed_uses_earthsearch_sentinel2_primary_metadata() -> None:
    sources = StaticSourceCatalog().list_sources()
    sentinel2 = next(source for source in sources if source.source_id == "sentinel-2-l2a")

    assert sentinel2.provider_adapter == "earthsearch"
    assert sentinel2.schedule_state == "scheduled"
    assert sentinel2.product_exposure == "public"
    assert sentinel2.supported_indices == ["ndvi", "msavi", "ndmi", "ndbi", "ndre", "reci"]


def test_memory_provider_routes_expose_manual_sentinel2_primary_only_by_default() -> None:
    repository = InMemorySourceProviderRouteRepository(build_memory_routes(PROVIDER_ROUTES))

    routes = repository.list_by_source("sentinel-2-l2a")
    route = repository.get_by_route_key("sentinel-2-l2a", "earthsearch:sentinel-2-l2a")

    assert [item.route_key for item in routes] == ["earthsearch:sentinel-2-l2a"]
    assert route is not None
    assert route.provider_role == "primary"
    assert route.status == "manual_only"
    assert route.access_mode == "public_https"


def test_landsat_seed_is_hidden_manual_with_signed_https_primary() -> None:
    sources = StaticSourceCatalog().list_sources()
    landsat = next(source for source in sources if source.source_id == "landsat-c2-l2")
    repository = InMemorySourceProviderRouteRepository(build_memory_routes(PROVIDER_ROUTES))

    routes = repository.list_by_source("landsat-c2-l2")
    route = repository.get_by_route_key(
        "landsat-c2-l2", "planetary-computer:landsat-c2-l2"
    )

    assert landsat.provider_adapter == "planetary-computer"
    assert landsat.schedule_state == "manual"
    assert landsat.product_exposure == "hidden"
    assert landsat.supported_indices == ["ndvi", "msavi", "ndmi", "ndwi_green_nir"]
    assert [item.route_key for item in routes] == ["planetary-computer:landsat-c2-l2"]
    assert route is not None
    assert route.provider_role == "primary"
    assert route.status == "manual_only"
    assert route.access_mode == "signed_https"


def test_every_provider_route_references_a_seeded_execution_policy() -> None:
    policy_keys = {policy["policy_key"] for policy in build_execution_policies()}

    assert {
        route["execution_policy_ref"]
        for route in PROVIDER_ROUTES
        if route.get("execution_policy_ref")
    } <= policy_keys


def test_memory_profiles_return_seeded_ndvi_defaults() -> None:
    visualization_profiles, threshold_profiles = build_memory_profiles(
        VISUALIZATION_PROFILES,
        THRESHOLD_PROFILES,
    )
    repository = InMemoryProfileRepository(
        visualization_profiles=visualization_profiles,
        threshold_profiles=threshold_profiles,
    )

    visualization = repository.get_default_visualization("NDVI")
    threshold = repository.get_default_threshold("NDVI", source_id="sentinel-2-l2a")

    assert visualization is not None
    assert visualization.version == "ndvi-default-v1"
    assert threshold is not None
    assert threshold.profile_key == "ndvi-generic-v1"


def test_stage_store_tracks_attempts_and_blocks_duplicate_running_stage() -> None:
    store = InMemoryStageStore()
    first = store.start_stage(job_id="job-1", stage_name="search")

    with pytest.raises(ValueError, match="stage already running"):
        store.start_stage(job_id="job-1", stage_name="search")

    completed = store.mark_completed(first.stage_id, metadata={"searched_count": 1})
    second = store.start_stage(job_id="job-1", stage_name="search")

    assert completed.status == StageStatus.COMPLETED
    assert completed.metadata["searched_count"] == 1
    assert second.attempt == 2
    assert [stage.attempt for stage in store.list_for_job("job-1")] == [1, 2]


def test_phase2_migration_avoids_unquoted_offset_identifier() -> None:
    migration = REPO_ROOT / "migrations" / "versions" / "0002_phase2_sentinel2_vertical_slice.py"
    migration_text = migration.read_text(encoding="utf-8")

    assert "ADD COLUMN offset numeric" not in migration_text
    assert "DROP COLUMN IF EXISTS offset," not in migration_text
    assert "offset_value" in migration_text
