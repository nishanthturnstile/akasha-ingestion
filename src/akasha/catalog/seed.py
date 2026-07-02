from __future__ import annotations

from akasha.schemas import SourceResponse

SEED_SOURCES: tuple[SourceResponse, ...] = (
    SourceResponse(
        source_id="sentinel-2-l2a",
        catalog_slug="sentinel-2",
        provider_adapter="cdse",
        instrument_mode="MSI",
        analysis_level="L2A",
        schedule_state="disabled",
        product_exposure="hidden",
        supported_indices=["ndvi", "ndre", "reci", "msavi", "ndmi", "ndbi"],
    ),
    SourceResponse(
        source_id="resourcesat-2a-liss4-mx70-l2",
        catalog_slug="resourcesat-2a",
        provider_adapter="bhoonidhi",
        instrument_mode="LISS-4",
        analysis_level="L2",
        schedule_state="disabled",
        product_exposure="hidden",
        supported_indices=["ndvi", "msavi", "savi", "ndwi", "gndvi"],
    ),
    SourceResponse(
        source_id="resourcesat-2a-liss3-boa",
        catalog_slug="resourcesat-2a",
        provider_adapter="bhoonidhi",
        instrument_mode="LISS-3",
        analysis_level="BOA",
        schedule_state="disabled",
        product_exposure="hidden",
        supported_indices=["ndvi", "msavi", "savi", "ndwi", "ndmi", "ndbi"],
    ),
    SourceResponse(
        source_id="resourcesat-2a-awifs-boa",
        catalog_slug="resourcesat-2a",
        provider_adapter="bhoonidhi",
        instrument_mode="AWiFS",
        analysis_level="BOA",
        schedule_state="disabled",
        product_exposure="hidden",
        supported_indices=["ndvi", "msavi", "savi", "ndwi", "ndmi", "ndbi"],
    ),
    SourceResponse(
        source_id="landsat-8-9-c2-l2",
        catalog_slug="landsat-8-9",
        provider_adapter="usgs",
        instrument_mode="OLI/TIRS",
        analysis_level="C2L2",
        schedule_state="disabled",
        product_exposure="hidden",
        supported_indices=["ndvi", "msavi", "savi", "ndmi", "ndbi"],
    ),
)


def list_seed_sources() -> list[SourceResponse]:
    return [source.model_copy(deep=True) for source in SEED_SOURCES]
