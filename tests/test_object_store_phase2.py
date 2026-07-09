from __future__ import annotations

from pathlib import Path

import pytest

from akasha.storage.object_store import InMemoryObjectStore, ObjectStoreNotFoundError, file_sha256


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


def test_memory_object_store_writes_raw_zip_and_prepared_cog_files(tmp_path: Path) -> None:
    store = InMemoryObjectStore()
    raw_zip = tmp_path / "original.zip"
    raw_zip.write_bytes(b"zip-payload")
    analytic = tmp_path / "analytic.cog.tif"
    analytic.write_bytes(b"cog-payload")

    raw_path, raw_checksum = store.put_raw_file(
        provider="bhoonidhi",
        source_id="resourcesat-2a-liss3-boa",
        product_id="P1",
        file_path=raw_zip,
        checksum_sha256=file_sha256(raw_zip),
    )
    cog_path, cog_checksum = store.put_prepared_cog_file(
        provider="bhoonidhi",
        source_id="resourcesat-2a-liss3-boa",
        product_id="P1",
        asset_key="analytic",
        file_path=analytic,
        checksum_sha256=file_sha256(analytic),
        metadata={"asset-kind": "prepared-analytic"},
    )

    assert raw_path == "raw/bhoonidhi/resourcesat-2a-liss3-boa/P1/original.zip"
    assert cog_path == "prepared/bhoonidhi/resourcesat-2a-liss3-boa/P1/analytic.cog.tif"
    assert raw_checksum == file_sha256(raw_zip)
    assert cog_checksum == file_sha256(analytic)
    assert store.get_required(f"{raw_path}.sha256") == raw_checksum.encode("utf-8")
    assert store.get_required(f"{cog_path}.sha256") == cog_checksum.encode("utf-8")


def test_memory_object_store_writes_composite_cogs_and_manifest(tmp_path: Path) -> None:
    store = InMemoryObjectStore()
    analytic = tmp_path / "analytic.tif"
    mask = tmp_path / "mask.tif"
    analytic.write_bytes(b"analytic")
    mask.write_bytes(b"mask")

    analytic_path, analytic_checksum = store.put_composite_cog_file(
        source_id="resourcesat-2a-liss3-boa",
        aoi_id="bangalore_60km_geodesic_aoi",
        composite_date="2026-03-19",
        asset_key="analytic",
        file_path=analytic,
        checksum_sha256=file_sha256(analytic),
    )
    mask_path, mask_checksum = store.put_composite_cog_file(
        source_id="resourcesat-2a-liss3-boa",
        aoi_id="bangalore_60km_geodesic_aoi",
        composite_date="2026-03-19",
        asset_key="mask",
        file_path=mask,
        checksum_sha256=file_sha256(mask),
    )
    manifest_path, _ = store.put_composite_manifest(
        source_id="resourcesat-2a-liss3-boa",
        aoi_id="bangalore_60km_geodesic_aoi",
        composite_date="2026-03-19",
        manifest={"output_kind": "resource_sat_composite"},
    )

    assert analytic_path == (
        "composite/resourcesat-2a-liss3-boa/bangalore_60km_geodesic_aoi/"
        "2026-03-19/analytic.tif"
    )
    assert mask_path.endswith("/mask.tif")
    assert manifest_path.endswith("/manifest.json")
    assert store.get_required(f"{analytic_path}.sha256") == analytic_checksum.encode("utf-8")
    assert store.get_required(f"{mask_path}.sha256") == mask_checksum.encode("utf-8")
    assert store.get_json(manifest_path)["output_kind"] == "resource_sat_composite"


def test_memory_object_store_writes_derived_cog_file_with_canonical_index_path(
    tmp_path: Path,
) -> None:
    store = InMemoryObjectStore()
    derived = tmp_path / "ndwi_green_nir.cog.tif"
    derived.write_bytes(b"derived-index")

    object_path, checksum = store.put_derived_cog_file(
        provider="bhoonidhi",
        source_id="resourcesat-2a-liss3-boa",
        stac_item_id="resourcesat-2a-liss3-boa:composite:bangalore:2026-03-19",
        index_name="ndwi_green_nir",
        file_path=derived,
        checksum_sha256=file_sha256(derived),
        metadata={"index-name": "ndwi_green_nir"},
    )

    assert object_path == (
        "indices/bhoonidhi/resourcesat-2a-liss3-boa/"
        "resourcesat-2a-liss3-boa:composite:bangalore:2026-03-19/"
        "ndwi_green_nir.cog.tif"
    )
    assert checksum == file_sha256(derived)
    assert store.get_required(f"{object_path}.sha256") == checksum.encode("utf-8")
