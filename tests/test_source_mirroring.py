from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from akasha.config import RuntimeBackend, Settings
from akasha.providers.contracts import NormalizedAsset, NormalizedStacItem
from akasha.services.source_mirroring import SourceMirroringService
from akasha.storage.object_store import InMemoryObjectStore


def test_source_mirroring_writes_source_cog_and_provenance_metadata() -> None:
    store = InMemoryObjectStore()
    service = SourceMirroringService(
        object_store=store,
        settings=Settings(runtime_backend=RuntimeBackend.MEMORY),
    )
    item = NormalizedStacItem(
        provider_adapter="earthsearch",
        provider_collection="sentinel-2-l2a",
        source_id="sentinel-2-l2a",
        stac_item_id="S2A_001",
        logical_scene_key="sentinel-2-l2a:S2A_001",
        acquisition_at=datetime(2026, 1, 15, tzinfo=UTC),
        platform="sentinel-2a",
        constellation="sentinel-2",
        instrument="msi",
        mgrs_tile="T43PHQ",
        footprint=None,
        bbox=[77.0, 12.5, 78.0, 13.5],
        cloud_percent=4.2,
        assets={},
        raw_item={"id": "S2A_001"},
    )
    asset = NormalizedAsset(
        asset_key="red",
        href="https://earth-search.test/S2A_001/red.tif",
        scale=0.0001,
        offset=0.0,
        nodata=0,
    )

    result = service.mirror_asset(item=item, asset=asset, payload=b"tiny-source-cog")
    metadata = store.get_json(result.metadata_path)

    assert result.object_path == (
        "raw/earthsearch/sentinel-2-l2a/S2A_001/source-cogs/red.tif"
    )
    assert result.size_bytes == len(b"tiny-source-cog")
    assert metadata["source_href"] == "https://earth-search.test/S2A_001/red.tif"
    assert metadata["mirror_checksum_sha256"] == result.checksum_sha256
    assert metadata["mirror_mode"] == "aoi_clipped"


def test_source_mirroring_streaming_path_enforces_byte_limit(tmp_path) -> None:
    store = InMemoryObjectStore()
    service = SourceMirroringService(
        object_store=store,
        settings=Settings(
            runtime_backend=RuntimeBackend.MEMORY,
            scratch_dir=tmp_path,
            source_mirror_max_bytes_per_run=4,
        ),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, content=b"larger-than-limit")
            )
        ),
    )
    item = _item()
    asset = NormalizedAsset(
        asset_key="red",
        href="https://earth-search.test/S2A_001/red.tif",
        scale=0.0001,
        offset=0.0,
        nodata=0,
    )

    with pytest.raises(ValueError, match="byte limit"):
        service.mirror_asset(item=item, asset=asset)


def _item() -> NormalizedStacItem:
    return NormalizedStacItem(
        provider_adapter="earthsearch",
        provider_collection="sentinel-2-l2a",
        source_id="sentinel-2-l2a",
        stac_item_id="S2A_001",
        logical_scene_key="sentinel-2-l2a:S2A_001",
        acquisition_at=datetime(2026, 1, 15, tzinfo=UTC),
        platform="sentinel-2a",
        constellation="sentinel-2",
        instrument="msi",
        mgrs_tile="T43PHQ",
        footprint=None,
        bbox=[77.0, 12.5, 78.0, 13.5],
        cloud_percent=4.2,
        assets={},
        raw_item={"id": "S2A_001"},
    )
