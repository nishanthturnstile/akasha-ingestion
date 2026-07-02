from __future__ import annotations

from hashlib import sha256
from hmac import compare_digest

from fastapi import HTTPException, status

from akasha.config import Settings


def hash_api_key(api_key: str) -> str:
    return sha256(api_key.encode("utf-8")).hexdigest()


def configured_api_key_hashes(settings: Settings) -> dict[str, str]:
    configured: dict[str, str] = {}
    for item in settings.api_key_hashes.split(","):
        if not item.strip():
            continue
        if ":" not in item:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="api key hash configuration is invalid",
            )
        name, key_hash = item.split(":", 1)
        configured[name.strip()] = key_hash.strip()
    return configured


def require_api_key(api_key: str | None, settings: Settings) -> None:
    hashes = configured_api_key_hashes(settings)
    if not hashes:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="api key authentication is not configured",
        )
    if not api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing api key")

    candidate = hash_api_key(api_key)
    if not any(compare_digest(candidate, stored_hash) for stored_hash in hashes.values()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")
