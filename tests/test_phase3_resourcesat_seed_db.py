from __future__ import annotations

import json
from typing import Any

from akasha.catalog.profile_repository import InMemoryProfileRepository, build_memory_profiles
from akasha.catalog.seed_db import (
    PROVIDER_ROUTES,
    SOURCE_METADATA,
    THRESHOLD_PROFILES,
    VISUALIZATION_PROFILES,
    seed_execution_policies,
)
from akasha.catalog.source_route_repository import (
    InMemorySourceProviderRouteRepository,
    build_memory_routes,
)
from akasha.processing.resourcesat import (
    BHOONIDHI_AWIFS_BOA_COLLECTION_ID,
    BHOONIDHI_LISS3_BOA_COLLECTION_ID,
    BHOONIDHI_LISS4_MX70_L2_COLLECTION_ID,
    RESOURCESAT_AWIFS_BOA_SOURCE_ID,
    RESOURCESAT_LISS3_BOA_SOURCE_ID,
    RESOURCESAT_LISS4_MX70_L2_SOURCE_ID,
)

EXPECTED_COLLECTIONS = {
    RESOURCESAT_LISS3_BOA_SOURCE_ID: BHOONIDHI_LISS3_BOA_COLLECTION_ID,
    RESOURCESAT_LISS4_MX70_L2_SOURCE_ID: BHOONIDHI_LISS4_MX70_L2_COLLECTION_ID,
    RESOURCESAT_AWIFS_BOA_SOURCE_ID: BHOONIDHI_AWIFS_BOA_COLLECTION_ID,
}

EXPECTED_SUPPORTED_INDICES = {
    RESOURCESAT_LISS3_BOA_SOURCE_ID: {"ndvi", "msavi", "ndmi", "ndwi_green_nir"},
    RESOURCESAT_LISS4_MX70_L2_SOURCE_ID: {"ndvi", "msavi", "ndwi_green_nir"},
    RESOURCESAT_AWIFS_BOA_SOURCE_ID: {"ndvi", "msavi", "ndmi", "ndwi_green_nir"},
}


def test_memory_provider_routes_include_bhoonidhi_resourcesat_routes() -> None:
    routes = build_memory_routes(PROVIDER_ROUTES)
    repository = InMemorySourceProviderRouteRepository(routes)

    for source_id, provider_collection in EXPECTED_COLLECTIONS.items():
        route_key = f"bhoonidhi:{provider_collection}"
        route = repository.get_by_route_key(source_id, route_key)
        visible_routes = repository.list_by_source(source_id)

        assert route is not None
        assert route in visible_routes
        assert route.provider_adapter == "bhoonidhi"
        assert route.provider_collection == provider_collection
        assert route.access_mode == "authenticated_download"
        assert route.execution_policy_ref == "bhoonidhi-default"
        assert route.status == "manual_only"
        assert route.metadata["route_key"] == route_key
        assert route.metadata["requires_approved_runtime"] is True
        assert "source_notes" in route.metadata


def test_memory_profiles_include_resourcesat_indices_and_source_thresholds() -> None:
    visualization_profiles, threshold_profiles = build_memory_profiles(
        VISUALIZATION_PROFILES,
        THRESHOLD_PROFILES,
    )
    repository = InMemoryProfileRepository(
        visualization_profiles=visualization_profiles,
        threshold_profiles=threshold_profiles,
    )

    expected_indices = {"ndvi", "msavi", "ndmi", "ndwi_green_nir"}
    default_visualizations = {
        profile.index_name for profile in visualization_profiles if profile.is_default
    }
    assert expected_indices <= default_visualizations

    resource_thresholds = [
        profile
        for profile in threshold_profiles
        if profile.source_id in EXPECTED_SUPPORTED_INDICES
    ]
    threshold_lookup = {
        (profile.source_id, profile.index_name)
        for profile in resource_thresholds
    }
    expected_thresholds = {
        (source_id, index_name)
        for source_id, indices in EXPECTED_SUPPORTED_INDICES.items()
        for index_name in indices
    }

    assert expected_thresholds <= threshold_lookup
    assert (RESOURCESAT_LISS4_MX70_L2_SOURCE_ID, "ndmi") not in threshold_lookup
    assert {
        profile.index_name
        for profile in resource_thresholds
        if profile.source_id == RESOURCESAT_LISS4_MX70_L2_SOURCE_ID
    } == EXPECTED_SUPPORTED_INDICES[RESOURCESAT_LISS4_MX70_L2_SOURCE_ID]

    for source_id, index_names in EXPECTED_SUPPORTED_INDICES.items():
        for index_name in index_names:
            assert repository.get_default_visualization(index_name) is not None
            threshold = repository.get_default_threshold(index_name, source_id=source_id)
            assert threshold is not None
            assert threshold.source_id == source_id


def test_source_metadata_has_resourcesat_validation_processing_and_provider_profiles() -> None:
    for source_id, provider_collection in EXPECTED_COLLECTIONS.items():
        metadata = SOURCE_METADATA[source_id]
        validation_profile = metadata["validation_profile"]
        processing_profile = metadata["processing_profile"]
        license_profile = metadata["license_profile"]
        provider_metadata = metadata["provider_metadata"]

        assert metadata["status"] == "disabled"
        assert metadata["execution_policy_ref"] == "bhoonidhi-default"
        assert validation_profile["version"].startswith("phase3-resourcesat-")
        assert validation_profile["mask_method"] == "akasha-threshold-mask-v1"
        assert "resourcesat_mask_validation" in validation_profile["checks"]
        assert processing_profile["version"].startswith("resourcesat-")
        assert processing_profile["source"] == "bhoonidhi"
        assert processing_profile["reflectance_offset"] == 0.0
        assert set(processing_profile["supported_indices"]) == EXPECTED_SUPPORTED_INDICES[source_id]
        assert license_profile["profile"] == "nrsc-bhoonidhi-restricted"
        assert provider_metadata["provider_collection"] == provider_collection
        assert provider_metadata["pgstac_collection"].startswith("akasha-resourcesat-2a-")
        assert provider_metadata["primary_route"] == f"bhoonidhi:{provider_collection}"
        assert provider_metadata["mask_method"] == "akasha-threshold-mask-v1"
        assert provider_metadata["requires_approved_runtime"] is True


def test_bhoonidhi_execution_policy_seed_is_disabled_and_explicit() -> None:
    connection = _CapturingConnection()

    seed_execution_policies(connection)

    bhoonidhi_policy = next(
        params for params in connection.params if params["policy_key"] == "bhoonidhi-default"
    )
    retry_policy = json.loads(bhoonidhi_policy["retry_policy_json"])
    staging_policy = json.loads(bhoonidhi_policy["staging_policy_json"])
    checksum_policy = json.loads(bhoonidhi_policy["checksum_policy_json"])

    assert bhoonidhi_policy["provider_adapter"] == "bhoonidhi"
    assert bhoonidhi_policy["enabled"] is False
    assert bhoonidhi_policy["version"] == "phase3-bhoonidhi-v1"
    assert retry_policy["max_attempts"] == 3
    assert retry_policy["backoff"] == "exponential"
    assert staging_policy["enabled_by_default"] is False
    assert staging_policy["approved_runtime_required"] is True
    assert staging_policy["concurrency"]["max_concurrent_downloads"] == 1
    assert staging_policy["redaction"]["enabled"] is True
    assert "download_url" in staging_policy["redaction"]["fields"]
    assert checksum_policy == {
        "required": True,
        "algorithm": "sha256",
        "sidecar_required": True,
        "validate_on_download": True,
        "validate_before_catalog": True,
    }


class _CapturingConnection:
    def __init__(self) -> None:
        self.params: list[dict[str, Any]] = []

    def execute(self, statement: object, params: dict[str, Any]) -> None:
        self.params.append(dict(params))
