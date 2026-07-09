from __future__ import annotations

import pytest

from akasha.processing.resourcesat import (
    BHOONIDHI_AWIFS_BOA_COLLECTION_ID,
    BHOONIDHI_COLLECTION_IDS,
    BHOONIDHI_LISS3_BOA_COLLECTION_ID,
    BHOONIDHI_LISS4_MX70_L2_COLLECTION_ID,
    INGESTION_BANGALORE_60KM_AOI_ID,
    INGESTION_TO_PRODUCT_AOI,
    PRODUCT_BANGALORE_60KM_AOI_LABEL,
    RESOURCESAT_AWIFS_BOA_SOURCE_ID,
    RESOURCESAT_LISS3_BOA_SOURCE_ID,
    RESOURCESAT_LISS4_MX70_L2_SOURCE_ID,
    RESOURCESAT_SOURCE_COLLECTIONS,
    RESOURCESAT_SOURCE_IDS,
    source_collection,
)


def test_resourcesat_source_contract_constants_are_pinned() -> None:
    assert RESOURCESAT_LISS3_BOA_SOURCE_ID == "resourcesat-2a-liss3-boa"
    assert RESOURCESAT_LISS4_MX70_L2_SOURCE_ID == "resourcesat-2a-liss4-mx70-l2"
    assert RESOURCESAT_AWIFS_BOA_SOURCE_ID == "resourcesat-2a-awifs-boa"
    assert RESOURCESAT_SOURCE_IDS == (
        "resourcesat-2a-liss3-boa",
        "resourcesat-2a-liss4-mx70-l2",
        "resourcesat-2a-awifs-boa",
    )

    assert BHOONIDHI_LISS3_BOA_COLLECTION_ID == "ResourceSat-2A_LISS3_BOA"
    assert BHOONIDHI_LISS4_MX70_L2_COLLECTION_ID == "ResourceSat-2A_LISS4-MX70_L2"
    assert BHOONIDHI_AWIFS_BOA_COLLECTION_ID == "ResourceSat-2A_AWIFS_BOA"
    assert BHOONIDHI_COLLECTION_IDS == (
        "ResourceSat-2A_LISS3_BOA",
        "ResourceSat-2A_LISS4-MX70_L2",
        "ResourceSat-2A_AWIFS_BOA",
    )


def test_source_collection_maps_resourcesat_sources_to_bhoonidhi_collections() -> None:
    assert dict(RESOURCESAT_SOURCE_COLLECTIONS) == {
        "resourcesat-2a-liss3-boa": "ResourceSat-2A_LISS3_BOA",
        "resourcesat-2a-liss4-mx70-l2": "ResourceSat-2A_LISS4-MX70_L2",
        "resourcesat-2a-awifs-boa": "ResourceSat-2A_AWIFS_BOA",
    }
    assert source_collection(RESOURCESAT_LISS3_BOA_SOURCE_ID) == BHOONIDHI_LISS3_BOA_COLLECTION_ID
    assert (
        source_collection(RESOURCESAT_LISS4_MX70_L2_SOURCE_ID)
        == BHOONIDHI_LISS4_MX70_L2_COLLECTION_ID
    )
    assert source_collection(RESOURCESAT_AWIFS_BOA_SOURCE_ID) == BHOONIDHI_AWIFS_BOA_COLLECTION_ID


def test_ingestion_aoi_maps_to_product_aoi_label() -> None:
    assert INGESTION_BANGALORE_60KM_AOI_ID == "bangalore_60km_geodesic_aoi"
    assert PRODUCT_BANGALORE_60KM_AOI_LABEL == "bangalore-60km"
    assert dict(INGESTION_TO_PRODUCT_AOI) == {"bangalore_60km_geodesic_aoi": "bangalore-60km"}


def test_source_collection_rejects_unknown_sources() -> None:
    with pytest.raises(ValueError, match="unsupported ResourceSat source"):
        source_collection("sentinel-2-l2a")
