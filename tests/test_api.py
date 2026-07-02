from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from akasha.api.app import create_app
from akasha.config import Environment, RuntimeBackend, Settings
from akasha.security import hash_api_key


def test_health_is_public() -> None:
    app = create_app(Settings(environment=Environment.TEST, runtime_backend=RuntimeBackend.MEMORY))
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ok"


def test_sources_require_api_key() -> None:
    app = create_app(Settings(environment=Environment.TEST, runtime_backend=RuntimeBackend.MEMORY))
    client = TestClient(app)

    response = client.get("/api/v1/sources")

    assert response.status_code == 503


def test_mock_sync_is_idempotent() -> None:
    api_key = "test-akasha-key"
    app = create_app(
        Settings(
            environment=Environment.TEST,
            runtime_backend=RuntimeBackend.MEMORY,
            task_always_eager=True,
            api_key_hashes=f"test:{hash_api_key(api_key)}",
        )
    )
    client = TestClient(app)
    headers = {"X-API-Key": api_key}
    payload = {
        "source_id": "sentinel-2-l2a",
        "aoi_id": "bangalore_60km_geodesic_aoi",
        "date_start": "2026-01-15",
        "date_end": "2026-04-15",
    }

    first = client.post("/api/v1/ingestion/sync", headers=headers, json=payload)
    second = client.post("/api/v1/ingestion/sync", headers=headers, json=payload)

    assert first.status_code == 202
    assert second.status_code == 202
    first_job = first.json()["data"]
    second_job = second.json()["data"]
    assert first_job["job_id"] == second_job["job_id"]
    assert first_job["status"] == "completed"
    assert first_job["checksum_sha256"]
    assert "object_path" not in first_job
    assert first_job["asset_ref"].startswith("asset:")


def test_sync_rejects_reversed_date_range() -> None:
    api_key = "test-akasha-key"
    app = create_app(
        Settings(
            environment=Environment.TEST,
            runtime_backend=RuntimeBackend.MEMORY,
            task_always_eager=True,
            api_key_hashes=f"test:{hash_api_key(api_key)}",
        )
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/ingestion/sync",
        headers={"X-API-Key": api_key},
        json={
            "source_id": "sentinel-2-l2a",
            "aoi_id": "bangalore_60km_geodesic_aoi",
            "date_start": "2026-04-15",
            "date_end": "2026-01-15",
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "422"
    assert "date_end must be on or after date_start" in body["error"]["message"]


def test_openapi_documents_secured_route_errors() -> None:
    app = create_app(Settings(environment=Environment.TEST, runtime_backend=RuntimeBackend.MEMORY))
    client = TestClient(app)

    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "401" in paths["/api/v1/sources"]["get"]["responses"]
    assert "503" in paths["/api/v1/sources"]["get"]["responses"]
    assert "401" in paths["/api/v1/ingestion/sync"]["post"]["responses"]
    assert "422" in paths["/api/v1/ingestion/sync"]["post"]["responses"]
    assert "404" in paths["/api/v1/jobs/{job_id}"]["get"]["responses"]


def test_job_detail_rejects_invalid_uuid_with_error_envelope() -> None:
    api_key = "test-akasha-key"
    app = create_app(
        Settings(
            environment=Environment.TEST,
            runtime_backend=RuntimeBackend.MEMORY,
            api_key_hashes=f"test:{hash_api_key(api_key)}",
        )
    )
    client = TestClient(app)

    response = client.get("/api/v1/jobs/not-a-uuid", headers={"X-API-Key": api_key})

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "422"


def test_job_detail_returns_not_found_for_missing_uuid() -> None:
    api_key = "test-akasha-key"
    app = create_app(
        Settings(
            environment=Environment.TEST,
            runtime_backend=RuntimeBackend.MEMORY,
            api_key_hashes=f"test:{hash_api_key(api_key)}",
        )
    )
    client = TestClient(app)

    response = client.get(f"/api/v1/jobs/{uuid4()}", headers={"X-API-Key": api_key})

    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "404"
