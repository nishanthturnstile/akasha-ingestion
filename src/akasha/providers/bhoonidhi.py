from __future__ import annotations

import base64
import hashlib
import json
import re
import time as time_module
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import httpx

from akasha.config import Settings
from akasha.processing.resourcesat import source_collection
from akasha.providers.contracts import ProviderErrorCategory

RETRYABLE_SEARCH_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})
RETRYABLE_DOWNLOAD_STATUS: frozenset[int] = frozenset({412, 500, 502, 503, 504})
REDACTED = "******"
_DEFAULT_TOKEN_EXPIRES_SECONDS = 1200
_DEFAULT_MAX_RETRIES = 3


class BhoonidhiError(RuntimeError):
    """Base Bhoonidhi client error with redacted metadata for durable job records."""

    def __init__(
        self,
        message: str,
        *,
        category: ProviderErrorCategory | str = ProviderErrorCategory.PROVIDER_SLA_UNAVAILABLE,
        status_code: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.status_code = status_code
        self.metadata = redact_value(dict(metadata or {}))

    def to_metadata(self) -> dict[str, Any]:
        return {
            "category": str(self.category),
            "message": redact_string(str(self)),
            "status_code": self.status_code,
            "metadata": self.metadata,
        }


class BhoonidhiAuthError(BhoonidhiError):
    """Authentication/session failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            category=ProviderErrorCategory.PROVIDER_SLA_UNAVAILABLE,
            status_code=status_code,
            metadata=metadata,
        )


class BhoonidhiDownloadUnavailable(BhoonidhiError):
    """Product is not currently downloadable from Bhoonidhi."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = 404,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            category=ProviderErrorCategory.ASSET_UNAVAILABLE,
            status_code=status_code,
            metadata=metadata,
        )


class BhoonidhiDownloadIntegrityError(BhoonidhiError):
    """Downloaded bytes did not match provider-declared integrity metadata."""

    def __init__(self, message: str, *, metadata: Mapping[str, Any]) -> None:
        super().__init__(
            message,
            category=ProviderErrorCategory.DOWNLOAD_FAILED,
            metadata=metadata,
        )


@dataclass(slots=True)
class TokenSession:
    access_token: str
    refresh_token: str | None
    expires_at: float

    def is_fresh(self, now: float, skew_seconds: int = 60) -> bool:
        return bool(self.access_token) and now < (self.expires_at - skew_seconds)


@dataclass(frozen=True, slots=True)
class BhoonidhiCandidate:
    source_id: str
    collection: str
    provider_product_id: str
    item_id: str
    acquisition_datetime: str | None
    acquisition_at: datetime | None
    bbox: list[float]
    overlap_bbox: list[float] | None
    overlap_area: float
    online: bool
    intersects_aoi: bool
    provider_properties: dict[str, Any] = field(default_factory=dict)
    provider_metadata: dict[str, Any] = field(default_factory=dict)
    raw_item: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        overlap = list(self.overlap_bbox) if self.overlap_bbox else None
        return {
            "source_id": self.source_id,
            "sourceId": self.source_id,
            "collection": self.collection,
            "provider_product_id": self.provider_product_id,
            "providerProductId": self.provider_product_id,
            "item_id": self.item_id,
            "itemId": self.item_id,
            "acquisition_datetime": self.acquisition_datetime,
            "acquisitionDatetime": self.acquisition_datetime,
            "bbox": list(self.bbox),
            "overlap_bbox": overlap,
            "overlapBbox": overlap,
            "overlap_area": self.overlap_area,
            "overlapArea": self.overlap_area,
            "online": self.online,
            "intersects_aoi": self.intersects_aoi,
            "intersectsAoi": self.intersects_aoi,
            "selected": self.online and self.intersects_aoi,
            "provider_properties": self.provider_properties,
            "providerProperties": self.provider_properties,
            "provider_metadata": self.provider_metadata,
            "providerMetadata": self.provider_metadata,
        }


@dataclass(frozen=True, slots=True)
class _ChecksumExpectation:
    algorithm: str
    header: str
    expected_hex: str


class _RetryableHttpStatus(RuntimeError):
    def __init__(self, status_code: int, metadata: Mapping[str, Any] | None = None) -> None:
        super().__init__(f"retryable HTTP {status_code}")
        self.status_code = status_code
        self.metadata = dict(metadata or {})


class BhoonidhiClient:
    provider_adapter = "bhoonidhi"

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time_module.sleep,
        now: Callable[[], float] = time_module.time,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        self._api_base = settings.bhoonidhi_api_base.rstrip("/")
        self._user_id = settings.bhoonidhi_user_id
        self._password = settings.bhoonidhi_password.get_secret_value()
        self._timeout = settings.bhoonidhi_timeout_seconds
        self._search_rps = settings.bhoonidhi_search_rps
        self._download_chunk_bytes = settings.bhoonidhi_download_chunk_bytes
        self._client = client or httpx.Client(timeout=self._timeout)
        self._owns_client = client is None
        self._sleep = sleep
        self._now = now
        self._max_retries = max_retries
        self.session: TokenSession | None = None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> BhoonidhiClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def token(self) -> str:
        if self.session and self.session.is_fresh(self._now()):
            return self.session.access_token

        if self.session and self.session.refresh_token:
            stale_session = self.session
            try:
                return self._refresh_token(stale_session.refresh_token)
            except BhoonidhiAuthError as exc:
                if exc.status_code not in {401, 403}:
                    raise
                self._logout_session(stale_session, ignore_errors=True)
                self.session = None

        return self._password_token()

    def logout(self, *, ignore_errors: bool = False) -> None:
        try:
            if self.session:
                self._logout_session(self.session, ignore_errors=ignore_errors)
        finally:
            self.session = None

    def search(
        self,
        *,
        source_id: str,
        intersects: dict[str, Any],
        date_start: date | datetime | str | None = None,
        date_end: date | datetime | str | None = None,
        datetime_range: str | None = None,
        collection: str | None = None,
        limit: int = 100,
        sortby: list[dict[str, str]] | None = None,
        max_items: int | None = None,
        aoi_bbox: Sequence[float] | None = None,
    ) -> list[BhoonidhiCandidate]:
        provider_collection = collection or source_collection(source_id)
        payload: dict[str, Any] = {
            "collections": [provider_collection],
            "datetime": _datetime_value(
                datetime_range=datetime_range,
                date_start=date_start,
                date_end=date_end,
            ),
            "intersects": intersects,
            "filter": {"op": "eq", "args": [{"property": "Online"}, "Y"]},
            "filter-lang": "cql2-json",
            "limit": _clamp_limit(limit),
            "sortby": sortby or [{"field": "datetime", "direction": "desc"}],
        }
        normalized_aoi_bbox = _normalise_bbox(aoi_bbox) if aoi_bbox is not None else None
        candidates: list[BhoonidhiCandidate] = []
        data = self._request_json_with_retries(
            "POST",
            self._url("/data/search"),
            json_body=payload,
            retry_statuses=RETRYABLE_SEARCH_STATUS,
            auth=True,
        )
        while True:
            for item in _features(data):
                candidate = normalize_candidate(
                    item,
                    source_id=source_id,
                    collection=provider_collection,
                    aoi_bbox=normalized_aoi_bbox,
                )
                if normalized_aoi_bbox is not None and not (
                    candidate.online and candidate.intersects_aoi
                ):
                    continue
                candidates.append(candidate)
                if max_items is not None and len(candidates) >= max_items:
                    return candidates

            next_link = _next_link(data)
            if next_link is None:
                return candidates
            delay = self._page_delay_seconds()
            if delay > 0:
                self._sleep(delay)
            data = self._request_next_link(next_link)

    def download_product(
        self,
        *,
        product_id: str,
        collection: str,
        destination: Path,
        chunk_size: int | None = None,
    ) -> dict[str, Any]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file() and destination.stat().st_size > 0:
            return {
                "status": "exists",
                "path": str(destination),
                "bytes": destination.stat().st_size,
                "sha256": _sha256_file(destination),
            }

        part_path = destination.with_suffix(destination.suffix + ".part")
        _unlink_if_exists(part_path)
        download_chunk_size = chunk_size or self._download_chunk_bytes
        for attempt in range(self._max_retries + 1):
            try:
                return self._download_once(
                    product_id=product_id,
                    collection=collection,
                    destination=destination,
                    part_path=part_path,
                    chunk_size=download_chunk_size,
                )
            except _RetryableHttpStatus as exc:
                _unlink_if_exists(part_path)
                if exc.status_code == 401:
                    self.session = None
                if attempt >= self._max_retries:
                    raise BhoonidhiError(
                        f"Bhoonidhi download failed with HTTP {exc.status_code}",
                        category=ProviderErrorCategory.EXTERNAL_ASSET_READ_FAILED,
                        status_code=exc.status_code,
                        metadata={
                            "product_id": product_id,
                            "collection": collection,
                            "response": exc.metadata,
                        },
                    ) from exc
                self._sleep(_backoff_seconds(attempt, exc.status_code))
        raise BhoonidhiError(
            "Bhoonidhi download failed after retries",
            category=ProviderErrorCategory.EXTERNAL_ASSET_READ_FAILED,
            metadata={"product_id": product_id, "collection": collection},
        )

    def normalize_candidates(
        self,
        items: Sequence[Mapping[str, Any]],
        *,
        source_id: str,
        collection: str | None = None,
        aoi_bbox: Sequence[float] | None = None,
        filter_online_overlap: bool = False,
    ) -> list[BhoonidhiCandidate]:
        return normalize_candidates(
            items,
            source_id=source_id,
            collection=collection or source_collection(source_id),
            aoi_bbox=aoi_bbox,
            filter_online_overlap=filter_online_overlap,
        )

    def _password_token(self) -> str:
        if not self._user_id or not self._password:
            raise BhoonidhiAuthError("Bhoonidhi credentials are not configured.")

        payload = {
            "userId": self._user_id,
            "password": self._password,
            "grant_type": "password",
        }
        response = self._request_json_response(
            "POST", self._url("/auth/token"), payload, auth=False
        )
        if response.status_code == 403 and self.session is not None:
            self.logout(ignore_errors=True)
            response = self._request_json_response(
                "POST", self._url("/auth/token"), payload, auth=False
            )
        if response.status_code in {401, 403}:
            raise BhoonidhiAuthError(
                "Bhoonidhi credentials were rejected.",
                status_code=response.status_code,
                metadata=_response_error_metadata(response),
            )
        if response.status_code >= 400:
            raise _error_from_response(
                response,
                category=ProviderErrorCategory.PROVIDER_SLA_UNAVAILABLE,
            )
        return self._store_token(_response_json(response))

    def _refresh_token(self, refresh_token: str) -> str:
        payload = {"refresh_token": refresh_token, "grant_type": "refresh_token"}
        response = self._request_json_response(
            "POST", self._url("/auth/token"), payload, auth=False
        )
        if response.status_code in {401, 403}:
            raise BhoonidhiAuthError(
                "Bhoonidhi refresh token was rejected.",
                status_code=response.status_code,
                metadata=_response_error_metadata(response),
            )
        if response.status_code >= 400:
            raise _error_from_response(
                response,
                category=ProviderErrorCategory.PROVIDER_SLA_UNAVAILABLE,
            )
        return self._store_token(_response_json(response))

    def _store_token(self, data: Mapping[str, Any]) -> str:
        token = _first_string(data, "access_token", "accessToken", "token")
        if not token:
            raise BhoonidhiAuthError("Bhoonidhi auth response did not include access_token.")
        refresh_token = _first_string(data, "refresh_token", "refreshToken")
        expires_in = _optional_int(
            data.get("expires_in") or data.get("expiresIn") or data.get("expires")
        )
        self.session = TokenSession(
            access_token=token,
            refresh_token=refresh_token,
            expires_at=self._now() + max(expires_in or _DEFAULT_TOKEN_EXPIRES_SECONDS, 1),
        )
        return token

    def _logout_session(self, session: TokenSession, *, ignore_errors: bool) -> None:
        try:
            response = self._request_json_response(
                "POST",
                self._url("/auth/logout"),
                {},
                auth=False,
                headers={"Authorization": f"Bearer {session.access_token}"},
            )
            if response.status_code >= 400 and not ignore_errors:
                raise _error_from_response(
                    response,
                    category=ProviderErrorCategory.PROVIDER_SLA_UNAVAILABLE,
                )
        except Exception:
            if not ignore_errors:
                raise

    def _request_next_link(self, link: Mapping[str, Any]) -> dict[str, Any]:
        href = link.get("href")
        if not isinstance(href, str) or not href:
            raise BhoonidhiError(
                "Bhoonidhi next link is missing href.",
                category=ProviderErrorCategory.STAC_SEARCH_FAILED,
                metadata={"link": redact_value(dict(link))},
            )
        method = str(link.get("method") or "GET").upper()
        body = link.get("body") if isinstance(link.get("body"), dict) else None
        url = self._absolute_url(href)
        if not _same_origin(url, self._api_base):
            raise BhoonidhiError(
                "Bhoonidhi next link points outside the configured API base.",
                category=ProviderErrorCategory.STAC_SEARCH_FAILED,
                metadata={"link": redact_provider_metadata(dict(link))},
            )
        return self._request_json_with_retries(
            method,
            url,
            json_body=body,
            retry_statuses=RETRYABLE_SEARCH_STATUS,
            auth=True,
        )

    def _request_json_with_retries(
        self,
        method: str,
        url: str,
        *,
        json_body: Mapping[str, Any] | None,
        retry_statuses: frozenset[int],
        auth: bool,
    ) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            response = self._request_json_response(method, url, json_body, auth=auth)
            if 200 <= response.status_code < 300:
                return _response_json(response)
            metadata = _response_error_metadata(response)
            if _is_search_no_results(url, response.status_code, metadata):
                return {"features": [], "links": []}
            if response.status_code == 401:
                self.session = None
            should_retry = response.status_code in retry_statuses or response.status_code == 401
            if not should_retry or attempt >= self._max_retries:
                raise _error_from_response(
                    response,
                    category=_search_error_category(response.status_code),
                    metadata=metadata,
                )
            self._sleep(_backoff_seconds(attempt, response.status_code))
        raise BhoonidhiError(
            "Bhoonidhi request failed after retries.",
            category=ProviderErrorCategory.STAC_SEARCH_FAILED,
        )

    def _request_json_response(
        self,
        method: str,
        url: str,
        json_body: Mapping[str, Any] | None,
        *,
        auth: bool,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        request_headers = {"Accept": "application/json", **dict(headers or {})}
        if json_body is not None:
            request_headers["Content-Type"] = "application/json"
        if auth:
            request_headers["Authorization"] = f"Bearer {self.token()}"
        try:
            return self._client.request(
                method,
                url,
                json=dict(json_body) if json_body is not None else None,
                headers=request_headers,
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise BhoonidhiError(
                "Bhoonidhi request timed out.",
                category=ProviderErrorCategory.PROVIDER_SLA_UNAVAILABLE,
                metadata={"method": method},
            ) from exc
        except httpx.NetworkError as exc:
            raise BhoonidhiError(
                "Bhoonidhi network error.",
                category=ProviderErrorCategory.PROVIDER_SLA_UNAVAILABLE,
                metadata={"method": method},
            ) from exc

    def _download_once(
        self,
        *,
        product_id: str,
        collection: str,
        destination: Path,
        part_path: Path,
        chunk_size: int,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token()}"}
        url = self._url("/download")
        params = {"id": product_id, "collection": collection}
        try:
            with self._client.stream(
                "GET",
                url,
                params=params,
                headers=headers,
                timeout=self._timeout,
            ) as response:
                if response.status_code == 404:
                    raise BhoonidhiDownloadUnavailable(
                        f"Bhoonidhi product is not online: {product_id}",
                        metadata={"product_id": product_id, "collection": collection},
                    )
                if response.status_code == 401:
                    raise _RetryableHttpStatus(401, _response_error_metadata(response))
                if response.status_code in RETRYABLE_DOWNLOAD_STATUS:
                    raise _RetryableHttpStatus(
                        response.status_code, _response_error_metadata(response)
                    )
                if response.status_code >= 400:
                    raise _error_from_response(
                        response,
                        category=ProviderErrorCategory.EXTERNAL_ASSET_READ_FAILED,
                    )

                expected_length = _content_length(response.headers)
                checksum = _provider_checksum(response.headers)
                sha256 = hashlib.sha256()
                md5 = hashlib.md5(usedforsecurity=False)
                total = 0
                with part_path.open("wb") as file_handle:
                    for chunk in response.iter_bytes(chunk_size=chunk_size):
                        if not chunk:
                            continue
                        file_handle.write(chunk)
                        total += len(chunk)
                        sha256.update(chunk)
                        md5.update(chunk)

                sha256_hex = sha256.hexdigest()
                md5_hex = md5.hexdigest()
                self._verify_download_integrity(
                    part_path=part_path,
                    product_id=product_id,
                    collection=collection,
                    total=total,
                    expected_length=expected_length,
                    checksum=checksum,
                    sha256_hex=sha256_hex,
                    md5_hex=md5_hex,
                )
                part_path.replace(destination)
                return {
                    "status": "downloaded",
                    "path": str(destination),
                    "bytes": total,
                    "sha256": sha256_hex,
                    "provider_checksum": _checksum_metadata(checksum),
                }
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            _unlink_if_exists(part_path)
            raise BhoonidhiError(
                "Bhoonidhi download network error.",
                category=ProviderErrorCategory.PROVIDER_SLA_UNAVAILABLE,
                metadata={"product_id": product_id, "collection": collection},
            ) from exc

    def _verify_download_integrity(
        self,
        *,
        part_path: Path,
        product_id: str,
        collection: str,
        total: int,
        expected_length: int | None,
        checksum: _ChecksumExpectation | None,
        sha256_hex: str,
        md5_hex: str,
    ) -> None:
        if expected_length is not None and total != expected_length:
            _unlink_if_exists(part_path)
            raise BhoonidhiDownloadIntegrityError(
                "Bhoonidhi download size mismatch.",
                metadata={
                    "product_id": product_id,
                    "collection": collection,
                    "expected_bytes": expected_length,
                    "actual_bytes": total,
                },
            )
        if checksum is None:
            return
        actual = sha256_hex if checksum.algorithm == "sha256" else md5_hex
        if actual.lower() != checksum.expected_hex.lower():
            _unlink_if_exists(part_path)
            raise BhoonidhiDownloadIntegrityError(
                "Bhoonidhi download checksum mismatch.",
                metadata={
                    "product_id": product_id,
                    "collection": collection,
                    "checksum_header": checksum.header,
                    "checksum_algorithm": checksum.algorithm,
                    "expected_checksum": checksum.expected_hex,
                    "actual_checksum": actual,
                },
            )

    def _url(self, path: str) -> str:
        return self._absolute_url(path)

    def _absolute_url(self, url_or_path: str) -> str:
        return urllib.parse.urljoin(self._api_base + "/", url_or_path.lstrip("/"))

    def _page_delay_seconds(self) -> float:
        return 1.0 / self._search_rps if self._search_rps > 0 else 0.0


def normalize_candidate(
    item: Mapping[str, Any],
    *,
    source_id: str,
    collection: str | None = None,
    aoi_bbox: Sequence[float] | None = None,
) -> BhoonidhiCandidate:
    item_dict = dict(item)
    props = _mapping(item_dict.get("properties"))
    provider_collection = str(
        item_dict.get("collection")
        or props.get("collection")
        or collection
        or source_collection(source_id)
    )
    bbox = _normalise_bbox(item_dict.get("bbox") or props.get("bbox") or props.get("BoundingBox"))
    if bbox is None:
        raise BhoonidhiError(
            "Bhoonidhi item has an invalid bbox.",
            category=ProviderErrorCategory.METADATA_FAILED,
            metadata={"item": redact_value(item_dict)},
        )
    normalized_aoi_bbox = _normalise_bbox(aoi_bbox) if aoi_bbox is not None else None
    overlap_bbox = _bbox_intersection(bbox, normalized_aoi_bbox) if normalized_aoi_bbox else None
    acquisition_datetime = _acquisition_datetime(item_dict, props)
    provider_product_id = _provider_product_id(item_dict, props)
    online = _is_online(props.get("Online", props.get("online", item_dict.get("Online"))))
    provider_metadata = _provider_metadata(item_dict)
    return BhoonidhiCandidate(
        source_id=source_id,
        collection=provider_collection,
        provider_product_id=provider_product_id,
        item_id=str(item_dict.get("id") or provider_product_id),
        acquisition_datetime=acquisition_datetime,
        acquisition_at=_parse_datetime(acquisition_datetime),
        bbox=bbox,
        overlap_bbox=overlap_bbox,
        overlap_area=_bbox_area(overlap_bbox),
        online=online,
        intersects_aoi=overlap_bbox is not None if normalized_aoi_bbox else True,
        provider_properties=redact_value(props),
        provider_metadata=provider_metadata,
        raw_item=redact_provider_metadata(item_dict),
    )


def normalize_candidates(
    items: Sequence[Mapping[str, Any]],
    *,
    source_id: str,
    collection: str | None = None,
    aoi_bbox: Sequence[float] | None = None,
    filter_online_overlap: bool = False,
) -> list[BhoonidhiCandidate]:
    provider_collection = collection or source_collection(source_id)
    normalized_aoi_bbox = _normalise_bbox(aoi_bbox) if aoi_bbox is not None else None
    candidates: list[BhoonidhiCandidate] = []
    for item in items:
        candidate = normalize_candidate(
            item,
            source_id=source_id,
            collection=provider_collection,
            aoi_bbox=normalized_aoi_bbox,
        )
        if (
            filter_online_overlap
            and normalized_aoi_bbox is not None
            and not (candidate.online and candidate.intersects_aoi)
        ):
            continue
        candidates.append(candidate)
    return candidates


def _datetime_value(
    *,
    datetime_range: str | None,
    date_start: date | datetime | str | None,
    date_end: date | datetime | str | None,
) -> str:
    if datetime_range:
        return datetime_range
    if isinstance(date_start, str) and date_end is None:
        return date_start
    if date_start is None or date_end is None:
        raise ValueError("Bhoonidhi search requires datetime_range or date_start/date_end.")
    return f"{_datetime_boundary(date_start, end=False)}/{_datetime_boundary(date_end, end=True)}"


def _datetime_boundary(value: date | datetime | str, *, end: bool) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.combine(value, time.max if end else time.min, tzinfo=UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    dt = dt.astimezone(UTC)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _clamp_limit(limit: int) -> int:
    return min(max(int(limit), 1), 500)


def _features(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    features = data.get("features") if isinstance(data, Mapping) else []
    return [dict(feature) for feature in features if isinstance(feature, Mapping)]


def _next_link(data: Mapping[str, Any]) -> dict[str, Any] | None:
    links = data.get("links") if isinstance(data, Mapping) else []
    if not isinstance(links, list):
        return None
    for link in links:
        if isinstance(link, Mapping) and str(link.get("rel") or "").lower() == "next":
            return dict(link)
    return None


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _first_string(mapping: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _response_json(response: httpx.Response) -> dict[str, Any]:
    try:
        parsed = response.json()
    except httpx.ResponseNotRead:
        try:
            raw = response.read()
        except httpx.HTTPError:
            return {}
        if not raw:
            return {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {"body": redact_string(raw.decode("utf-8", errors="replace"))}
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        text = response.text if response.content else ""
        return {"body": redact_string(text)} if text else {}
    return dict(parsed) if isinstance(parsed, Mapping) else {"value": parsed}


def _response_error_metadata(response: httpx.Response) -> dict[str, Any]:
    return {
        "status_code": response.status_code,
        "body": redact_value(_response_json(response)),
    }


def _error_from_response(
    response: httpx.Response,
    *,
    category: ProviderErrorCategory,
    metadata: Mapping[str, Any] | None = None,
) -> BhoonidhiError:
    return BhoonidhiError(
        f"Bhoonidhi request failed with HTTP {response.status_code}.",
        category=category,
        status_code=response.status_code,
        metadata=metadata or _response_error_metadata(response),
    )


def _search_error_category(status_code: int) -> ProviderErrorCategory:
    if status_code == 429:
        return ProviderErrorCategory.SOURCE_RATE_LIMITED
    if status_code >= 500:
        return ProviderErrorCategory.PROVIDER_SLA_UNAVAILABLE
    return ProviderErrorCategory.STAC_SEARCH_FAILED


def _is_search_no_results(url: str, status_code: int, metadata: Mapping[str, Any]) -> bool:
    if status_code != 404:
        return False
    if urllib.parse.urlparse(url).path.rstrip("/") != "/data/search":
        return False
    text = json.dumps(metadata, sort_keys=True).lower()
    return "no results" in text or "no result" in text


def _backoff_seconds(attempt: int, status_code: int) -> float:
    base = 10.0 if status_code in {412, 429} else 2.0
    return min(base * (2**attempt), 120.0)


def _provider_product_id(item: Mapping[str, Any], props: Mapping[str, Any]) -> str:
    keys = (
        "productId",
        "product_id",
        "ProductId",
        "ProductID",
        "productIdentifier",
        "identifier",
        "id",
    )
    for key in keys:
        value = item.get(key, props.get(key))
        if isinstance(value, str) and value:
            return value
    fallback = item.get("id")
    return str(fallback or "")


def _acquisition_datetime(item: Mapping[str, Any], props: Mapping[str, Any]) -> str | None:
    keys = (
        "datetime",
        "acquisition_datetime",
        "acquisitionDatetime",
        "acquisitionDateTime",
        "AcquisitionDateTime",
        "acquisitionDate",
        "AcquisitionDate",
        "start_datetime",
        "startDateTime",
    )
    for key in keys:
        value = props.get(key, item.get(key))
        if isinstance(value, str) and value:
            return value
    return None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _provider_metadata(item: Mapping[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in ("links", "assets", "downloadUrl", "download_url", "providerUrl", "href"):
        if key in item:
            metadata[key] = item[key]
    return redact_provider_metadata(metadata)


def _is_online(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    return str(value or "").strip().upper() in {"Y", "YES", "TRUE", "ONLINE", "1"}


def _normalise_bbox(value: Any) -> list[float] | None:
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(value, str) or len(value) != 4:
        return None
    try:
        west, south_or_north, east, north_or_south = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    south = min(south_or_north, north_or_south)
    north = max(south_or_north, north_or_south)
    if west >= east:
        return None
    return [west, south, east, north]


def _bbox_intersection(a: Sequence[float], b: Sequence[float] | None) -> list[float] | None:
    if b is None:
        return None
    minx = max(float(a[0]), float(b[0]))
    miny = max(float(a[1]), float(b[1]))
    maxx = min(float(a[2]), float(b[2]))
    maxy = min(float(a[3]), float(b[3]))
    if minx >= maxx or miny >= maxy:
        return None
    return [minx, miny, maxx, maxy]


def _bbox_area(bbox: Sequence[float] | None) -> float:
    if bbox is None:
        return 0.0
    return max(float(bbox[2]) - float(bbox[0]), 0.0) * max(
        float(bbox[3]) - float(bbox[1]), 0.0
    )


def _content_length(headers: httpx.Headers) -> int | None:
    value = headers.get("content-length")
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _provider_checksum(headers: httpx.Headers) -> _ChecksumExpectation | None:
    for header in ("x-checksum-sha256", "x-amz-checksum-sha256"):
        value = headers.get(header)
        if not value:
            continue
        expected = _digest_to_hex(value, "sha256")
        if expected:
            return _ChecksumExpectation("sha256", header, expected)
    for header in ("x-checksum-md5", "content-md5"):
        value = headers.get(header)
        if not value:
            continue
        expected = _digest_to_hex(value, "md5")
        if expected:
            return _ChecksumExpectation("md5", header, expected)
    etag = headers.get("etag")
    if etag:
        expected = _etag_md5(etag)
        if expected:
            return _ChecksumExpectation("md5", "etag", expected)
    return None


def _digest_to_hex(value: str, algorithm: str) -> str | None:
    clean = value.strip().strip('"').lower()
    expected_length = 64 if algorithm == "sha256" else 32
    if re.fullmatch(rf"[0-9a-f]{{{expected_length}}}", clean):
        return clean
    try:
        decoded = base64.b64decode(value.strip().strip('"'), validate=True)
    except (ValueError, base64.binascii.Error):
        return None
    if len(decoded) == expected_length // 2:
        return decoded.hex()
    return None


def _etag_md5(value: str) -> str | None:
    clean = value.strip().strip('"').lower()
    if "-" in clean:
        return None
    if re.fullmatch(r"[0-9a-f]{32}", clean):
        return clean
    return None


def _checksum_metadata(checksum: _ChecksumExpectation | None) -> dict[str, str] | None:
    if checksum is None:
        return None
    return {
        "algorithm": checksum.algorithm,
        "header": checksum.header,
        "expected": checksum.expected_hex,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unlink_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


_SECRET_KEY_EXACT = {
    "authorization",
    "cookie",
    "set-cookie",
    "password",
    "passwd",
    "access_token",
    "refresh_token",
    "token",
    "api_key",
    "apikey",
    "secret",
    "signature",
    "sig",
}
_SECRET_KEY_SUFFIXES = (
    "_password",
    "_secret",
    "_token",
    "_api_key",
    "_apikey",
    "_credential",
    "_bearer",
)
_SECRET_KEY_PREFIXES = ("x-amz-", "x-goog-")
_SIGNED_URL_QUERY_PARAMS = {
    "token",
    "access_token",
    "auth",
    "sig",
    "signature",
    "x-amz-signature",
    "x-amz-security-token",
    "x-amz-credential",
    "x-amz-date",
    "x-amz-algorithm",
    "x-amz-expires",
    "x-amz-signedheaders",
    "x-goog-signature",
    "x-goog-credential",
    "x-goog-date",
    "policy",
    "awsaccesskeyid",
}
_AUTH_HEADER_RE = re.compile(r"((?:Bearer|Basic|Token)\s+)[A-Za-z0-9+/=._\-]{3,}", re.I)
_COOKIE_RE = re.compile(r"((?:Cookie|Set-Cookie):\s*)[^\r\n;]+(?:;[^\r\n]+)?", re.I)
_URL_SECRET_RE = re.compile(
    r"([?&](?:token|access_token|api_key|apikey|key|password|secret|credential|sig"
    r"|signature|x-amz-[A-Za-z0-9-]+|x-goog-[A-Za-z0-9-]+)=)[^&\s\"']+",
    re.I,
)


def redact_value(value: Any, *, depth: int = 0, max_depth: int = 20) -> Any:
    if depth > max_depth:
        return REDACTED
    if isinstance(value, Mapping):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_secret_key(key_text) or _is_provider_url_key(key_text):
                redacted[key] = REDACTED
            else:
                redacted[key] = redact_value(item, depth=depth + 1, max_depth=max_depth)
        return redacted
    if isinstance(value, list):
        return [redact_value(item, depth=depth + 1, max_depth=max_depth) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item, depth=depth + 1, max_depth=max_depth) for item in value)
    if isinstance(value, str):
        if _is_signed_url(value):
            return redact_url(value)
        return redact_string(value)
    return value


def redact_string(value: str) -> str:
    redacted = _AUTH_HEADER_RE.sub(r"\g<1>" + REDACTED, value)
    redacted = _COOKIE_RE.sub(r"\g<1>" + REDACTED, redacted)
    return _URL_SECRET_RE.sub(r"\g<1>" + REDACTED, redacted)


def redact_url(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(url)
        if not parsed.query:
            return url
        parts: list[str] = []
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
            safe_key = urllib.parse.quote(key, safe="")
            if _is_signed_query_param(key):
                parts.append(f"{safe_key}={REDACTED}")
            else:
                parts.append(f"{safe_key}={urllib.parse.quote(value, safe='')}")
        return urllib.parse.urlunparse(parsed._replace(query="&".join(parts)))
    except Exception:
        return REDACTED


def _is_secret_key(key: str) -> bool:
    lower = key.lower()
    if lower in _SECRET_KEY_EXACT:
        return True
    if any(lower.endswith(suffix) for suffix in _SECRET_KEY_SUFFIXES):
        return True
    return any(lower.startswith(prefix) for prefix in _SECRET_KEY_PREFIXES)


def _is_provider_url_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    if normalized in {"downloadurl", "downloadhref", "providerurl", "providerhref", "signedurl"}:
        return True
    return "download" in normalized and ("url" in normalized or "href" in normalized)


def _is_signed_query_param(key: str) -> bool:
    lower = key.lower()
    return lower in _SIGNED_URL_QUERY_PARAMS or lower.startswith("x-amz-")


def _is_signed_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        return any(_is_signed_query_param(key) for key, _ in urllib.parse.parse_qsl(parsed.query))
    except Exception:
        return False


def redact_provider_metadata(value: Any, *, depth: int = 0, max_depth: int = 20) -> Any:
    if depth > max_depth:
        return REDACTED
    if isinstance(value, Mapping):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_secret_key(key_text) or _is_provider_metadata_url_key(key_text):
                redacted[key] = REDACTED
            else:
                redacted[key] = redact_provider_metadata(
                    item,
                    depth=depth + 1,
                    max_depth=max_depth,
                )
        return redacted
    if isinstance(value, list):
        return [
            redact_provider_metadata(item, depth=depth + 1, max_depth=max_depth)
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            redact_provider_metadata(item, depth=depth + 1, max_depth=max_depth)
            for item in value
        )
    if isinstance(value, str):
        if _is_signed_url(value):
            return redact_url(value)
        return redact_string(value)
    return value


def _same_origin(left_url: str, right_url: str) -> bool:
    left = urllib.parse.urlparse(left_url)
    right = urllib.parse.urlparse(right_url)
    return (
        left.scheme.lower(),
        left.netloc.lower(),
    ) == (
        right.scheme.lower(),
        right.netloc.lower(),
    )


def _is_provider_metadata_url_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return normalized in {"href", "url", "uri"} or _is_provider_url_key(key)
