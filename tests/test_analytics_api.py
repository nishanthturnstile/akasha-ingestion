from __future__ import annotations

from fastapi.testclient import TestClient

from akasha.api.app import create_app
from akasha.config import Environment, RuntimeBackend, Settings
from akasha.security import hash_api_key


def test_field_index_requires_authentication() -> None:
    app = create_app(Settings(environment=Environment.TEST, runtime_backend=RuntimeBackend.MEMORY))
    client = TestClient(app)

    response = client.post("/api/v1/analytics/field-index", json=_payload())

    assert response.status_code == 503


def test_field_index_returns_unavailable_without_leaking_internal_paths() -> None:
    api_key = "test-akasha-key"
    app = create_app(
        Settings(
            environment=Environment.TEST,
            runtime_backend=RuntimeBackend.MEMORY,
            api_key_hashes=f"test:{hash_api_key(api_key)}",
        )
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/analytics/field-index",
        headers={"X-API-Key": api_key},
        json=_payload(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "UNAVAILABLE"
    serialized = str(body)
    assert "s3://" not in serialized
    assert "raw/" not in serialized
    assert "earth-search.aws" not in serialized


def test_field_index_rejects_non_polygon_geometry() -> None:
    api_key = "test-akasha-key"
    app = create_app(
        Settings(
            environment=Environment.TEST,
            runtime_backend=RuntimeBackend.MEMORY,
            api_key_hashes=f"test:{hash_api_key(api_key)}",
        )
    )
    client = TestClient(app)
    payload = _payload()
    payload["geometry"] = {"type": "Point", "coordinates": [77.0, 12.5]}

    response = client.post(
        "/api/v1/analytics/field-index",
        headers={"X-API-Key": api_key},
        json=payload,
    )

    assert response.status_code == 422
    assert "Polygon" in response.json()["error"]["message"]


def test_field_index_rejects_malformed_polygon_coordinates_with_error_envelope() -> None:
    api_key = "test-akasha-key"
    app = create_app(
        Settings(
            environment=Environment.TEST,
            runtime_backend=RuntimeBackend.MEMORY,
            api_key_hashes=f"test:{hash_api_key(api_key)}",
        )
    )
    client = TestClient(app)
    payload = _payload()
    payload["geometry"] = {
        "type": "Polygon",
        "coordinates": [
            [77.60, 12.95],
            [77.61, 12.95],
            [77.61, 12.96],
            [77.60, 12.96],
            [77.60, 12.95],
        ],
    }

    response = client.post(
        "/api/v1/analytics/field-index",
        headers={"X-API-Key": api_key},
        json=payload,
    )

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "422"
    assert "geometry coordinates are invalid" in body["error"]["message"]


def _payload() -> dict:
    return {
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [77.60, 12.95],
                    [77.61, 12.95],
                    [77.61, 12.96],
                    [77.60, 12.96],
                    [77.60, 12.95],
                ]
            ],
        },
        "crs": "EPSG:4326",
        "index": "NDVI",
        "date": "2026-06-30",
        "fallbackPolicy": "nearest_valid_scene",
        "maxCloudPercentage": 20,
    }
