from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from akasha.api.app import create_app
from akasha.catalog.raster_repository import RasterOutputRecord
from akasha.catalog.scene_repository import ProviderSceneRecord
from akasha.config import Environment, RuntimeBackend, Settings
from akasha.security import hash_api_key

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "contracts"
API_KEY = "test-akasha-key"


def test_readiness_requires_authentication() -> None:
    app = create_app(Settings(environment=Environment.TEST, runtime_backend=RuntimeBackend.MEMORY))
    client = TestClient(app)

    response = client.get(_readiness_path())

    assert response.status_code == 503


def test_readiness_reports_fresh_outputs_from_golden_contract() -> None:
    app = _app(freshness_hours=100_000)
    _seed_successful_preload(
        app,
        completed_at=datetime(2026, 1, 14, 2, 30, tzinfo=UTC),
        job_id="job_01JZ8H",
    )
    client = TestClient(app)

    response = client.get(_readiness_path(), headers=_headers())

    assert response.status_code == 200
    assert response.json() == _fixture("readiness_fresh.json")


def test_readiness_reports_stale_outputs_from_golden_contract() -> None:
    app = _app(freshness_hours=168)
    _seed_successful_preload(
        app,
        completed_at=datetime(2026, 1, 2, 2, 30, tzinfo=UTC),
        job_id="job_01JYOLD",
    )
    client = TestClient(app)

    response = client.get(_readiness_path(), headers=_headers())

    assert response.status_code == 200
    assert response.json() == _fixture("readiness_stale.json")


def test_readiness_reports_missing_successful_job() -> None:
    app = _app()
    _seed_scene_and_raster(app, scene_date=date(2026, 1, 13), index_name="ndvi")
    client = TestClient(app)

    response = client.get(_readiness_path(), headers=_headers())

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "AVAILABLE"
    assert data["lastSuccessfulJob"] is None


def test_readiness_reports_missing_index_coverage_from_golden_contract() -> None:
    app = _app()
    _seed_successful_job(
        app,
        completed_at=datetime(2026, 1, 14, 2, 30, tzinfo=UTC),
        job_id="job_01MISSINDEX",
    )
    _seed_scene_and_raster(app, scene_date=date(2026, 1, 13), index_name="ndmi")
    client = TestClient(app)

    response = client.get(_readiness_path(), headers=_headers())

    assert response.status_code == 200
    assert response.json() == _fixture("readiness_unavailable.json")


def test_readiness_reports_source_mismatch() -> None:
    app = _app()
    client = TestClient(app)

    response = client.get(
        _readiness_path(source_id="landsat-c2-l2"),
        headers=_headers(),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "UNAVAILABLE"
    assert data["reasonCode"] == "SOURCE_MISMATCH"
    assert data["providerRoute"] == "earthsearch:sentinel-2-l2a"


def test_recent_metadata_only_job_does_not_make_old_ndvi_outputs_fresh() -> None:
    app = _app()
    old_output_at = datetime.now(UTC) - timedelta(days=30)
    recent_job_at = datetime.now(UTC) - timedelta(hours=1)
    _seed_scene_and_raster(
        app,
        scene_date=date(2026, 1, 13),
        index_name="ndvi",
        created_at=old_output_at,
    )
    _seed_successful_job(
        app,
        completed_at=recent_job_at,
        job_id="job_recent_metadata",
        mode="metadata_only",
        processed_count=0,
    )
    client = TestClient(app)

    response = client.get(_readiness_path(), headers=_headers())

    data = response.json()["data"]
    assert data["status"] == "STALE"
    assert data["reasonCode"] == "PRELOAD_STALE"
    assert data["latestSuccessfulJobCompletedAt"] is None
    assert data["lastSuccessfulJobAt"] == _format_fixture_time(old_output_at)


def test_recent_mirror_only_job_does_not_make_old_ndvi_outputs_fresh() -> None:
    app = _app()
    old_output_at = datetime.now(UTC) - timedelta(days=30)
    _seed_scene_and_raster(
        app,
        scene_date=date(2026, 1, 13),
        index_name="ndvi",
        created_at=old_output_at,
    )
    _seed_successful_job(
        app,
        completed_at=datetime.now(UTC) - timedelta(hours=1),
        job_id="job_recent_mirror",
        mode="mirror_only",
        processed_count=0,
    )
    client = TestClient(app)

    response = client.get(_readiness_path(), headers=_headers())

    data = response.json()["data"]
    assert data["status"] == "STALE"
    assert data["reasonCode"] == "PRELOAD_STALE"
    assert data["lastSuccessfulJob"] is None


def test_recent_full_pipeline_no_output_job_does_not_make_old_ndvi_outputs_fresh() -> None:
    app = _app()
    old_output_at = datetime.now(UTC) - timedelta(days=30)
    _seed_scene_and_raster(
        app,
        scene_date=date(2026, 1, 13),
        index_name="ndvi",
        created_at=old_output_at,
    )
    _seed_successful_job(
        app,
        completed_at=datetime.now(UTC) - timedelta(hours=1),
        job_id="job_recent_no_output",
        mode="full_pipeline",
        processed_count=0,
    )
    client = TestClient(app)

    response = client.get(_readiness_path(), headers=_headers())

    data = response.json()["data"]
    assert data["status"] == "STALE"
    assert data["reasonCode"] == "PRELOAD_STALE"
    assert data["lastSuccessfulJob"] is None


def test_recent_full_pipeline_with_ndvi_outputs_is_available() -> None:
    app = _app()
    recent_at = datetime.now(UTC) - timedelta(hours=1)
    _seed_scene_and_raster(
        app,
        scene_date=date(2026, 1, 13),
        index_name="ndvi",
        created_at=recent_at,
    )
    _seed_successful_job(
        app,
        completed_at=recent_at,
        job_id="job_recent_full_pipeline",
        mode="full_pipeline",
        processed_count=1,
    )
    client = TestClient(app)

    response = client.get(_readiness_path(), headers=_headers())

    data = response.json()["data"]
    assert data["status"] == "AVAILABLE"
    assert data["reasonCode"] is None
    assert data["latestSuccessfulJobCompletedAt"] == _format_fixture_time(recent_at)
    assert data["lastSuccessfulJob"]["jobId"] == "job_recent_full_pipeline"


def test_error_and_malformed_golden_fixtures_are_valid_json() -> None:
    assert _fixture("readiness_auth_failed.json")["success"] is False
    malformed = _fixture("readiness_malformed.json")
    assert malformed["success"] is True
    assert malformed["data"]["status"] == "AVAILABLE"
    assert "indexCoverage" not in malformed["data"]


def _app(*, freshness_hours: int = 168):
    return create_app(
        Settings(
            environment=Environment.TEST,
            runtime_backend=RuntimeBackend.MEMORY,
            api_key_hashes=f"test:{hash_api_key(API_KEY)}",
            sentinel2_preload_freshness_max_age_hours=freshness_hours,
        )
    )


def _seed_successful_preload(app, *, completed_at: datetime, job_id: str) -> None:
    _seed_successful_job(
        app,
        completed_at=completed_at,
        job_id=job_id,
        mode="full_pipeline",
        processed_count=2,
    )
    _seed_scene_and_raster(
        app,
        scene_date=date(2026, 1, 13),
        index_name="ndvi",
        created_at=completed_at,
    )
    _seed_scene_and_raster(
        app,
        scene_date=date(2026, 1, 6),
        index_name="ndvi",
        created_at=completed_at,
    )


def _seed_successful_job(
    app,
    *,
    completed_at: datetime,
    job_id: str,
    mode: str = "full_pipeline",
    processed_count: int = 2,
    failed_count: int = 0,
) -> None:
    job, _ = app.state.job_store.create_or_get(
        job_type="sentinel2_backfill",
        idempotency_key=f"preload-{completed_at.isoformat()}",
        source_id="sentinel-2-l2a",
        aoi_id="bangalore_60km_geodesic_aoi",
        date_start="2026-01-01",
        date_end="2026-06-30",
    )
    completed = app.state.job_store.mark_completed(
        job,
        result_metadata={
            "mode": mode,
            "backfill_summary": {
                "processed_count": processed_count,
                "failed_count": failed_count,
            },
        },
    )
    completed.job_id = job_id
    completed.completed_at = completed_at
    completed.updated_at = completed_at


def _seed_scene_and_raster(
    app,
    *,
    scene_date: date,
    index_name: str,
    created_at: datetime | None = None,
) -> None:
    scene = app.state.scene_repository.upsert(
        ProviderSceneRecord(
            id=None,
            provider_adapter="earthsearch",
            source_id="sentinel-2-l2a",
            provider_product_id=f"S2A_{scene_date.isoformat()}",
            acquisition_at=datetime.combine(scene_date, datetime.min.time(), tzinfo=UTC),
            status="accepted",
            cloud_percent=4.2,
            aoi_id="bangalore_60km_geodesic_aoi",
            provider_metadata={"provider_route": "earthsearch:sentinel-2-l2a"},
        )
    )
    output = app.state.raster_repository.upsert_derived_index(
        RasterOutputRecord(
            id=None,
            scene_id=scene.id or "",
            output_kind="derived_index",
            index_name=index_name,
            object_path=f"indices/earthsearch/sentinel-2-l2a/{scene.provider_product_id}/{index_name}.tif",
            formula_version=f"{index_name}-s2-v1",
            processing_profile_version="sentinel2-l2a-earthsearch-v1",
            processing_resolution=10,
        )
    )
    if created_at is not None:
        output.created_at = created_at


def _headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


def _readiness_path(
    *,
    source_id: str = "sentinel-2-l2a",
    aoi_id: str = "bangalore_60km_geodesic_aoi",
) -> str:
    return f"/api/v1/analytics/readiness?sourceId={source_id}&aoiId={aoi_id}"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _format_fixture_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
