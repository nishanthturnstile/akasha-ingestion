from __future__ import annotations

from akasha.config import RuntimeBackend, Settings
from akasha.services.signing import SigningService


def test_signing_service_verifies_expected_operation_and_rejects_wrong_operation() -> None:
    service = SigningService(
        Settings(
            runtime_backend=RuntimeBackend.MEMORY,
            signing_secret="test-signing-secret",
            signed_url_ttl_seconds=60,
        )
    )
    query_hash = service.query_hash("field-query")
    signed = service.sign(
        method="GET",
        operation="tile",
        resource_id="layer_123",
        path_template="/tiles/layer_123/{z}/{x}/{y}.png",
        geometry_or_query_hash=query_hash,
    )

    assert service.verify(
        method="GET",
        operation="tile",
        resource_id=signed.resource_id,
        path_template=signed.path_template,
        geometry_or_query_hash=query_hash,
        expires_at=signed.expires_at,
        key_id=signed.key_id,
        signature=signed.signature,
    )
    assert not service.verify(
        method="GET",
        operation="stats",
        resource_id=signed.resource_id,
        path_template=signed.path_template,
        geometry_or_query_hash=query_hash,
        expires_at=signed.expires_at,
        key_id=signed.key_id,
        signature=signed.signature,
    )
