from __future__ import annotations

from hashlib import sha256


def advisory_lock_key(lock_name: str) -> int:
    digest = sha256(lock_name.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def try_advisory_lock_sql(lock_name: str) -> tuple[str, dict[str, int]]:
    return "SELECT pg_try_advisory_lock(:lock_key)", {"lock_key": advisory_lock_key(lock_name)}
