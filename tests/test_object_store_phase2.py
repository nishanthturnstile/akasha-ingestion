from __future__ import annotations

import pytest

from akasha.storage.object_store import InMemoryObjectStore, ObjectStoreNotFoundError


def test_memory_object_store_writes_stac_manifest_and_source_cog() -> None:
    store = InMemoryObjectStore()
    item = {"id": "S2A_001", "type": "Feature"}
    manifest = {"assets": [{"asset_key": "red"}]}

    item_path, item_checksum = store.put_stac_item(
        provider="earthsearch",
        source_id="sentinel-2-l2a",
        stac_item_id="S2A_001",
        item=item,
    )
    manifest_path, _ = store.put_asset_manifest(
        provider="earthsearch",
        source_id="sentinel-2-l2a",
        stac_item_id="S2A_001",
        manifest=manifest,
    )
    cog_path, cog_checksum = store.put_source_cog(
        provider="earthsearch",
        source_id="sentinel-2-l2a",
        stac_item_id="S2A_001",
        asset_key="red",
        payload=b"tiny-cog",
    )

    assert item_path == "raw/earthsearch/sentinel-2-l2a/S2A_001/stac-item.json"
    assert store.get_json(item_path) == item
    assert store.get_json(manifest_path) == manifest
    assert store.stat(cog_path).size_bytes == len(b"tiny-cog")
    assert item_checksum
    assert cog_checksum


def test_memory_object_store_missing_required_object_raises() -> None:
    store = InMemoryObjectStore()

    with pytest.raises(ObjectStoreNotFoundError):
        store.get_required("missing/object.tif")
