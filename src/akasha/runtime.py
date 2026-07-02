from __future__ import annotations

from sqlalchemy import Engine

from akasha.catalog.repository import DatabaseSourceCatalog, StaticSourceCatalog
from akasha.config import RuntimeBackend, Settings
from akasha.db.session import create_db_engine
from akasha.jobs.sql_store import PostgresJobStore
from akasha.jobs.store import InMemoryJobStore
from akasha.storage.object_store import InMemoryObjectStore, MinIOObjectStore


def create_engine_if_needed(settings: Settings) -> Engine | None:
    if settings.runtime_backend == RuntimeBackend.MEMORY:
        return None
    return create_db_engine(settings)


def create_job_store(
    settings: Settings,
    engine: Engine | None = None,
) -> InMemoryJobStore | PostgresJobStore:
    if settings.runtime_backend == RuntimeBackend.MEMORY:
        return InMemoryJobStore()
    return PostgresJobStore(engine or create_db_engine(settings))


def create_object_store(settings: Settings) -> InMemoryObjectStore | MinIOObjectStore:
    if settings.runtime_backend == RuntimeBackend.MEMORY:
        return InMemoryObjectStore()
    return MinIOObjectStore(settings)


def create_source_catalog(
    settings: Settings,
    engine: Engine | None = None,
) -> StaticSourceCatalog | DatabaseSourceCatalog:
    if settings.runtime_backend == RuntimeBackend.MEMORY:
        return StaticSourceCatalog()
    return DatabaseSourceCatalog(engine or create_db_engine(settings))
