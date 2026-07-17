from __future__ import annotations

from akasha.catalog.seed import list_seed_sources
from akasha.schemas import SourceResponse

RESOURCESAT_INDEX_SETS = {
    "resourcesat-2a-liss3-boa": ["ndvi", "msavi", "ndmi", "ndwi_green_nir"],
    "resourcesat-2a-liss4-mx70-l2": ["ndvi", "msavi", "ndwi_green_nir"],
    "resourcesat-2a-awifs-boa": ["ndvi", "msavi", "ndmi", "ndwi_green_nir"],
}

RESOURCESAT_METADATA = {
    "resourcesat-2a-liss3-boa": ("LISS-3", "BOA"),
    "resourcesat-2a-liss4-mx70-l2": ("LISS-4", "L2"),
    "resourcesat-2a-awifs-boa": ("AWiFS", "BOA"),
}


def _seed_sources_by_id() -> dict[str, SourceResponse]:
    return {source.source_id: source for source in list_seed_sources()}


def test_resourcesat_seed_supported_indices_are_exact() -> None:
    sources_by_id = _seed_sources_by_id()

    for source_id, expected_indices in RESOURCESAT_INDEX_SETS.items():
        assert sources_by_id[source_id].supported_indices == expected_indices


def test_resourcesat_seed_metadata_is_bhoonidhi_scheduled_and_public() -> None:
    sources_by_id = _seed_sources_by_id()

    for source_id, (instrument_mode, analysis_level) in RESOURCESAT_METADATA.items():
        source = sources_by_id[source_id]

        assert source.provider_adapter == "bhoonidhi"
        assert source.instrument_mode == instrument_mode
        assert source.analysis_level == analysis_level
        assert source.schedule_state == "scheduled"
        assert source.product_exposure == "public"


def test_removed_indices_do_not_appear_in_resourcesat_seed_rows() -> None:
    removed_indices = {"savi", "gndvi", "ndbi", "ndwi"}
    resourcesat_sources = [
        source for source in list_seed_sources() if source.source_id.startswith("resourcesat-")
    ]

    for source in resourcesat_sources:
        assert removed_indices.isdisjoint(source.supported_indices)


def test_resourcesat_seed_source_ids_are_not_duplicated() -> None:
    resourcesat_source_ids = [
        source.source_id
        for source in list_seed_sources()
        if source.source_id.startswith("resourcesat-")
    ]

    assert set(resourcesat_source_ids) == set(RESOURCESAT_INDEX_SETS)
    assert len(resourcesat_source_ids) == len(set(resourcesat_source_ids))


def test_sentinel2_seed_supported_indices_remain_unchanged() -> None:
    sentinel2 = _seed_sources_by_id()["sentinel-2-l2a"]

    assert sentinel2.supported_indices == ["ndvi", "msavi", "ndmi", "ndbi", "ndre", "reci"]
