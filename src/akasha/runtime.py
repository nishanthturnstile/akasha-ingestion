from __future__ import annotations

from sqlalchemy import Engine

from akasha.catalog.aoi_repository import DatabaseAoiRepository, InMemoryAoiRepository
from akasha.catalog.asset_repository import (
    DatabaseSceneAssetRepository,
    InMemorySceneAssetRepository,
)
from akasha.catalog.backfill_repository import (
    DatabaseBackfillRepository,
    InMemoryBackfillRepository,
)
from akasha.catalog.field_query_repository import (
    DatabaseFieldQueryRepository,
    InMemoryFieldQueryRepository,
)
from akasha.catalog.pgstac_repository import PgstacRepository
from akasha.catalog.profile_repository import (
    DatabaseProfileRepository,
    InMemoryProfileRepository,
    build_memory_profiles,
)
from akasha.catalog.raster_repository import DatabaseRasterRepository, InMemoryRasterRepository
from akasha.catalog.repository import DatabaseSourceCatalog, StaticSourceCatalog
from akasha.catalog.scene_repository import DatabaseSceneRepository, InMemorySceneRepository
from akasha.catalog.seed_db import PROVIDER_ROUTES, THRESHOLD_PROFILES, VISUALIZATION_PROFILES
from akasha.catalog.source_route_repository import (
    DatabaseSourceProviderRouteRepository,
    InMemorySourceProviderRouteRepository,
    build_memory_routes,
)
from akasha.catalog.tile_layer_repository import (
    DatabaseTileLayerRepository,
    InMemoryTileLayerRepository,
)
from akasha.config import RuntimeBackend, Settings
from akasha.db.session import create_db_engine
from akasha.jobs.sql_store import PostgresJobStore
from akasha.jobs.stage_store import InMemoryStageStore, PostgresStageStore
from akasha.jobs.store import InMemoryJobStore
from akasha.services.titiler_tiles import TiTilerTileService
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


def create_aoi_repository(
    settings: Settings,
    engine: Engine | None = None,
) -> InMemoryAoiRepository | DatabaseAoiRepository:
    if settings.runtime_backend == RuntimeBackend.MEMORY:
        return InMemoryAoiRepository(settings.aoi_geojson_path)
    return DatabaseAoiRepository(engine or create_db_engine(settings))


def create_source_provider_route_repository(
    settings: Settings,
    engine: Engine | None = None,
) -> InMemorySourceProviderRouteRepository | DatabaseSourceProviderRouteRepository:
    if settings.runtime_backend == RuntimeBackend.MEMORY:
        return InMemorySourceProviderRouteRepository(build_memory_routes(PROVIDER_ROUTES))
    return DatabaseSourceProviderRouteRepository(engine or create_db_engine(settings))


def create_profile_repository(
    settings: Settings,
    engine: Engine | None = None,
) -> InMemoryProfileRepository | DatabaseProfileRepository:
    if settings.runtime_backend == RuntimeBackend.MEMORY:
        visualization_profiles, threshold_profiles = build_memory_profiles(
            VISUALIZATION_PROFILES,
            THRESHOLD_PROFILES,
        )
        return InMemoryProfileRepository(
            visualization_profiles=visualization_profiles,
            threshold_profiles=threshold_profiles,
        )
    return DatabaseProfileRepository(engine or create_db_engine(settings))


def create_stage_store(
    settings: Settings,
    engine: Engine | None = None,
) -> InMemoryStageStore | PostgresStageStore:
    if settings.runtime_backend == RuntimeBackend.MEMORY:
        return InMemoryStageStore()
    return PostgresStageStore(engine or create_db_engine(settings))


def create_scene_repository(
    settings: Settings,
    engine: Engine | None = None,
) -> InMemorySceneRepository | DatabaseSceneRepository:
    if settings.runtime_backend == RuntimeBackend.MEMORY:
        return InMemorySceneRepository()
    return DatabaseSceneRepository(engine or create_db_engine(settings))


def create_asset_repository(
    settings: Settings,
    engine: Engine | None = None,
) -> InMemorySceneAssetRepository | DatabaseSceneAssetRepository:
    if settings.runtime_backend == RuntimeBackend.MEMORY:
        return InMemorySceneAssetRepository()
    return DatabaseSceneAssetRepository(engine or create_db_engine(settings))


def create_raster_repository(
    settings: Settings,
    engine: Engine | None = None,
) -> InMemoryRasterRepository | DatabaseRasterRepository:
    if settings.runtime_backend == RuntimeBackend.MEMORY:
        return InMemoryRasterRepository()
    return DatabaseRasterRepository(engine or create_db_engine(settings))


def create_backfill_repository(
    settings: Settings,
    engine: Engine | None = None,
) -> InMemoryBackfillRepository | DatabaseBackfillRepository:
    if settings.runtime_backend == RuntimeBackend.MEMORY:
        return InMemoryBackfillRepository()
    return DatabaseBackfillRepository(engine or create_db_engine(settings))


def create_pgstac_repository(
    settings: Settings,
    engine: Engine | None = None,
) -> PgstacRepository | None:
    if settings.runtime_backend == RuntimeBackend.MEMORY:
        return None
    return PgstacRepository(engine or create_db_engine(settings))


def create_tile_layer_repository(
    settings: Settings,
    engine: Engine | None = None,
    *,
    raster_repository: object | None = None,
    scene_repository: object | None = None,
) -> InMemoryTileLayerRepository | DatabaseTileLayerRepository:
    if settings.runtime_backend == RuntimeBackend.MEMORY:
        return InMemoryTileLayerRepository(
            raster_repository=raster_repository,
            scene_repository=scene_repository,
        )
    return DatabaseTileLayerRepository(engine or create_db_engine(settings))


def create_titiler_tile_service(settings: Settings) -> TiTilerTileService:
    return TiTilerTileService(settings=settings)


def create_field_query_repository(
    settings: Settings,
    engine: Engine | None = None,
) -> InMemoryFieldQueryRepository | DatabaseFieldQueryRepository:
    if settings.runtime_backend == RuntimeBackend.MEMORY:
        return InMemoryFieldQueryRepository()
    return DatabaseFieldQueryRepository(engine or create_db_engine(settings))
