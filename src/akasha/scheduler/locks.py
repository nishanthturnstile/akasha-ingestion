from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from threading import Lock


def advisory_lock_key(lock_name: str) -> int:
    digest = sha256(lock_name.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def try_advisory_lock_sql(lock_name: str) -> tuple[str, dict[str, int]]:
    return "SELECT pg_try_advisory_lock(:lock_key)", {"lock_key": advisory_lock_key(lock_name)}


def source_aoi_lock_name(*, source_id: str, aoi_id: str) -> str:
    return f"source:{source_id}:aoi:{aoi_id}"


class InMemorySourceAoiLockRegistry:
    def __init__(self) -> None:
        self._guard = Lock()
        self._held: set[str] = set()

    @contextmanager
    def acquire(self, *, source_id: str, aoi_id: str):
        lock_name = source_aoi_lock_name(source_id=source_id, aoi_id=aoi_id)
        with self._guard:
            if lock_name in self._held:
                yield False
                return
            self._held.add(lock_name)
        try:
            yield True
        finally:
            with self._guard:
                self._held.discard(lock_name)


class PostgresSourceAoiLockRegistry:
    def __init__(self, engine) -> None:
        self._engine = engine

    @contextmanager
    def acquire(self, *, source_id: str, aoi_id: str):
        from sqlalchemy import text

        lock_name = source_aoi_lock_name(source_id=source_id, aoi_id=aoi_id)
        sql, params = try_advisory_lock_sql(lock_name)
        with self._engine.begin() as connection:
            acquired = bool(connection.execute(text(sql), params).scalar())
            if not acquired:
                yield False
                return
            try:
                yield True
            finally:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_key)"),
                    params,
                )
