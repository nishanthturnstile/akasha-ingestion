from __future__ import annotations

from typing import Any

from akasha.providers.contracts import NormalizedAsset, NormalizedStacItem


def build_asset_manifest(item: NormalizedStacItem) -> dict[str, Any]:
    return {
        "schema_version": "phase2-asset-manifest-v1",
        "provider_adapter": item.provider_adapter,
        "provider_collection": item.provider_collection,
        "source_id": item.source_id,
        "stac_item_id": item.stac_item_id,
        "logical_scene_key": item.logical_scene_key,
        "acquisition_at": item.acquisition_at.isoformat() if item.acquisition_at else None,
        "mgrs_tile": item.mgrs_tile,
        "cloud_percent": item.cloud_percent,
        "assets": [
            {
                "asset_key": asset.asset_key,
                "href": asset.href,
                "alternate_hrefs": asset.alternate_hrefs,
                "media_type": asset.media_type,
                "roles": asset.roles,
                "band_common_name": asset.band_common_name,
                "scale": asset.scale,
                "offset": asset.offset,
                "nodata": asset.nodata,
                "spatial_resolution": asset.spatial_resolution,
                "storage_backend": asset.storage_backend,
                "selected_access_mode": asset.selected_access_mode,
            }
            for asset in sorted(item.assets.values(), key=lambda value: value.asset_key)
        ],
    }


def manifest_asset_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    assets = manifest.get("assets", [])
    if not isinstance(assets, list):
        raise ValueError("asset manifest assets must be a list")
    mapped: dict[str, dict[str, Any]] = {}
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("asset_key"), str):
            raise ValueError("asset manifest entries require asset_key")
        mapped[asset["asset_key"]] = asset
    return mapped


def asset_manifest_entry(asset: NormalizedAsset) -> dict[str, Any]:
    return {
        "asset_key": asset.asset_key,
        "href": asset.href,
        "alternate_hrefs": asset.alternate_hrefs,
        "media_type": asset.media_type,
        "roles": asset.roles,
        "band_common_name": asset.band_common_name,
        "scale": asset.scale,
        "offset": asset.offset,
        "nodata": asset.nodata,
        "spatial_resolution": asset.spatial_resolution,
        "storage_backend": asset.storage_backend,
        "selected_access_mode": asset.selected_access_mode,
    }
