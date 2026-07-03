from __future__ import annotations

from datetime import UTC, datetime

from akasha.processing.stac_assets import build_asset_manifest, manifest_asset_map
from akasha.providers.contracts import NormalizedAsset, NormalizedStacItem


def test_build_asset_manifest_preserves_scale_offset_and_alternates() -> None:
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
        assets={
            "red": NormalizedAsset(
                asset_key="red",
                href="https://earth-search.test/S2A_001/red.tif",
                alternate_hrefs={"s3": "s3://sentinel-cogs/S2A_001/red.tif"},
                scale=0.0001,
                offset=-0.1,
                nodata=0,
            )
        },
        raw_item={"id": "S2A_001"},
    )

    manifest = build_asset_manifest(item)
    mapped = manifest_asset_map(manifest)

    assert manifest["schema_version"] == "phase2-asset-manifest-v1"
    assert mapped["red"]["scale"] == 0.0001
    assert mapped["red"]["offset"] == -0.1
    assert mapped["red"]["alternate_hrefs"]["s3"] == "s3://sentinel-cogs/S2A_001/red.tif"
