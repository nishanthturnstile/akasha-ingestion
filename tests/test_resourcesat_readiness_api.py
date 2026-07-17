from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

from akasha.api.app import create_app
from akasha.catalog.raster_repository import RasterOutputRecord
from akasha.catalog.scene_repository import ProviderSceneRecord
from akasha.config import Environment, RuntimeBackend, Settings
from akasha.processing.resourcesat import RESOURCESAT_LISS3_BOA_SOURCE_ID
from akasha.security import hash_api_key
from akasha.services.resourcesat_outputs import RESOURCESAT_COMPOSITE_OUTPUT_KIND

API_KEY = "test-akasha-key"
AOI_ID = "bangalore_60km_geodesic_aoi"
SOURCE_ID = RESOURCESAT_LISS3_BOA_SOURCE_ID
PROVIDER_ROUTE = "bhoonidhi:ResourceSat-2A_LISS3_BOA"


def test_resourcesat_readiness_is_disabled_by_default() -> None:
    app = _app(readiness_enabled=False)
    client = TestClient(app)

    response = client.get(_path(), headers=_headers())

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "UNAVAILABLE"
    assert data["reasonCode"] == "SOURCE_NOT_ENABLED"
    assert data["providerRoute"] == PROVIDER_ROUTE


def test_resourcesat_readiness_reports_fresh_composite_outputs() -> None:
    app = _app(readiness_enabled=True, freshness_hours=100_000)
    completed_at = datetime(2026, 3, 20, 2, 30, tzinfo=UTC)
    _seed_successful_resourcesat_job(app, completed_at=completed_at, job_id="job_rs_fresh")
    _seed_resourcesat_scene_and_raster(
        app,
        scene_date=date(2026, 3, 19),
        index_name="ndvi",
        coverage_percentage=98.0,
        created_at=completed_at,
    )
    client = TestClient(app)

    response = client.get(_path(), headers=_headers())

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "AVAILABLE"
    assert data["providerRoute"] == PROVIDER_ROUTE
    assert data["availableDates"] == ["2026-03-19"]
    assert data["indexCoverage"] == {
        "NDVI": {"available": True, "dateCount": 1, "coveragePercent": 100.0}
    }
    assert data["lastSuccessfulJob"]["jobId"] == "job_rs_fresh"
    assert data["reasonCode"] is None


def test_resourcesat_readiness_reports_stale_outputs() -> None:
    old_at = datetime.now(UTC) - timedelta(days=30)
    app = _app(readiness_enabled=True, freshness_hours=1)
    _seed_successful_resourcesat_job(app, completed_at=old_at, job_id="job_rs_old")
    _seed_resourcesat_scene_and_raster(
        app,
        scene_date=date(2026, 3, 19),
        index_name="ndvi",
        coverage_percentage=98.0,
        created_at=old_at,
    )
    client = TestClient(app)

    response = client.get(_path(), headers=_headers())

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "STALE"
    assert data["reasonCode"] == "RESOURCE_SAT_STALE"


def test_resourcesat_readiness_reports_missing_outputs_when_job_exists() -> None:
    app = _app(readiness_enabled=True)
    _seed_successful_resourcesat_job(
        app,
        completed_at=datetime(2026, 3, 20, 2, 30, tzinfo=UTC),
        job_id="job_rs_no_outputs",
    )
    client = TestClient(app)

    response = client.get(_path(), headers=_headers())

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "UNAVAILABLE"
    assert data["reasonCode"] == "NO_RESOURCE_SAT_OUTPUTS"


def test_resourcesat_readiness_requires_successful_job() -> None:
    app = _app(readiness_enabled=True)
    _seed_resourcesat_scene_and_raster(
        app,
        scene_date=date(2026, 3, 19),
        index_name="ndvi",
        coverage_percentage=98.0,
    )
    client = TestClient(app)

    response = client.get(_path(), headers=_headers())

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "UNAVAILABLE"
    assert data["reasonCode"] == "NO_SUCCESSFUL_RESOURCE_SAT_JOB"


def test_resourcesat_readiness_reports_missing_required_index() -> None:
    app = _app(readiness_enabled=True)
    _seed_successful_resourcesat_job(
        app,
        completed_at=datetime(2026, 3, 20, 2, 30, tzinfo=UTC),
        job_id="job_rs_missing_index",
    )
    _seed_resourcesat_scene_and_raster(
        app,
        scene_date=date(2026, 3, 19),
        index_name="ndmi",
        coverage_percentage=98.0,
    )
    client = TestClient(app)

    response = client.get(_path(), headers=_headers())

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "UNAVAILABLE"
    assert data["reasonCode"] == "MISSING_INDEX_COVERAGE"


def test_resourcesat_readiness_keeps_partial_output_as_processed_candidate() -> None:
    app = _app(readiness_enabled=True)
    _seed_successful_resourcesat_job(
        app,
        completed_at=datetime(2026, 3, 20, 2, 30, tzinfo=UTC),
        job_id="job_rs_low_coverage",
    )
    _seed_resourcesat_scene_and_raster(
        app,
        scene_date=date(2026, 3, 19),
        index_name="ndvi",
        coverage_percentage=50.0,
    )
    client = TestClient(app)

    response = client.get(_path(), headers=_headers())

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "AVAILABLE"
    assert data["availableDates"] == ["2026-03-19"]
    assert data["reasonCode"] is None


def test_resourcesat_readiness_reports_aoi_and_source_mismatch() -> None:
    app = _app(readiness_enabled=True)
    client = TestClient(app)

    aoi_response = client.get(_path(aoi_id="other-aoi"), headers=_headers())
    source_response = client.get(_path(source_id="unknown-source"), headers=_headers())

    assert aoi_response.status_code == 200
    assert aoi_response.json()["data"]["reasonCode"] == "AOI_MISMATCH"
    assert source_response.status_code == 200
    assert source_response.json()["data"]["reasonCode"] == "SOURCE_MISMATCH"


def test_sentinel_readiness_still_reports_ndvi_only_index_coverage() -> None:
    app = create_app(
        Settings(
            environment=Environment.TEST,
            runtime_backend=RuntimeBackend.MEMORY,
            api_key_hashes=f"test:{hash_api_key(API_KEY)}",
        )
    )
    client = TestClient(app)

    response = client.get(
        f"/api/v1/analytics/readiness?sourceId=sentinel-2-l2a&aoiId={AOI_ID}",
        headers=_headers(),
    )

    assert response.status_code == 200
    assert list(response.json()["data"]["indexCoverage"]) == ["NDVI"]


def _app(*, readiness_enabled: bool = True, freshness_hours: int = 336):
    return create_app(
        Settings(
            environment=Environment.TEST,
            runtime_backend=RuntimeBackend.MEMORY,
            api_key_hashes=f"test:{hash_api_key(API_KEY)}",
            resourcesat_liss3_readiness_enabled=readiness_enabled,
            resourcesat_liss3_preload_freshness_max_age_hours=freshness_hours,
        )
    )


def _seed_successful_resourcesat_job(
    app,
    *,
    completed_at: datetime,
    job_id: str,
    processed_count: int = 1,
    failed_count: int = 0,
) -> None:
    job, _ = app.state.job_store.create_or_get(
        job_type="resourcesat_backfill",
        idempotency_key=f"resourcesat-{job_id}",
        source_id=SOURCE_ID,
        aoi_id=AOI_ID,
        date_start="2026-03-01",
        date_end="2026-03-31",
    )
    completed = app.state.job_store.mark_completed(
        job,
        result_metadata={
            "mode": "full_pipeline",
            "backfill_summary": {
                "processed_count": processed_count,
                "failed_count": failed_count,
            },
        },
    )
    completed.job_id = job_id
    completed.completed_at = completed_at
    completed.updated_at = completed_at


def _seed_resourcesat_scene_and_raster(
    app,
    *,
    scene_date: date,
    index_name: str,
    coverage_percentage: float,
    created_at: datetime | None = None,
) -> None:
    scene = app.state.scene_repository.upsert(
        ProviderSceneRecord(
            id=None,
            provider_adapter="bhoonidhi",
            source_id=SOURCE_ID,
            provider_product_id=f"{SOURCE_ID}:composite:{AOI_ID}:{scene_date.isoformat()}",
            acquisition_at=datetime.combine(scene_date, datetime.min.time(), tzinfo=UTC),
            status="composited",
            cloud_percent=4.2,
            aoi_id=AOI_ID,
            coverage_percentage=coverage_percentage,
            provider_metadata={
                "output_kind": RESOURCESAT_COMPOSITE_OUTPUT_KIND,
                "provider_collection": "ResourceSat-2A_LISS3_BOA",
            },
        )
    )
    output = app.state.raster_repository.upsert_derived_index(
        RasterOutputRecord(
            id=None,
            scene_id=scene.id or "",
            output_kind="derived_index",
            index_name=index_name,
            object_path=f"indices/bhoonidhi/{SOURCE_ID}/{scene.provider_product_id}/{index_name}.tif",
            formula_version=f"{index_name}-resourcesat-v1",
            processing_profile_version="resourcesat-liss3-boa-processing-v1",
            processing_resolution=23.5,
        )
    )
    if created_at is not None:
        output.created_at = created_at


def _headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


def _path(*, source_id: str = SOURCE_ID, aoi_id: str = AOI_ID) -> str:
    return f"/api/v1/analytics/readiness?sourceId={source_id}&aoiId={aoi_id}"
