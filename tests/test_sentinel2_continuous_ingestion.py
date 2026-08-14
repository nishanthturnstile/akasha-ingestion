from __future__ import annotations

from datetime import date, timedelta

import httpx

from akasha.catalog.sync_ledger_repository import (
    InMemorySyncLedgerRepository,
    SyncLedgerRecord,
)
from akasha.config import RuntimeBackend, Settings
from akasha.jobs.sentinel2_tasks import _scheduled_date_window
from akasha.providers.contracts import ProviderSearchRequest
from akasha.providers.earthsearch import EarthSearchProvider


def test_sync_ledger_tracks_retry_and_monitoring_state() -> None:
    repository = InMemorySyncLedgerRepository()
    repository.upsert(
        SyncLedgerRecord(
            source_id="sentinel-2-l2a",
            aoi_id="aoi",
            provider_date=date(2026, 1, 1),
            status="complete",
            scene_count=2,
            processed_count=2,
            search_complete=True,
        )
    )
    repository.upsert(
        SyncLedgerRecord(
            source_id="sentinel-2-l2a",
            aoi_id="aoi",
            provider_date=date(2026, 1, 2),
            status="retry",
            scene_count=1,
            retry_count=2,
            last_error="provider unavailable",
        )
    )

    assert repository.latest_fully_searched_day(
        source_id="sentinel-2-l2a", aoi_id="aoi"
    ) == date(2026, 1, 1)
    assert repository.incomplete_count(source_id="sentinel-2-l2a", aoi_id="aoi") == 1
    assert repository.processing_backlog(source_id="sentinel-2-l2a", aoi_id="aoi") == 1
    assert repository.last_error(source_id="sentinel-2-l2a", aoi_id="aoi") == "provider unavailable"


def test_scheduler_seeds_missing_180_days_and_late_overlap() -> None:
    settings = Settings(
        runtime_backend=RuntimeBackend.MEMORY,
        sentinel2_preload_date_window_days=180,
        sentinel2_preload_refresh_days=7,
    )
    end = date(2026, 7, 13)
    records = [
        SyncLedgerRecord("sentinel-2-l2a", "aoi", end - timedelta(days=1), status="complete")
    ]

    assert _scheduled_date_window(settings, end_date=end, ledger_records=records) == (
        end - timedelta(days=179),
        end,
    )

    complete = [
        SyncLedgerRecord(
            "sentinel-2-l2a",
            "aoi",
            end - timedelta(days=offset),
            status="complete",
        )
        for offset in range(180)
    ]
    assert _scheduled_date_window(settings, end_date=end, ledger_records=complete) == (
        end - timedelta(days=6),
        end,
    )


def test_provider_search_reports_cap_truncation_without_cloud_filter() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "features": [_item("S2A_001"), _item("S2A_002")],
                "links": [
                    {
                        "rel": "next",
                        "href": "https://earth-search.test/v1/search?page=2",
                    }
                ],
            },
        )

    settings = Settings(
        runtime_backend=RuntimeBackend.MEMORY,
        earthsearch_api_url="https://earth-search.test/v1",
        provider_retry_backoff_seconds=0,
    )
    provider = EarthSearchProvider(
        settings,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = provider.search_with_status(
        ProviderSearchRequest(
            source_id="sentinel-2-l2a",
            provider_collection="sentinel-2-l2a",
            date_start=date(2026, 1, 1),
            date_end=date(2026, 1, 1),
            bbox=[77, 12, 78, 13],
            max_items=1,
        )
    )

    assert result.truncated is True
    assert result.exhausted is False
    assert len(result.items) == 1
    payload = calls[0].content.decode()
    assert "eo:cloud_cover" not in payload


def test_provider_search_reports_cap_truncation_with_more_items_on_same_page() -> None:
    settings = Settings(
        runtime_backend=RuntimeBackend.MEMORY,
        earthsearch_api_url="https://earth-search.test/v1",
        provider_retry_backoff_seconds=0,
    )
    provider = EarthSearchProvider(
        settings,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={
                        "features": [_item("S2A_001"), _item("S2A_002")],
                        "links": [],
                    },
                )
            )
        ),
    )

    result = provider.search_with_status(
        ProviderSearchRequest(
            source_id="sentinel-2-l2a",
            provider_collection="sentinel-2-l2a",
            date_start=date(2026, 1, 1),
            date_end=date(2026, 1, 1),
            bbox=[77, 12, 78, 13],
            max_items=1,
        )
    )

    assert result.truncated is True
    assert result.exhausted is False
    assert [item.stac_item_id for item in result.items] == ["S2A_001"]


def test_provider_search_retries_rate_limit_with_bounded_backoff() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429)
        return httpx.Response(200, json={"features": [], "links": []})

    settings = Settings(
        runtime_backend=RuntimeBackend.MEMORY,
        earthsearch_api_url="https://earth-search.test/v1",
        provider_retry_attempts=3,
        provider_retry_backoff_seconds=0,
    )
    provider = EarthSearchProvider(
        settings,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = provider.search_with_status(
        ProviderSearchRequest(
            source_id="sentinel-2-l2a",
            provider_collection="sentinel-2-l2a",
            date_start=date(2026, 1, 1),
            date_end=date(2026, 1, 1),
            bbox=[77, 12, 78, 13],
        )
    )

    assert result.items == []
    assert calls == 2


def _item(item_id: str) -> dict:
    return {
        "type": "Feature",
        "id": item_id,
        "collection": "sentinel-2-l2a",
        "bbox": [77, 12, 78, 13],
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[77, 12], [78, 12], [78, 13], [77, 12]]],
        },
        "properties": {
            "datetime": "2026-01-01T05:00:00Z",
            "eo:cloud_cover": 95,
        },
        "assets": {
            key: {"href": f"https://earth-search.test/{item_id}/{key}.tif"}
            for key in ("red", "nir", "scl")
        },
    }
