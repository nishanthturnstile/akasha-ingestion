from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class ProviderErrorCategory(StrEnum):
    PROVIDER_SLA_UNAVAILABLE = "provider_sla_unavailable"
    SOURCE_RATE_LIMITED = "source_rate_limited"
    STAC_SEARCH_FAILED = "stac_search_failed"
    ASSET_UNAVAILABLE = "asset_unavailable"
    ASSET_METADATA_INVALID = "asset_metadata_invalid"
    EXTERNAL_ASSET_READ_FAILED = "external_asset_read_failed"
    DOWNLOAD_FAILED = "download_failed"
    INVALID_PRODUCT = "invalid_product"
    PREPARE_FAILED = "prepare_failed"
    METADATA_FAILED = "metadata_failed"


class ProviderDataError(RuntimeError):
    def __init__(self, category: ProviderErrorCategory, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True, slots=True)
class ProviderSearchRequest:
    source_id: str
    provider_collection: str
    date_start: date
    date_end: date
    intersects: dict[str, Any] | None = None
    bbox: list[float] | None = None
    max_cloud_percentage: float | None = None
    required_assets: tuple[str, ...] = ()
    max_items: int | None = None


@dataclass(frozen=True, slots=True)
class ProviderSearchResult:
    """A paginated search plus an explicit completeness signal.

    Providers historically returned a bare list.  Keeping ``search`` compatible while
    exposing this result lets ingestion distinguish an empty day from a safety-cap
    truncation.
    """

    items: list[NormalizedStacItem]
    exhausted: bool = True
    pages: int = 0
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class NormalizedAsset:
    asset_key: str
    href: str
    alternate_hrefs: dict[str, str] = field(default_factory=dict)
    media_type: str | None = None
    roles: list[str] = field(default_factory=list)
    band_common_name: str | None = None
    scale: float | None = None
    offset: float = 0.0
    nodata: float | int | None = None
    spatial_resolution: float | None = None
    storage_backend: str = "https"
    selected_access_mode: str = "public_https"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NormalizedStacItem:
    provider_adapter: str
    provider_collection: str
    source_id: str
    stac_item_id: str
    logical_scene_key: str
    acquisition_at: datetime | None
    platform: str | None
    constellation: str | None
    instrument: str | None
    mgrs_tile: str | None
    footprint: dict[str, Any] | None
    bbox: list[float]
    cloud_percent: float | None
    assets: dict[str, NormalizedAsset]
    raw_item: dict[str, Any]
