from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from akasha.config import RuntimeBackend, Settings
from akasha.providers.bhoonidhi import (
    BhoonidhiAuthError,
    BhoonidhiClient,
    BhoonidhiDownloadIntegrityError,
    BhoonidhiDownloadUnavailable,
    BhoonidhiError,
    TokenSession,
    normalize_candidates,
    redact_value,
)
from akasha.providers.contracts import ProviderErrorCategory

SOURCE_ID = "resourcesat-2a-liss3-boa"
COLLECTION = "ResourceSat-2A_LISS3_BOA"


class FakeClock:
    def __init__(self) -> None:
        self.current = 1_000.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += seconds


class OneShotStream(httpx.SyncByteStream):
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.iterated = False

    def __iter__(self) -> Iterator[bytes]:
        if self.iterated:
            return
        self.iterated = True
        yield self.body


@pytest.fixture
def work_dir() -> Iterator[Path]:
    root = Path(".runtime") / "pytest-bhoonidhi-provider"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_missing_credentials_raise_auth_error_without_http() -> None:
    called = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    provider = _provider(
        handler,
        settings=_settings(bhoonidhi_user_id="", bhoonidhi_password=""),
    )

    with pytest.raises(BhoonidhiAuthError):
        provider.token()

    assert called is False


def test_password_token_uses_password_grant_body() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/auth/token"
        assert _json_body(request) == {
            "userId": "operator",
            "password": "secret",
            "grant_type": "password",
        }
        return _token_response("access-1", "refresh-1")

    provider = _provider(handler)

    assert provider.token() == "access-1"
    assert provider.session is not None
    assert provider.session.refresh_token == "refresh-1"
    assert len(requests) == 1


def test_fresh_token_reuse_makes_no_http_call() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _token_response("access-1", "refresh-1", expires_in=600)

    clock = FakeClock()
    provider = _provider(handler, clock=clock)

    assert provider.token() == "access-1"
    assert provider.token() == "access-1"
    assert len(requests) == 1


def test_refresh_token_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/token"
        assert _json_body(request) == {
            "refresh_token": "refresh-old",
            "grant_type": "refresh_token",
        }
        return _token_response("access-new", "refresh-new")

    clock = FakeClock()
    provider = _provider(handler, clock=clock)
    provider.session = TokenSession("access-old", "refresh-old", clock.now() - 1)

    assert provider.token() == "access-new"
    assert provider.session is not None
    assert provider.session.refresh_token == "refresh-new"


@pytest.mark.parametrize("status_code", [401, 403])
def test_refresh_auth_failure_logs_out_stale_session_and_password_grants(status_code: int) -> None:
    token_calls = 0
    logout_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls, logout_calls
        if request.url.path == "/auth/token":
            token_calls += 1
            if token_calls == 1:
                assert _json_body(request)["grant_type"] == "refresh_token"
                return httpx.Response(status_code, json={"Description": "expired"})
            assert _json_body(request)["grant_type"] == "password"
            return _token_response("access-new", "refresh-new")
        if request.url.path == "/auth/logout":
            logout_calls += 1
            assert request.headers["authorization"] == "Bearer access-old"
            return httpx.Response(200, json={})
        raise AssertionError(f"unexpected request {request.url}")

    clock = FakeClock()
    provider = _provider(handler, clock=clock)
    provider.session = TokenSession("access-old", "refresh-old", clock.now() - 1)

    assert provider.token() == "access-new"
    assert token_calls == 2
    assert logout_calls == 1


def test_password_grant_403_with_active_session_logs_out_and_retries_once() -> None:
    token_calls = 0
    logout_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls, logout_calls
        if request.url.path == "/auth/token":
            token_calls += 1
            assert _json_body(request)["grant_type"] == "password"
            if token_calls == 1:
                return httpx.Response(403, json={"Description": "active session"})
            return _token_response("access-new", "refresh-new")
        if request.url.path == "/auth/logout":
            logout_calls += 1
            assert request.headers["authorization"] == "Bearer stale"
            return httpx.Response(200, json={})
        raise AssertionError(f"unexpected request {request.url}")

    clock = FakeClock()
    provider = _provider(handler, clock=clock)
    provider.session = TokenSession("stale", None, clock.now() - 1)

    assert provider.token() == "access-new"
    assert token_calls == 2
    assert logout_calls == 1


def test_password_grant_403_without_session_raises_auth_error() -> None:
    provider = _provider(lambda _: httpx.Response(403, json={"Description": "denied"}))

    with pytest.raises(BhoonidhiAuthError) as exc_info:
        provider.token()

    assert exc_info.value.status_code == 403


def test_logout_posts_with_bearer_token_and_clears_session() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/auth/logout"
        assert request.method == "POST"
        assert request.headers["authorization"] == "Bearer access-1"
        return httpx.Response(200, json={})

    clock = FakeClock()
    provider = _provider(handler, clock=clock)
    provider.session = TokenSession("access-1", "refresh-1", clock.now() + 600)

    provider.logout()

    assert provider.session is None
    assert len(requests) == 1


def test_search_payload_pagination_and_throttling() -> None:
    search_bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer access-1"
        assert request.url.path == "/data/search"
        search_bodies.append(_json_body(request))
        if len(search_bodies) == 1:
            return httpx.Response(
                200,
                json={
                    "features": [_feature("P1")],
                    "links": [
                        {
                            "rel": "next",
                            "href": "https://bhoonidhi.test/data/search",
                            "method": "POST",
                            "body": {"page": 2},
                        }
                    ],
                },
            )
        return httpx.Response(200, json={"features": [_feature("P2")], "links": []})

    clock = FakeClock()
    provider = _provider(handler, clock=clock, settings=_settings(bhoonidhi_search_rps=2.0))
    provider.session = TokenSession("access-1", "refresh-1", clock.now() + 600)

    candidates = provider.search(
        source_id=SOURCE_ID,
        date_start=date(2026, 1, 1),
        date_end=date(2026, 1, 31),
        intersects=_polygon(),
        limit=999,
    )

    assert [candidate.provider_product_id for candidate in candidates] == ["P1", "P2"]
    assert search_bodies[0]["collections"] == [COLLECTION]
    assert search_bodies[0]["datetime"] == "2026-01-01T00:00:00Z/2026-01-31T23:59:59Z"
    assert search_bodies[0]["filter"] == {"op": "eq", "args": [{"property": "Online"}, "Y"]}
    assert search_bodies[0]["filter-lang"] == "cql2-json"
    assert search_bodies[0]["limit"] == 500
    assert search_bodies[0]["sortby"] == [{"field": "datetime", "direction": "desc"}]
    assert search_bodies[1] == {"page": 2}
    assert clock.sleeps == [0.5]


def test_search_rejects_off_origin_next_link_before_auth_leak() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "evil.test":
            raise AssertionError("off-origin next link should not be requested")
        return httpx.Response(
            200,
            json={
                "features": [_feature("P1")],
                "links": [
                    {
                        "rel": "next",
                        "href": "https://evil.test/capture",
                        "method": "POST",
                    }
                ],
            },
        )

    clock = FakeClock()
    provider = _provider(handler, clock=clock)
    provider.session = TokenSession("access-1", None, clock.now() + 600)

    with pytest.raises(BhoonidhiError, match="outside the configured API base"):
        provider.search(
            source_id=SOURCE_ID,
            datetime_range="2026-01-01/2026-01-31",
            intersects=_polygon(),
        )

    assert len(calls) == 1


def test_no_results_404_search_returns_empty_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/data/search"
        return httpx.Response(404, json={"Description": "No Results found"})

    clock = FakeClock()
    provider = _provider(handler, clock=clock)
    provider.session = TokenSession("access-1", None, clock.now() + 600)

    assert (
        provider.search(
            source_id=SOURCE_ID,
            datetime_range="2026-01-01/2026-01-31",
            intersects=_polygon(),
        )
        == []
    )


def test_retryable_search_statuses_eventually_succeed() -> None:
    statuses = [500, 429, 200]

    def handler(_: httpx.Request) -> httpx.Response:
        status = statuses.pop(0)
        if status == 200:
            return httpx.Response(200, json={"features": [_feature("P1")], "links": []})
        return httpx.Response(status, json={"Description": "retry"})

    clock = FakeClock()
    provider = _provider(handler, clock=clock, max_retries=2)
    provider.session = TokenSession("access-1", None, clock.now() + 600)

    candidates = provider.search(
        source_id=SOURCE_ID,
        datetime_range="2026-01-01/2026-01-31",
        intersects=_polygon(),
    )

    assert [candidate.provider_product_id for candidate in candidates] == ["P1"]
    assert clock.sleeps == [2.0, 20.0]


def test_online_candidate_filtering_drops_offline_and_no_overlap() -> None:
    candidates = normalize_candidates(
        [
            _feature("P1", bbox=[0, 1, 1, 0], online="Y"),
            _feature("P2", bbox=[0, 0, 1, 1], online="N"),
            _feature("P3", bbox=[2, 2, 3, 3], online="Y"),
        ],
        source_id=SOURCE_ID,
        collection=COLLECTION,
        aoi_bbox=[0, 0, 1, 1],
        filter_online_overlap=True,
    )

    assert [candidate.provider_product_id for candidate in candidates] == ["P1"]
    assert candidates[0].bbox == [0.0, 0.0, 1.0, 1.0]
    assert candidates[0].overlap_bbox == [0.0, 0.0, 1.0, 1.0]
    assert candidates[0].overlap_area == 1.0


def test_download_unavailable_404_raises(work_dir: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/download"
        return httpx.Response(404, json={"Description": "offline"})

    clock = FakeClock()
    provider = _provider(handler, clock=clock)
    provider.session = TokenSession("access-1", None, clock.now() + 600)

    with pytest.raises(BhoonidhiDownloadUnavailable):
        provider.download_product(
            product_id="P1",
            collection=COLLECTION,
            destination=work_dir / "P1.zip",
        )


def test_existing_download_reuse_makes_no_http_call(work_dir: Path) -> None:
    destination = work_dir / "existing.zip"
    destination.write_bytes(b"already-present")
    called = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    provider = _provider(handler)

    result = provider.download_product(
        product_id="P1",
        collection=COLLECTION,
        destination=destination,
    )

    assert result["status"] == "exists"
    assert result["bytes"] == len(b"already-present")
    assert result["sha256"] == hashlib.sha256(b"already-present").hexdigest()
    assert called is False


def test_download_retry_412_and_5xx_succeeds(work_dir: Path) -> None:
    statuses = [412, 500, 200]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/download"
        status = statuses.pop(0)
        if status == 200:
            return httpx.Response(200, content=b"downloaded")
        return httpx.Response(status, json={"Description": "retry later"})

    clock = FakeClock()
    provider = _provider(handler, clock=clock, max_retries=3)
    provider.session = TokenSession("access-1", None, clock.now() + 600)

    result = provider.download_product(
        product_id="P1",
        collection=COLLECTION,
        destination=work_dir / "P1.zip",
        chunk_size=3,
    )

    assert result["status"] == "downloaded"
    assert result["sha256"] == hashlib.sha256(b"downloaded").hexdigest()
    assert clock.sleeps == [10.0, 4.0]


def test_streaming_download_error_response_retries(work_dir: Path) -> None:
    calls = 0
    error_stream = OneShotStream(b'{"Description":"retry later"}')

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        assert request.url.path == "/download"
        calls += 1
        if calls == 1:
            return httpx.Response(
                412,
                stream=error_stream,
            )
        return httpx.Response(200, content=b"downloaded")

    clock = FakeClock()
    provider = _provider(handler, clock=clock, max_retries=1)
    provider.session = TokenSession("access-1", None, clock.now() + 600)

    result = provider.download_product(
        product_id="P1",
        collection=COLLECTION,
        destination=work_dir / "streaming-error.zip",
    )

    assert result["status"] == "downloaded"
    assert calls == 2
    assert error_stream.iterated is True
    assert clock.sleeps == [10.0]


def test_download_401_resets_auth_and_retries_with_password_token(work_dir: Path) -> None:
    download_calls = 0
    token_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal download_calls, token_calls
        if request.url.path == "/download":
            download_calls += 1
            if download_calls == 1:
                assert request.headers["authorization"] == "Bearer stale"
                return httpx.Response(401, json={"Description": "expired"})
            assert request.headers["authorization"] == "Bearer access-new"
            return httpx.Response(200, content=b"fresh")
        if request.url.path == "/auth/token":
            token_calls += 1
            assert _json_body(request)["grant_type"] == "password"
            return _token_response("access-new", "refresh-new")
        raise AssertionError(f"unexpected request {request.url}")

    clock = FakeClock()
    provider = _provider(handler, clock=clock, max_retries=2)
    provider.session = TokenSession("stale", None, clock.now() + 600)

    result = provider.download_product(
        product_id="P1",
        collection=COLLECTION,
        destination=work_dir / "P1.zip",
    )

    assert result["status"] == "downloaded"
    assert download_calls == 2
    assert token_calls == 1


def test_content_length_size_mismatch_raises_and_cleans_part(work_dir: Path) -> None:
    provider = _download_provider(
        httpx.Response(200, content=b"abc", headers={"content-length": "4"})
    )
    destination = work_dir / "bad-size.zip"

    with pytest.raises(BhoonidhiDownloadIntegrityError) as exc_info:
        provider.download_product(product_id="P1", collection=COLLECTION, destination=destination)

    assert exc_info.value.category == ProviderErrorCategory.DOWNLOAD_FAILED
    assert destination.exists() is False
    assert destination.with_suffix(".zip.part").exists() is False


def test_provider_checksum_mismatch_raises_and_cleans_part(work_dir: Path) -> None:
    provider = _download_provider(
        httpx.Response(200, content=b"abc", headers={"x-checksum-sha256": "0" * 64})
    )
    destination = work_dir / "bad-checksum.zip"

    with pytest.raises(BhoonidhiDownloadIntegrityError):
        provider.download_product(product_id="P1", collection=COLLECTION, destination=destination)

    assert destination.exists() is False
    assert destination.with_suffix(".zip.part").exists() is False


def test_internal_sha256_recorded_when_provider_checksum_absent(work_dir: Path) -> None:
    provider = _download_provider(httpx.Response(200, content=b"abc"))
    destination = work_dir / "ok.zip"

    result = provider.download_product(
        product_id="P1",
        collection=COLLECTION,
        destination=destination,
        chunk_size=2,
    )

    assert result["status"] == "downloaded"
    assert result["bytes"] == 3
    assert result["sha256"] == hashlib.sha256(b"abc").hexdigest()
    assert result["provider_checksum"] is None
    assert destination.read_bytes() == b"abc"


def test_redaction_removes_tokens_cookies_signed_urls_and_provider_urls() -> None:
    payload = {
        "access_token": "access-secret",
        "refresh_token": "refresh-secret",
        "Authorization": "Bearer bearer-secret",
        "Cookie": "session=cookie-secret",
        "downloadUrl": "https://provider.test/download?id=P1&token=download-secret",
        "nested": {
            "apiKey": "api-secret",
            "url": "https://object.test/key?X-Amz-Signature=amz-secret&safe=ok&sig=sig-secret",
            "message": "Authorization: Bearer message-secret",
        },
        "items": ["https://host/path?token=list-secret&safe=1", "Token inline-secret"],
    }

    redacted = redact_value(payload)
    dumped = json.dumps(redacted)

    for secret in (
        "access-secret",
        "refresh-secret",
        "bearer-secret",
        "cookie-secret",
        "download-secret",
        "api-secret",
        "amz-secret",
        "sig-secret",
        "list-secret",
        "inline-secret",
    ):
        assert secret not in dumped
    assert redacted["downloadUrl"] == "******"
    assert "safe=ok" in redacted["nested"]["url"]
    assert "X-Amz-Signature=******" in redacted["nested"]["url"]

    error = BhoonidhiError(
        "failed",
        metadata={
            "providerUrl": "https://provider.test/download?id=P1&token=provider-secret",
            "cookie": "session=provider-cookie",
        },
    )
    assert "provider-secret" not in json.dumps(error.to_metadata())
    assert "provider-cookie" not in json.dumps(error.to_metadata())


def test_candidate_metadata_redacts_plain_provider_hrefs() -> None:
    candidate = normalize_candidates(
        [
            {
                **_feature("P1"),
                "href": "https://bhoonidhi.test/products/P1",
                "links": [{"rel": "download", "href": "https://bhoonidhi.test/download/P1"}],
                "assets": {
                    "metadata": {
                        "href": "https://bhoonidhi.test/assets/P1.xml",
                        "safe": "kept",
                    }
                },
            }
        ],
        source_id=SOURCE_ID,
        collection=COLLECTION,
    )[0]

    dumped_metadata = json.dumps(candidate.provider_metadata)
    dumped_raw = json.dumps(candidate.raw_item)

    assert "https://bhoonidhi.test" not in dumped_metadata
    assert "https://bhoonidhi.test" not in dumped_raw
    assert candidate.provider_metadata["href"] == "******"
    assert candidate.provider_metadata["links"][0]["href"] == "******"
    assert candidate.provider_metadata["assets"]["metadata"]["href"] == "******"
    assert candidate.provider_metadata["assets"]["metadata"]["safe"] == "kept"


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "runtime_backend": RuntimeBackend.MEMORY,
        "bhoonidhi_api_base": "https://bhoonidhi.test",
        "bhoonidhi_user_id": "operator",
        "bhoonidhi_password": "secret",
        "bhoonidhi_search_rps": 1.0,
        "bhoonidhi_timeout_seconds": 5.0,
        "bhoonidhi_download_chunk_bytes": 4,
    }
    values.update(overrides)
    return Settings(**values)


def _provider(
    handler: httpx.MockTransport | Any,
    *,
    settings: Settings | None = None,
    clock: FakeClock | None = None,
    max_retries: int = 2,
) -> BhoonidhiClient:
    fake_clock = clock or FakeClock()
    transport = (
        handler if isinstance(handler, httpx.MockTransport) else httpx.MockTransport(handler)
    )
    client = httpx.Client(transport=transport)
    return BhoonidhiClient(
        settings or _settings(),
        client=client,
        sleep=fake_clock.sleep,
        now=fake_clock.now,
        max_retries=max_retries,
    )


def _download_provider(response: httpx.Response) -> BhoonidhiClient:
    clock = FakeClock()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/download"
        return response

    provider = _provider(handler, clock=clock)
    provider.session = TokenSession("access-1", None, clock.now() + 600)
    return provider


def _token_response(
    access_token: str,
    refresh_token: str,
    *,
    expires_in: int = 600,
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": expires_in,
        },
    )


def _json_body(request: httpx.Request) -> dict[str, Any]:
    if not request.content:
        return {}
    parsed = json.loads(request.content.decode("utf-8"))
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _feature(
    product_id: str,
    *,
    bbox: list[float] | None = None,
    online: str = "Y",
) -> dict[str, Any]:
    return {
        "type": "Feature",
        "id": product_id,
        "collection": COLLECTION,
        "bbox": bbox or [77.0, 12.5, 78.0, 13.5],
        "properties": {
            "productId": product_id,
            "datetime": "2026-01-15T05:30:00Z",
            "Online": online,
            "downloadUrl": "https://provider.test/download?id=P1&token=secret",
        },
    }


def _polygon() -> dict[str, Any]:
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [77.0, 12.5],
                [78.0, 12.5],
                [78.0, 13.5],
                [77.0, 13.5],
                [77.0, 12.5],
            ]
        ],
    }
