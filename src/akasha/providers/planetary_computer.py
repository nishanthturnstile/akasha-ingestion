from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from akasha.config import Settings
from akasha.processing.landsat import LANDSAT_PROVIDER_COLLECTION
from akasha.providers.contracts import (
    NormalizedAsset,
    NormalizedStacItem,
    ProviderDataError,
    ProviderErrorCategory,
    ProviderSearchRequest,
)


class PlanetaryComputerLandsatProvider:
    provider_adapter = "planetary-computer"

    def __init__(self, settings: Settings, *, client: httpx.Client | None = None) -> None:
        self._api_url = settings.planetary_computer_api_url.rstrip("/")
        self._sas_url = settings.planetary_computer_sas_url.rstrip("/")
        self._timeout = settings.planetary_computer_timeout_seconds
        self._page_size = settings.planetary_computer_page_size
        self._refresh_margin = timedelta(
            seconds=settings.planetary_computer_sas_refresh_margin_seconds
        )
        self._client = client or httpx.Client(timeout=self._timeout)
        self._owns_client = client is None
        self._sas_token: str | None = None
        self._sas_expiry: datetime | None = None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def search(self, request: ProviderSearchRequest) -> list[NormalizedStacItem]:
        if request.provider_collection != LANDSAT_PROVIDER_COLLECTION:
            raise ValueError(
                f"Planetary Computer Landsat provider requires {LANDSAT_PROVIDER_COLLECTION}"
            )
        if request.intersects is None and request.bbox is None:
            raise ValueError("Planetary Computer requests require intersects geometry or bbox")
        payload: dict[str, Any] = {
            "collections": [request.provider_collection],
            "datetime": (
                f"{request.date_start.isoformat()}T00:00:00Z/"
                f"{request.date_end.isoformat()}T23:59:59Z"
            ),
            "limit": self._page_size,
            "query": {
                "platform": {"in": ["landsat-8", "landsat-9"]},
                "landsat:collection_category": {"eq": "T1"},
            },
        }
        if request.intersects is not None:
            payload["intersects"] = request.intersects
        else:
            payload["bbox"] = request.bbox
        if request.max_cloud_percentage is not None:
            payload["query"]["eo:cloud_cover"] = {
                "lte": request.max_cloud_percentage
            }

        items: list[NormalizedStacItem] = []
        response_payload = self._request_json("POST", f"{self._api_url}/search", json=payload)
        while True:
            for raw_item in response_payload.get("features", []):
                item = self.normalize_item(
                    _dict(raw_item),
                    source_id=request.source_id,
                    required_assets=request.required_assets,
                )
                items.append(item)
                if request.max_items is not None and len(items) >= request.max_items:
                    return items
            next_link = _find_link(response_payload.get("links", []), "next")
            if next_link is None:
                return items
            method = str(next_link.get("method", "GET")).upper()
            href = _required_str(next_link, "href")
            response_payload = self._request_json(
                method,
                href,
                json=next_link.get("body") if method == "POST" else None,
            )

    def normalize_item(
        self,
        item: dict[str, Any],
        *,
        source_id: str,
        required_assets: tuple[str, ...] = (),
    ) -> NormalizedStacItem:
        item_id = _required_str(item, "id")
        properties = _dict(item.get("properties"))
        collection = str(item.get("collection") or LANDSAT_PROVIDER_COLLECTION)
        assets = {
            key: self.normalize_asset(key, _dict(value))
            for key, value in _dict(item.get("assets")).items()
        }
        missing = sorted(set(required_assets) - set(assets))
        if missing:
            raise ProviderDataError(
                ProviderErrorCategory.ASSET_UNAVAILABLE,
                f"STAC item {item_id} missing required assets: {', '.join(missing)}",
            )
        platform = _optional_str(properties.get("platform"))
        if platform not in {"landsat-8", "landsat-9"}:
            raise ProviderDataError(
                ProviderErrorCategory.INVALID_PRODUCT,
                f"STAC item {item_id} is not a Landsat 8/9 product",
            )
        acquisition_at = _parse_datetime(properties.get("datetime"))
        return NormalizedStacItem(
            provider_adapter=self.provider_adapter,
            provider_collection=collection,
            source_id=source_id,
            stac_item_id=item_id,
            logical_scene_key=f"{collection}:{item_id}",
            acquisition_at=acquisition_at,
            platform=platform,
            constellation=_optional_str(properties.get("constellation")),
            instrument=_instrument(properties),
            mgrs_tile=None,
            footprint=_dict_or_none(item.get("geometry")),
            bbox=[float(value) for value in item.get("bbox", [])],
            cloud_percent=_optional_float(properties.get("eo:cloud_cover")),
            assets=assets,
            raw_item=item,
        )

    def normalize_asset(self, asset_key: str, asset: dict[str, Any]) -> NormalizedAsset:
        href = _canonical_href(_required_str(asset, "href"))
        raster_band = _first_dict(asset.get("raster:bands"))
        eo_band = _first_dict(asset.get("eo:bands"))
        return NormalizedAsset(
            asset_key=asset_key,
            href=href,
            media_type=_optional_str(asset.get("type")),
            roles=[str(role) for role in asset.get("roles", [])],
            band_common_name=_optional_str(eo_band.get("common_name") or eo_band.get("name")),
            scale=_optional_float(raster_band.get("scale")),
            offset=_optional_float(raster_band.get("offset")) or 0.0,
            nodata=raster_band.get("nodata"),
            spatial_resolution=_optional_float(
                raster_band.get("spatial_resolution") or asset.get("gsd")
            ),
            storage_backend="https",
            selected_access_mode="signed_https",
            metadata={
                "title": asset.get("title"),
                "description": asset.get("description"),
            },
        )

    def signed_href(self, asset: NormalizedAsset) -> str:
        """Return a short-lived read URL without mutating the canonical asset record."""
        token = self._valid_sas_token()
        return f"{_canonical_href(asset.href)}?{token}"

    def _valid_sas_token(self) -> str:
        now = datetime.now(UTC)
        if (
            self._sas_token is not None
            and self._sas_expiry is not None
            and now + self._refresh_margin < self._sas_expiry
        ):
            return self._sas_token
        payload = self._request_json(
            "GET",
            f"{self._sas_url}/token/{LANDSAT_PROVIDER_COLLECTION}",
        )
        token = _required_str(payload, "token")
        expiry = _parse_datetime(payload.get("msft:expiry"))
        if expiry is None or expiry <= now:
            raise ProviderDataError(
                ProviderErrorCategory.PROVIDER_SLA_UNAVAILABLE,
                "Planetary Computer returned an expired SAS token",
            )
        self._sas_token = token
        self._sas_expiry = expiry
        return token

    def _request_json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        if kwargs.get("json") is None:
            kwargs.pop("json", None)
        try:
            response = self._client.request(method, url, timeout=self._timeout, **kwargs)
            response.raise_for_status()
            return _dict(response.json())
        except httpx.HTTPStatusError as exc:
            category = (
                ProviderErrorCategory.SOURCE_RATE_LIMITED
                if exc.response.status_code == 429
                else ProviderErrorCategory.PROVIDER_SLA_UNAVAILABLE
                if exc.response.status_code >= 500
                else ProviderErrorCategory.STAC_SEARCH_FAILED
            )
            raise ProviderDataError(
                category,
                f"Planetary Computer returned HTTP {exc.response.status_code}",
            ) from exc
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ProviderDataError(
                ProviderErrorCategory.PROVIDER_SLA_UNAVAILABLE,
                "Planetary Computer request failed",
            ) from exc


def _canonical_href(href: str) -> str:
    parts = urlsplit(href)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _find_link(links: list[Any], rel: str) -> dict[str, Any] | None:
    return next(
        (link for link in links if isinstance(link, dict) and link.get("rel") == rel),
        None,
    )


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
            "Planetary Computer datetime metadata must be a string",
        )
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _required_str(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ProviderDataError(
            ProviderErrorCategory.METADATA_FAILED,
            f"Planetary Computer response missing {key}",
        )
    return value


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dict_or_none(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _first_dict(value: Any) -> dict[str, Any]:
    return value[0] if isinstance(value, list) and value and isinstance(value[0], dict) else {}
