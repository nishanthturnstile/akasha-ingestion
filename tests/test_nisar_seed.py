from __future__ import annotations

from akasha.catalog.seed import list_seed_sources
from akasha.processing.nisar import NISAR_SOURCE_ID


def test_nisar_source_is_seeded_hidden_and_manual() -> None:
    source = next(
        source for source in list_seed_sources() if source.source_id == NISAR_SOURCE_ID
    )

    assert source.provider_adapter == "bhoonidhi"
    assert source.instrument_mode == "S-SAR"
    assert source.analysis_level == "L2-GCOV"
    assert source.schedule_state == "manual"
    assert source.product_exposure == "hidden"
    assert source.supported_indices == []
