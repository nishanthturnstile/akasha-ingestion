from __future__ import annotations

from datetime import date

import httpx

from akasha.config import RuntimeBackend, Settings
from akasha.processing.landsat import LANDSAT_REQUIRED_ASSETS
from akasha.providers.contracts import ProviderSearchRequest
from akasha.providers.planetary_computer import PlanetaryComputerLandsatProvider


def test_search_filters_landsat_8_9_tier1_and_normalizes_assets() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"features": [_item()], "links": []})

    provider = _provider(handler)
    items = provider.search(_request())

    assert len(items) == 1
    assert items[0].provider_adapter == "planetary-computer"
    assert items[0].platform == "landsat-9"
    assert items[0].assets["red"].scale == 0.0000275
    assert items[0].assets["red"].offset == -0.2
    assert items[0].assets["red"].spatial_resolution == 30
    assert items[0].assets["red"].selected_access_mode == "signed_https"
    body = requests[0].read().decode()
    assert '"landsat-8"' in body
    assert '"landsat-9"' in body
    assert '"T1"' in body


def test_sign_asset_strips_old_query_and_caches_token() -> None:
    token_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if "/token/" in request.url.path:
            token_calls += 1
            return httpx.Response(
                200,
                json={
                    "token": "se=future&sig=secret",
                    "msft:expiry": "2099-07-20T00:00:00Z",
                },
            )
        return httpx.Response(200, json={"features": [_item()], "links": []})

    provider = _provider(handler)
    asset = provider.search(_request())[0].assets["red"]
    signed_first = provider.signed_href(asset)
    signed_second = provider.signed_href(asset)

    assert signed_first.endswith("?se=future&sig=secret")
    assert signed_second == signed_first
    assert token_calls == 1
    assert asset.href.endswith("_SR_B4.TIF")
    assert "sig=" not in asset.href


def test_pagination_honors_max_items() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json={
                    "features": [_item()],
                    "links": [
                        {
                            "rel": "next",
                            "href": "https://planetary.test/api/stac/v1/search",
                            "method": "POST",
                            "body": {"page": 2},
                        }
                    ],
                },
            )
        second = _item()
        second["id"] = "LC08_L2SP_143052_20260629_02_T1"
        second["properties"]["platform"] = "landsat-8"
        return httpx.Response(200, json={"features": [second], "links": []})

    provider = _provider(handler)
    request = _request(max_items=2)
    items = provider.search(request)

    assert calls == 2
    assert len(items) == 2


def _provider(handler) -> PlanetaryComputerLandsatProvider:  # type: ignore[no-untyped-def]
    settings = Settings(
        runtime_backend=RuntimeBackend.MEMORY,
        planetary_computer_api_url="https://planetary.test/api/stac/v1",
        planetary_computer_sas_url="https://planetary.test/api/sas/v1",
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return PlanetaryComputerLandsatProvider(settings, client=client)


def _request(*, max_items: int | None = None) -> ProviderSearchRequest:
    return ProviderSearchRequest(
        source_id="landsat-c2-l2",
        provider_collection="landsat-c2-l2",
        date_start=date(2026, 6, 1),
        date_end=date(2026, 7, 18),
        bbox=[77.0, 12.5, 78.0, 13.5],
        max_cloud_percentage=80,
        required_assets=LANDSAT_REQUIRED_ASSETS,
        max_items=max_items,
    )


def _item() -> dict:
    item_id = "LC09_L2SP_143052_20260707_02_T1"
    assets = {
        asset_key: _asset(asset_key, item_id)
        for asset_key in LANDSAT_REQUIRED_ASSETS
    }
    return {
        "type": "Feature",
        "id": item_id,
        "collection": "landsat-c2-l2",
        "bbox": [77.0, 12.5, 78.0, 13.5],
        "geometry": {"type": "Polygon", "coordinates": []},
        "properties": {
            "datetime": "2026-07-07T05:04:36Z",
            "platform": "landsat-9",
            "constellation": "landsat",
            "instruments": ["oli", "tirs"],
            "eo:cloud_cover": 33.8,
            "landsat:collection_number": "02",
            "landsat:collection_category": "T1",
            "landsat:correction": "L2SP",
            "landsat:wrs_path": "143",
            "landsat:wrs_row": "052",
        },
        "assets": assets,
    }


def _asset(asset_key: str, item_id: str) -> dict:
    is_qa = asset_key in {"qa_pixel", "qa_radsat"}
    suffix = {
        "blue": "SR_B2",
        "green": "SR_B3",
        "red": "SR_B4",
        "nir08": "SR_B5",
        "swir16": "SR_B6",
        "swir22": "SR_B7",
        "qa_pixel": "QA_PIXEL",
        "qa_radsat": "QA_RADSAT",
    }[asset_key]
    raster_band = {
        "nodata": 1 if is_qa else 0,
        "data_type": "uint16",
        "spatial_resolution": 30,
    }
    if not is_qa:
        raster_band.update({"scale": 0.0000275, "offset": -0.2})
    return {
        "href": f"https://landsat.test/{item_id}_{suffix}.TIF?old=expired",
        "type": "image/tiff; application=geotiff; profile=cloud-optimized",
        "roles": ["data"],
        "eo:bands": [] if is_qa else [{"common_name": asset_key}],
        "raster:bands": [raster_band],
    }
