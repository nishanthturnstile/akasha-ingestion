from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from json import dumps
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, text

SYNC_LEDGER_STATUSES = {"running", "complete", "partial", "failed", "retry"}


@dataclass(slots=True)
class SyncLedgerRecord:
    source_id: str
    aoi_id: str
    provider_date: date
    status: str = "running"
    scene_count: int = 0
    searched_count: int = 0
    processed_count: int = 0
    failed_count: int = 0
    retry_count: int = 0
    search_complete: bool = False
    last_error: str | None = None
    heartbeat_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.status not in SYNC_LEDGER_STATUSES:
            raise ValueError(f"unsupported sync ledger status: {self.status}")


class InMemorySyncLedgerRepository:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str, date], SyncLedgerRecord] = {}

    def upsert(self, record: SyncLedgerRecord) -> SyncLedgerRecord:
        key = (record.source_id, record.aoi_id, record.provider_date)
        existing = self._records.get(key)
        now = datetime.now(UTC)
        record.id = existing.id if existing else record.id or str(uuid4())
        record.created_at = existing.created_at if existing else record.created_at or now
        record.updated_at = now
        record.heartbeat_at = record.heartbeat_at or now
        self._records[key] = record
        return record

    def get(self, *, source_id: str, aoi_id: str, provider_date: date) -> SyncLedgerRecord | None:
        return self._records.get((source_id, aoi_id, provider_date))

    def list_for_source_aoi(self, *, source_id: str, aoi_id: str) -> list[SyncLedgerRecord]:
        records = [
            record
            for record in self._records.values()
            if record.source_id == source_id and record.aoi_id == aoi_id
        ]
        return sorted(records, key=lambda record: record.provider_date)

    def incomplete_count(self, *, source_id: str, aoi_id: str) -> int:
        return sum(
            record.status != "complete"
            for record in self.list_for_source_aoi(source_id=source_id, aoi_id=aoi_id)
        )

    def latest_fully_searched_day(
        self, *, source_id: str, aoi_id: str
    ) -> date | None:
        dates = [
            record.provider_date
            for record in self.list_for_source_aoi(source_id=source_id, aoi_id=aoi_id)
            if record.search_complete
        ]
        return max(dates) if dates else None

    def processing_backlog(self, *, source_id: str, aoi_id: str) -> int:
        return sum(
            max(record.scene_count - record.processed_count, 0)
            for record in self.list_for_source_aoi(source_id=source_id, aoi_id=aoi_id)
        )

    def last_error(self, *, source_id: str, aoi_id: str) -> str | None:
        records = [
            record
            for record in self.list_for_source_aoi(source_id=source_id, aoi_id=aoi_id)
            if record.last_error
        ]
        if not records:
            return None
        return records[-1].last_error

    def heartbeat(self, *, source_id: str, aoi_id: str) -> datetime | None:
        records = self.list_for_source_aoi(source_id=source_id, aoi_id=aoi_id)
        return max((record.heartbeat_at for record in records if record.heartbeat_at), default=None)


class DatabaseSyncLedgerRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert(self, record: SyncLedgerRecord) -> SyncLedgerRecord:
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO akasha.sentinel2_sync_ledger (
                        source_id, aoi_id, provider_date, status, scene_count,
                        searched_count, processed_count, failed_count, retry_count,
                        search_complete, last_error, heartbeat_at, started_at,
                        completed_at, metadata
                    ) VALUES (
                        :source_id, :aoi_id, :provider_date, :status, :scene_count,
                        :searched_count, :processed_count, :failed_count, :retry_count,
                        :search_complete, :last_error, :heartbeat_at, :started_at,
                        :completed_at, CAST(:metadata AS jsonb)
                    )
                    ON CONFLICT (source_id, aoi_id, provider_date) DO UPDATE SET
                        status = EXCLUDED.status,
                        scene_count = EXCLUDED.scene_count,
                        searched_count = EXCLUDED.searched_count,
                        processed_count = EXCLUDED.processed_count,
                        failed_count = EXCLUDED.failed_count,
                        retry_count = EXCLUDED.retry_count,
                        search_complete = EXCLUDED.search_complete,
                        last_error = EXCLUDED.last_error,
                        heartbeat_at = EXCLUDED.heartbeat_at,
                        started_at = COALESCE(
                            akasha.sentinel2_sync_ledger.started_at, EXCLUDED.started_at
                        ),
                        completed_at = EXCLUDED.completed_at,
                        metadata = EXCLUDED.metadata,
                        updated_at = now()
                    RETURNING *
                    """
                ),
                {
                    "source_id": record.source_id,
                    "aoi_id": record.aoi_id,
                    "provider_date": record.provider_date,
                    "status": record.status,
                    "scene_count": record.scene_count,
                    "searched_count": record.searched_count,
                    "processed_count": record.processed_count,
                    "failed_count": record.failed_count,
                    "retry_count": record.retry_count,
                    "search_complete": record.search_complete,
                    "last_error": record.last_error,
                    "heartbeat_at": record.heartbeat_at,
                    "started_at": record.started_at,
                    "completed_at": record.completed_at,
                    "metadata": dumps(record.metadata or {}, sort_keys=True),
                },
            ).mappings().one()
        return _row_to_record(row)

    def get(self, *, source_id: str, aoi_id: str, provider_date: date) -> SyncLedgerRecord | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT * FROM akasha.sentinel2_sync_ledger
                    WHERE source_id = :source_id AND aoi_id = :aoi_id
                      AND provider_date = :provider_date
                    """
                ),
                {"source_id": source_id, "aoi_id": aoi_id, "provider_date": provider_date},
            ).mappings().first()
        return _row_to_record(row) if row else None

    def list_for_source_aoi(self, *, source_id: str, aoi_id: str) -> list[SyncLedgerRecord]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT * FROM akasha.sentinel2_sync_ledger
                    WHERE source_id = :source_id AND aoi_id = :aoi_id
                    ORDER BY provider_date
                    """
                ),
                {"source_id": source_id, "aoi_id": aoi_id},
            ).mappings().all()
        return [_row_to_record(row) for row in rows]

    def incomplete_count(self, *, source_id: str, aoi_id: str) -> int:
        return sum(
            record.status != "complete"
            for record in self.list_for_source_aoi(source_id=source_id, aoi_id=aoi_id)
        )

    def latest_fully_searched_day(self, *, source_id: str, aoi_id: str) -> date | None:
        records = [
            record
            for record in self.list_for_source_aoi(source_id=source_id, aoi_id=aoi_id)
            if record.search_complete
        ]
        return max((record.provider_date for record in records), default=None)

    def processing_backlog(self, *, source_id: str, aoi_id: str) -> int:
        return sum(
            max(record.scene_count - record.processed_count, 0)
            for record in self.list_for_source_aoi(source_id=source_id, aoi_id=aoi_id)
        )

    def last_error(self, *, source_id: str, aoi_id: str) -> str | None:
        records = [
            record
            for record in self.list_for_source_aoi(source_id=source_id, aoi_id=aoi_id)
            if record.last_error
        ]
        return records[-1].last_error if records else None

    def heartbeat(self, *, source_id: str, aoi_id: str) -> datetime | None:
        records = self.list_for_source_aoi(source_id=source_id, aoi_id=aoi_id)
        return max((record.heartbeat_at for record in records if record.heartbeat_at), default=None)


def _row_to_record(row: Any) -> SyncLedgerRecord:
    return SyncLedgerRecord(
        id=str(row.id),
        source_id=row.source_id,
        aoi_id=row.aoi_id,
        provider_date=row.provider_date,
        status=row.status,
        scene_count=row.scene_count,
        searched_count=row.searched_count,
        processed_count=row.processed_count,
        failed_count=row.failed_count,
        retry_count=row.retry_count,
        search_complete=row.search_complete,
        last_error=row.last_error,
        heartbeat_at=row.heartbeat_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        metadata=dict(row.metadata or {}),
    )
