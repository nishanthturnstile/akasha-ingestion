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


def create_resourcesat_ingestion_service(
    settings: Settings,
    engine: Engine | None = None,
    *,
    job_store: object | None = None,
    stage_store: object | None = None,
    aoi_repository: object | None = None,
    source_provider_route_repository: object | None = None,
    scene_repository: object | None = None,
    asset_repository: object | None = None,
    raster_repository: object | None = None,
    object_store: object | None = None,
    pgstac_repository: object | None = None,
    tile_layer_repository: object | None = None,
):
    from akasha.providers.bhoonidhi import BhoonidhiClient
    from akasha.services.resourcesat_ingestion import ResourceSatIngestionService

    resolved_job_store = job_store or create_job_store(settings, engine)
    resolved_stage_store = stage_store or create_stage_store(settings, engine)
    resolved_aoi_repository = aoi_repository or create_aoi_repository(settings, engine)
    resolved_source_provider_route_repository = (
        source_provider_route_repository
        or create_source_provider_route_repository(settings, engine)
    )
    resolved_scene_repository = scene_repository or create_scene_repository(settings, engine)
    resolved_asset_repository = asset_repository or create_asset_repository(settings, engine)
    resolved_raster_repository = raster_repository or create_raster_repository(settings, engine)
    resolved_object_store = object_store or create_object_store(settings)
    resolved_pgstac_repository = pgstac_repository
    if resolved_pgstac_repository is None:
        resolved_pgstac_repository = create_pgstac_repository(settings, engine)
    resolved_tile_layer_repository = tile_layer_repository or create_tile_layer_repository(
        settings,
        engine,
        raster_repository=resolved_raster_repository,
        scene_repository=resolved_scene_repository,
    )
    return ResourceSatIngestionService(
        job_store=resolved_job_store,
        stage_store=resolved_stage_store,
        settings=settings,
        aoi_repository=resolved_aoi_repository,
        object_store=resolved_object_store,
        bhoonidhi_client=BhoonidhiClient(settings),
        source_provider_route_repository=resolved_source_provider_route_repository,
        scene_repository=resolved_scene_repository,
        asset_repository=resolved_asset_repository,
        raster_repository=resolved_raster_repository,
        tile_layer_repository=resolved_tile_layer_repository,
        pgstac_repository=resolved_pgstac_repository,
    )


def create_eos04_ingestion_service(
    settings: Settings,
    engine: Engine | None = None,
    *,
    job_store: object | None = None,
    stage_store: object | None = None,
    aoi_repository: object | None = None,
    source_provider_route_repository: object | None = None,
    scene_repository: object | None = None,
    asset_repository: object | None = None,
    object_store: object | None = None,
    pgstac_repository: object | None = None,
):
    from akasha.providers.bhoonidhi import BhoonidhiClient
    from akasha.services.eos04_ingestion import Eos04IngestionService

    resolved_pgstac_repository = pgstac_repository
    if resolved_pgstac_repository is None:
        resolved_pgstac_repository = create_pgstac_repository(settings, engine)
    return Eos04IngestionService(
        job_store=job_store or create_job_store(settings, engine),
        stage_store=stage_store or create_stage_store(settings, engine),
        settings=settings,
        aoi_repository=aoi_repository or create_aoi_repository(settings, engine),
        object_store=object_store or create_object_store(settings),
        bhoonidhi_client=BhoonidhiClient(settings),
        source_provider_route_repository=(
            source_provider_route_repository
            or create_source_provider_route_repository(settings, engine)
        ),
        scene_repository=scene_repository or create_scene_repository(settings, engine),
        asset_repository=asset_repository or create_asset_repository(settings, engine),
        pgstac_repository=resolved_pgstac_repository,
    )


def create_nisar_ingestion_service(
    settings: Settings,
    engine: Engine | None = None,
    *,
    job_store: object | None = None,
    stage_store: object | None = None,
    aoi_repository: object | None = None,
    source_provider_route_repository: object | None = None,
    scene_repository: object | None = None,
    asset_repository: object | None = None,
    object_store: object | None = None,
    pgstac_repository: object | None = None,
):
    from akasha.providers.bhoonidhi import BhoonidhiClient
    from akasha.services.nisar_ingestion import NisarIngestionService

    resolved_pgstac_repository = pgstac_repository
    if resolved_pgstac_repository is None:
        resolved_pgstac_repository = create_pgstac_repository(settings, engine)
    return NisarIngestionService(
        job_store=job_store or create_job_store(settings, engine),
        stage_store=stage_store or create_stage_store(settings, engine),
        settings=settings,
        aoi_repository=aoi_repository or create_aoi_repository(settings, engine),
        object_store=object_store or create_object_store(settings),
        bhoonidhi_client=BhoonidhiClient(settings),
        source_provider_route_repository=(
            source_provider_route_repository
            or create_source_provider_route_repository(settings, engine)
        ),
        scene_repository=scene_repository or create_scene_repository(settings, engine),
        asset_repository=asset_repository or create_asset_repository(settings, engine),
        pgstac_repository=resolved_pgstac_repository,
    )


def create_landsat_ingestion_service(
    settings: Settings,
    engine: Engine | None = None,
    **overrides,
):
    from akasha.services.landsat_ingestion import LandsatIngestionService

    scene_repository = overrides.get("scene_repository") or create_scene_repository(
        settings, engine
    )
    raster_repository = overrides.get("raster_repository") or create_raster_repository(
        settings, engine
    )
    return LandsatIngestionService(
        job_store=overrides.get("job_store") or create_job_store(settings, engine),
        stage_store=overrides.get("stage_store") or create_stage_store(settings, engine),
        backfill_repository=(
            overrides.get("backfill_repository") or create_backfill_repository(settings, engine)
        ),
        settings=settings,
        aoi_repository=overrides.get("aoi_repository") or create_aoi_repository(settings, engine),
        scene_repository=scene_repository,
        asset_repository=overrides.get("asset_repository")
        or create_asset_repository(settings, engine),
        raster_repository=raster_repository,
        object_store=overrides.get("object_store") or create_object_store(settings),
        pgstac_repository=overrides.get("pgstac_repository")
        if "pgstac_repository" in overrides
        else create_pgstac_repository(settings, engine),
        tile_layer_repository=overrides.get("tile_layer_repository")
        or create_tile_layer_repository(
            settings,
            engine,
            raster_repository=raster_repository,
            scene_repository=scene_repository,
        ),
        provider=overrides.get("provider"),
        mirroring_service=overrides.get("mirroring_service"),
    )


def create_field_query_repository(
    settings: Settings,
    engine: Engine | None = None,
) -> InMemoryFieldQueryRepository | DatabaseFieldQueryRepository:
    if settings.runtime_backend == RuntimeBackend.MEMORY:
        return InMemoryFieldQueryRepository()
    return DatabaseFieldQueryRepository(engine or create_db_engine(settings))
