from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from akasha.config import Settings
from akasha.providers.contracts import (
    NormalizedAsset,
    NormalizedStacItem,
    ProviderDataError,
    ProviderErrorCategory,
    ProviderSearchRequest,
)


class EarthSearchProvider:
    provider_adapter = "earthsearch"

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_url = settings.earthsearch_api_url.rstrip("/")
        self._timeout = settings.earthsearch_timeout_seconds
        self._page_size = settings.earthsearch_page_size
        self._client = client or httpx.Client(timeout=self._timeout)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def validate_root(self) -> bool:
        response = self._request("GET", self._api_url)
        payload = response.json()
        collections_link = _find_link(payload.get("links", []), "data")
        return payload.get("stac_version") is not None and collections_link is not None

    def search(self, request: ProviderSearchRequest) -> list[NormalizedStacItem]:
        if request.intersects is None and request.bbox is None:
            raise ValueError("Earth Search requests require intersects geometry or bbox")

        payload: dict[str, Any] = {
            "collections": [request.provider_collection],
            "datetime": _datetime_range(request),
            "limit": self._page_size,
        }
        if request.intersects is not None:
            payload["intersects"] = request.intersects
        else:
            payload["bbox"] = request.bbox

        if request.max_cloud_percentage is not None:
            payload["query"] = {"eo:cloud_cover": {"lte": request.max_cloud_percentage}}

        items: list[NormalizedStacItem] = []
        response_payload = self._post_search(payload)
        while True:
            for raw_item in response_payload.get("features", []):
                item = self.normalize_item(
                    raw_item,
                    source_id=request.source_id,
                    provider_collection=request.provider_collection,
                    required_assets=request.required_assets,
                )
                items.append(item)
                if request.max_items is not None and len(items) >= request.max_items:
                    return items

            next_link = _find_link(response_payload.get("links", []), "next")
            if next_link is None:
                return items
            response_payload = self._request_next(next_link)

    def normalize_item(
        self,
        item: dict[str, Any],
        *,
        source_id: str,
        provider_collection: str,
        required_assets: tuple[str, ...] = (),
    ) -> NormalizedStacItem:
        item_id = _required_str(item, "id")
        collection = str(item.get("collection") or provider_collection)
        properties = _dict(item.get("properties"))
        assets = {
            asset_key: self.normalize_asset(asset_key, _dict(asset))
            for asset_key, asset in _dict(item.get("assets")).items()
        }
        missing_assets = sorted(set(required_assets) - set(assets))
        if missing_assets:
            raise ProviderDataError(
                ProviderErrorCategory.ASSET_UNAVAILABLE,
                f"STAC item {item_id} missing required assets: {', '.join(missing_assets)}",
            )

        acquisition_at = _parse_datetime(properties.get("datetime"))
        return NormalizedStacItem(
            provider_adapter=self.provider_adapter,
            provider_collection=collection,
            source_id=source_id,
            stac_item_id=item_id,
            logical_scene_key=f"{collection}:{item_id}",
            acquisition_at=acquisition_at,
            platform=_optional_str(properties.get("platform")),
            constellation=_optional_str(properties.get("constellation")),
            instrument=_instrument(properties),
            mgrs_tile=_mgrs_tile(properties),
            footprint=_dict_or_none(item.get("geometry")),
            bbox=[float(value) for value in item.get("bbox", [])],
            cloud_percent=_optional_float(properties.get("eo:cloud_cover")),
            assets=assets,
            raw_item=item,
        )

    def normalize_asset(self, asset_key: str, asset: dict[str, Any]) -> NormalizedAsset:
        href = _required_str(asset, "href")
        raster_band = _first_dict(asset.get("raster:bands"))
        band = _first_dict(asset.get("eo:bands"))
        scale = _optional_float(raster_band.get("scale"))
        offset = _optional_float(raster_band.get("offset"))
        nodata = raster_band.get("nodata", asset.get("nodata"))
        storage_backend, selected_access_mode = _storage_for_href(href)

        return NormalizedAsset(
            asset_key=asset_key,
            href=href,
            alternate_hrefs=_alternate_hrefs(asset),
            media_type=_optional_str(asset.get("type")),
            roles=[str(role) for role in asset.get("roles", [])],
            band_common_name=_optional_str(band.get("common_name") or band.get("name")),
            scale=scale,
            offset=offset if offset is not None else 0.0,
            nodata=nodata,
            spatial_resolution=_optional_float(asset.get("gsd")),
            storage_backend=storage_backend,
            selected_access_mode=selected_access_mode,
            metadata={
                "title": asset.get("title"),
                "description": asset.get("description"),
            },
        )

    def _post_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._request("POST", f"{self._api_url}/search", json=payload)
        return _dict(response.json())

    def _request_next(self, link: dict[str, Any]) -> dict[str, Any]:
        href = _required_str(link, "href")
        method = str(link.get("method", "GET")).upper()
        if method == "POST":
            response = self._request("POST", href, json=link.get("body") or {})
        else:
            response = self._request("GET", href)
        return _dict(response.json())

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._client.request(method, url, timeout=self._timeout, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            raise _provider_error_from_response(exc.response) from exc
        except httpx.TimeoutException as exc:
            raise ProviderDataError(
                ProviderErrorCategory.PROVIDER_SLA_UNAVAILABLE,
                f"Earth Search request timed out: {url}",
            ) from exc
        except httpx.NetworkError as exc:
            raise ProviderDataError(
                ProviderErrorCategory.PROVIDER_SLA_UNAVAILABLE,
                f"Earth Search network error: {url}",
            ) from exc


def _provider_error_from_response(response: httpx.Response) -> ProviderDataError:
    if response.status_code == 429:
        return ProviderDataError(
            ProviderErrorCategory.SOURCE_RATE_LIMITED,
            "Earth Search request was rate limited",
        )
    if response.status_code >= 500:
        return ProviderDataError(
            ProviderErrorCategory.PROVIDER_SLA_UNAVAILABLE,
            f"Earth Search returned {response.status_code}",
        )
    return ProviderDataError(
        ProviderErrorCategory.STAC_SEARCH_FAILED,
        f"Earth Search returned {response.status_code}",
    )


def _datetime_range(request: ProviderSearchRequest) -> str:
    return (
        f"{request.date_start.isoformat()}T00:00:00Z/"
        f"{request.date_end.isoformat()}T23:59:59Z"
    )


def _find_link(links: list[Any], rel: str) -> dict[str, Any] | None:
    for link in links:
        if isinstance(link, dict) and link.get("rel") == rel:
            return link
    return None


def _alternate_hrefs(asset: dict[str, Any]) -> dict[str, str]:
    alternates: dict[str, str] = {}
    for key, value in _dict(asset.get("alternate")).items():
        if isinstance(value, dict) and isinstance(value.get("href"), str):
            alternates[str(key)] = value["href"]
        elif isinstance(value, str):
            alternates[str(key)] = value
    return alternates


def _storage_for_href(href: str) -> tuple[str, str]:
    if href.startswith("s3://"):
        return "s3", "requester_pays_s3"
    if href.startswith("http://") or href.startswith("https://"):
        return "https", "public_https"
    return "local", "local"


def _mgrs_tile(properties: dict[str, Any]) -> str | None:
    direct = _optional_str(properties.get("s2:mgrs_tile"))
    if direct:
        return direct if direct.startswith("T") else f"T{direct}"

    grid_code = _optional_str(properties.get("grid:code"))
    if grid_code:
        return _normalize_mgrs_tile(grid_code.split("-")[-1])

    zone = properties.get("mgrs:utm_zone")
    latitude_band = _optional_str(properties.get("mgrs:latitude_band"))
    grid_square = _optional_str(properties.get("mgrs:grid_square"))
    if zone is not None and latitude_band and grid_square:
        return f"T{int(zone):02d}{latitude_band}{grid_square}"
    return None


def _normalize_mgrs_tile(value: str) -> str:
    return value if value.startswith("T") else f"T{value}"


def _instrument(properties: dict[str, Any]) -> str | None:
    instruments = properties.get("instruments")
    if isinstance(instruments, list) and instruments:
        return str(instruments[0])
    return _optional_str(properties.get("instrument"))


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProviderDataError(
            ProviderErrorCategory.METADATA_FAILED,
            "STAC item datetime must be a string",
        )
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _required_str(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ProviderDataError(
            ProviderErrorCategory.METADATA_FAILED,
            f"STAC object missing required string field: {key}",
        )
    return value


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dict_or_none(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _first_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return {}
