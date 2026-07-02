from __future__ import annotations

import logging

import pytest

from akasha.logging import configure_logging, redact_text


def test_redacts_sensitive_query_values() -> None:
    value = "https://example.test/path?token=abc123&x=1&X-Amz-Signature=secret"

    redacted = redact_text(value)

    assert "abc123" not in redacted
    assert "secret" not in redacted
    assert "[REDACTED]" in redacted


def test_redacts_authorization_header() -> None:
    value = "Authorization: Bearer secret-token"

    redacted = redact_text(value)

    assert "secret-token" not in redacted
    assert redacted == "Authorization: Bearer [REDACTED]"


def test_redacts_api_key_header() -> None:
    redacted = redact_text("X-API-Key: dev-akasha-key")

    assert "dev-akasha-key" not in redacted
    assert redacted == "X-API-Key: [REDACTED]"


def test_redacts_presigned_url_credentials() -> None:
    redacted = redact_text(
        "https://example.test/file?"
        "X-Amz-Credential=AKIA123"
        "&X-Amz-Signature=signature"
        "&AWSAccessKeyId=access-key"
    )

    assert "AKIA123" not in redacted
    assert "signature" not in redacted
    assert "access-key" not in redacted


def test_redacts_named_logger_records(caplog: pytest.LogCaptureFixture) -> None:
    configure_logging("INFO")

    with caplog.at_level(logging.INFO, logger="akasha.probe"):
        logging.getLogger("akasha.probe").info("token=childsecret")

    assert "childsecret" not in caplog.text
    assert "[REDACTED]" in caplog.text


def test_redaction_preserves_formatted_numeric_logs(caplog: pytest.LogCaptureFixture) -> None:
    configure_logging("INFO")

    with caplog.at_level(logging.INFO, logger="akasha.probe"):
        logging.getLogger("akasha.probe").info("status=%d", 200)

    assert "status=200" in caplog.text
