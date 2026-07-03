from __future__ import annotations

from datetime import date

import httpx
import pytest

from akasha.config import RuntimeBackend, Settings
from akasha.providers.contracts import (
    ProviderDataError,
    ProviderErrorCategory,
    ProviderSearchRequest,
)
from akasha.providers.earthsearch import EarthSearchProvider


def test_earthsearch_search_normalizes_paginated_items() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json={
                    "features": [_stac_item("S2A_001")],
                    "links": [
                        {
                            "rel": "next",
                            "href": "https://earth-search.test/v1/search",
                            "method": "POST",
                            "body": {"page": 2},
                        }
                    ],
                },
            )
        return httpx.Response(200, json={"features": [_stac_item("S2A_002")], "links": []})

    provider = _provider(handler)

    items = provider.search(
        ProviderSearchRequest(
            source_id="sentinel-2-l2a",
            provider_collection="sentinel-2-l2a",
            date_start=date(2026, 1, 1),
            date_end=date(2026, 1, 31),
            intersects=_polygon(),
            required_assets=("red", "nir", "scl"),
        )
    )

    assert calls == 2
    assert [item.stac_item_id for item in items] == ["S2A_001", "S2A_002"]
    assert items[0].logical_scene_key == "sentinel-2-l2a:S2A_001"
    assert items[0].mgrs_tile == "T43PHQ"
    assert items[0].cloud_percent == 4.2
    assert items[0].assets["red"].scale == 0.0001
    assert items[0].assets["red"].offset == -0.1
    assert items[0].assets["red"].alternate_hrefs["s3"] == "s3://sentinel-cogs/S2A_001/red.tif"
    assert items[0].assets["red"].selected_access_mode == "public_https"


def test_earthsearch_grid_code_normalizes_to_canonical_mgrs_tile() -> None:
    item = _stac_item("S2A_001")
    item["properties"]["grid:code"] = "MGRS-43PHQ"

    provider = _provider(lambda _: httpx.Response(200, json={"features": [item], "links": []}))

    items = provider.search(
        ProviderSearchRequest(
            source_id="sentinel-2-l2a",
            provider_collection="sentinel-2-l2a",
            date_start=date(2026, 1, 1),
            date_end=date(2026, 1, 31),
            intersects=_polygon(),
        )
    )

    assert items[0].mgrs_tile == "T43PHQ"


def test_earthsearch_missing_required_asset_is_categorized() -> None:
    provider = _provider(lambda _: httpx.Response(200, json={"features": [_stac_item("S2A_001")]}))

    with pytest.raises(ProviderDataError) as exc_info:
        provider.search(
            ProviderSearchRequest(
                source_id="sentinel-2-l2a",
                provider_collection="sentinel-2-l2a",
                date_start=date(2026, 1, 1),
                date_end=date(2026, 1, 31),
                intersects=_polygon(),
                required_assets=("red", "nir", "missing"),
            )
        )

    assert exc_info.value.category == ProviderErrorCategory.ASSET_UNAVAILABLE


def test_earthsearch_rate_limit_is_categorized() -> None:
    provider = _provider(lambda _: httpx.Response(429, json={"detail": "too many requests"}))

    with pytest.raises(ProviderDataError) as exc_info:
        provider.search(
            ProviderSearchRequest(
                source_id="sentinel-2-l2a",
                provider_collection="sentinel-2-l2a",
                date_start=date(2026, 1, 1),
                date_end=date(2026, 1, 31),
                bbox=[77.0, 12.5, 78.0, 13.5],
            )
        )

    assert exc_info.value.category == ProviderErrorCategory.SOURCE_RATE_LIMITED


def _provider(handler) -> EarthSearchProvider:  # type: ignore[no-untyped-def]
    settings = Settings(
        runtime_backend=RuntimeBackend.MEMORY,
        earthsearch_api_url="https://earth-search.test/v1",
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return EarthSearchProvider(settings, client=client)


def _stac_item(item_id: str) -> dict:
    return {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": item_id,
        "collection": "sentinel-2-l2a",
        "bbox": [77.0, 12.5, 78.0, 13.5],
        "geometry": _polygon(),
        "properties": {
            "datetime": "2026-01-15T05:30:00Z",
            "platform": "sentinel-2a",
            "constellation": "sentinel-2",
            "instruments": ["msi"],
            "eo:cloud_cover": 4.2,
            "mgrs:utm_zone": 43,
            "mgrs:latitude_band": "P",
            "mgrs:grid_square": "HQ",
        },
        "assets": {
            "red": _asset(item_id, "red", "red", scale=0.0001, offset=-0.1, nodata=0),
            "nir": _asset(item_id, "nir", "nir", scale=0.0001, offset=0.0, nodata=0),
            "scl": _asset(item_id, "scl", None, scale=None, offset=None, nodata=0),
        },
    }


def _asset(
    item_id: str,
    asset_key: str,
    common_name: str | None,
    *,
    scale: float | None,
    offset: float | None,
    nodata: int,
) -> dict:
    asset = {
        "href": f"https://earth-search.test/{item_id}/{asset_key}.tif",
        "type": "image/tiff; application=geotiff; profile=cloud-optimized",
        "roles": ["data"],
        "gsd": 10,
        "alternate": {"s3": {"href": f"s3://sentinel-cogs/{item_id}/{asset_key}.tif"}},
        "raster:bands": [{"nodata": nodata}],
    }
    if common_name is not None:
        asset["eo:bands"] = [{"common_name": common_name}]
    if scale is not None:
        asset["raster:bands"][0]["scale"] = scale
    if offset is not None:
        asset["raster:bands"][0]["offset"] = offset
    return asset


def _polygon() -> dict:
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [77.0, 12.5],
                [78.0, 12.5],
                [78.0, 13.5],
                [77.0, 13.5],
                [77.0, 12.5],
            ]
        ],
    }
