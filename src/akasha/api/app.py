from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import APIKeyHeader
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis import Redis

from akasha.config import RuntimeBackend, Settings, get_settings
from akasha.jobs.store import InMemoryJobStore
from akasha.logging import configure_logging
from akasha.providers.mock import MockProvider
from akasha.runtime import (
    create_aoi_repository,
    create_asset_repository,
    create_backfill_repository,
    create_engine_if_needed,
    create_field_query_repository,
    create_job_store,
    create_object_store,
    create_pgstac_repository,
    create_profile_repository,
    create_raster_repository,
    create_scene_repository,
    create_source_catalog,
    create_source_provider_route_repository,
    create_stage_store,
    create_tile_layer_repository,
)
from akasha.schemas import (
    APIResponse,
    ErrorPayload,
    FieldIndexRequest,
    FieldIndexResponse,
    HealthResponse,
    JobResponse,
    SourceResponse,
    SyncRequest,
)
from akasha.security import require_api_key
from akasha.services.analytics import AnalyticsService
from akasha.services.ingestion import MockIngestionService
from akasha.services.sentinel2_ingestion import Sentinel2IngestionService
from akasha.storage.object_store import InMemoryObjectStore

API_ERROR_RESPONSES = {
    status.HTTP_401_UNAUTHORIZED: {
        "model": APIResponse[None],
        "description": "API key is missing or invalid.",
    },
    status.HTTP_503_SERVICE_UNAVAILABLE: {
        "model": APIResponse[None],
        "description": "API key authentication is not configured.",
    },
}
VALIDATION_ERROR_RESPONSE = {
    status.HTTP_422_UNPROCESSABLE_ENTITY: {
        "model": APIResponse[None],
        "description": "Request validation failed.",
    }
}
NOT_FOUND_ERROR_RESPONSE = {
    status.HTTP_404_NOT_FOUND: {
        "model": APIResponse[None],
        "description": "Requested job was not found.",
    }
}


def create_app(
    settings: Settings | None = None,
    *,
    job_store: InMemoryJobStore | None = None,
    object_store: InMemoryObjectStore | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings.log_level)

    engine = create_engine_if_needed(app_settings)
    store = job_store or create_job_store(app_settings, engine)
    objects = object_store or create_object_store(app_settings)
    source_catalog = create_source_catalog(app_settings, engine)
    aoi_repository = create_aoi_repository(app_settings, engine)
    source_provider_routes = create_source_provider_route_repository(app_settings, engine)
    profile_repository = create_profile_repository(app_settings, engine)
    stage_store = create_stage_store(app_settings, engine)
    scene_repository = create_scene_repository(app_settings, engine)
    asset_repository = create_asset_repository(app_settings, engine)
    raster_repository = create_raster_repository(app_settings, engine)
    backfill_repository = create_backfill_repository(app_settings, engine)
    pgstac_repository = create_pgstac_repository(app_settings, engine)
    tile_layer_repository = create_tile_layer_repository(app_settings, engine)
    field_query_repository = create_field_query_repository(app_settings, engine)
    redis_client = (
        None
        if app_settings.runtime_backend == RuntimeBackend.MEMORY
        else Redis.from_url(app_settings.redis_url)
    )
    service = MockIngestionService(
        job_store=store,
        object_store=objects,
        provider=MockProvider(),
        settings=app_settings,
    )
    sentinel2_service = Sentinel2IngestionService(
        job_store=store,
        stage_store=stage_store,
        aoi_repository=aoi_repository,
        scene_repository=scene_repository,
        asset_repository=asset_repository,
        raster_repository=raster_repository,
        object_store=objects,
        backfill_repository=backfill_repository,
        pgstac_repository=pgstac_repository,
        tile_layer_repository=tile_layer_repository,
        settings=app_settings,
    )
    analytics_service = AnalyticsService(
        field_query_repository=field_query_repository,
        scene_repository=scene_repository,
        raster_repository=raster_repository,
        tile_layer_repository=tile_layer_repository,
        object_store=objects,
        profile_repository=profile_repository,
        settings=app_settings,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if redis_client is not None:
                redis_client.close()

    app = FastAPI(
        title="Akasha Ingestion API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.job_store = store
    app.state.object_store = objects
    app.state.source_catalog = source_catalog
    app.state.aoi_repository = aoi_repository
    app.state.source_provider_routes = source_provider_routes
    app.state.profile_repository = profile_repository
    app.state.stage_store = stage_store
    app.state.scene_repository = scene_repository
    app.state.asset_repository = asset_repository
    app.state.raster_repository = raster_repository
    app.state.backfill_repository = backfill_repository
    app.state.pgstac_repository = pgstac_repository
    app.state.tile_layer_repository = tile_layer_repository
    app.state.field_query_repository = field_query_repository
    app.state.redis = redis_client
    app.state.ingestion_service = service
    app.state.sentinel2_ingestion_service = sentinel2_service
    app.state.analytics_service = analytics_service

    api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    def api_key_dependency(
        request: Request,
        api_key: str | None = Depends(api_key_header),
    ) -> None:
        require_api_key(api_key, request.app.state.settings)

    auth_dependency: Callable[[], None] = Depends(api_key_dependency)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "request failed"
        return JSONResponse(
            status_code=exc.status_code,
            content=APIResponse[None](
                success=False,
                error=ErrorPayload(code=str(exc.status_code), message=detail),
            ).model_dump(),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        _: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=APIResponse[None](
                success=False,
                error=ErrorPayload(
                    code=str(status.HTTP_422_UNPROCESSABLE_ENTITY),
                    message=_validation_error_message(exc),
                ),
            ).model_dump(),
        )

    @app.get("/health", response_model=APIResponse[HealthResponse])
    def health(request: Request) -> APIResponse[HealthResponse]:
        settings_obj: Settings = request.app.state.settings
        services = {"api": "ok", "mock_provider": "ok"}
        if settings_obj.runtime_backend == RuntimeBackend.MEMORY:
            services["job_store"] = "ok"
            services["object_store"] = "ok"
        else:
            services["database"] = _probe(lambda: request.app.state.job_store.health_check())
            services["minio"] = _probe(lambda: request.app.state.object_store.health_check())
            services["redis"] = _probe(lambda: request.app.state.redis.ping())
        health_status = "ok" if all(value == "ok" for value in services.values()) else "degraded"
        return APIResponse(
            success=True,
            data=HealthResponse(
                status=health_status,
                environment=settings_obj.environment,
                services=services,
            ),
        )

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get(
        "/api/v1/sources",
        response_model=APIResponse[list[SourceResponse]],
        dependencies=[auth_dependency],
        responses=API_ERROR_RESPONSES,
    )
    def sources() -> APIResponse[list[SourceResponse]]:
        return APIResponse(success=True, data=source_catalog.list_sources())

    @app.post(
        "/api/v1/ingestion/sync",
        response_model=APIResponse[JobResponse],
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[auth_dependency],
        responses=API_ERROR_RESPONSES | VALIDATION_ERROR_RESPONSE,
    )
    def sync(request: Request, payload: SyncRequest) -> APIResponse[JobResponse]:
        if payload.job_type == "sentinel2_backfill":
            sentinel2_service_obj: Sentinel2IngestionService = (
                request.app.state.sentinel2_ingestion_service
            )
            job = sentinel2_service_obj.start_backfill(payload)
        else:
            service_obj: MockIngestionService = request.app.state.ingestion_service
            job = service_obj.start_mock_sync(payload)
        return APIResponse(success=True, data=JobResponse.from_job(job))

    @app.post(
        "/api/v1/analytics/field-index",
        response_model=APIResponse[FieldIndexResponse],
        dependencies=[auth_dependency],
        responses=API_ERROR_RESPONSES | VALIDATION_ERROR_RESPONSE,
    )
    def field_index(
        request: Request,
        payload: FieldIndexRequest,
    ) -> APIResponse[FieldIndexResponse]:
        service_obj: AnalyticsService = request.app.state.analytics_service
        try:
            result = service_obj.field_index(payload)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        return APIResponse(success=True, data=result)

    @app.get(
        "/api/v1/analytics/field-index/{query_id}",
        response_model=APIResponse[dict[str, object]],
        responses=API_ERROR_RESPONSES,
    )
    def field_index_stats(
        request: Request,
        query_id: str,
        op: str,
        exp: int,
        kid: str,
        sig: str,
    ) -> APIResponse[dict[str, object]]:
        if op != "stats":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="wrong operation")
        service_obj: AnalyticsService = request.app.state.analytics_service
        signing = service_obj._signing
        query_hash = signing.query_hash(f"{query_id}:stats")
        if not signing.verify(
            method="GET",
            operation="stats",
            resource_id=query_id,
            path_template=f"/api/v1/analytics/field-index/{query_id}",
            geometry_or_query_hash=query_hash,
            expires_at=exp,
            key_id=kid,
            signature=sig,
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid signature",
            )
        payload = service_obj.stats_for_query(query_id)
        if payload is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="query not found")
        return APIResponse(success=True, data=payload)

    @app.get("/tiles/{layer_id}/{z}/{x}/{y}.png", responses=API_ERROR_RESPONSES)
    def tile(
        request: Request,
        layer_id: str,
        z: int,
        x: int,
        y: int,
        op: str,
        exp: int,
        kid: str,
        sig: str,
    ) -> Response:
        del z, x, y
        if op != "tile":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="wrong operation")
        service_obj: AnalyticsService = request.app.state.analytics_service
        layer = request.app.state.tile_layer_repository.get(layer_id)
        if layer is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="layer not found")
        signing = service_obj._signing
        query_hash = signing.query_hash(f"{layer_id}:tile")
        if not signing.verify(
            method="GET",
            operation="tile",
            resource_id=layer_id,
            path_template=f"/tiles/{layer_id}/{{z}}/{{x}}/{{y}}.png",
            geometry_or_query_hash=query_hash,
            expires_at=exp,
            key_id=kid,
            signature=sig,
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid signature",
            )
        return Response(content=_TRANSPARENT_PNG, media_type="image/png")

    @app.get(
        "/api/v1/jobs",
        response_model=APIResponse[list[JobResponse]],
        dependencies=[auth_dependency],
        responses=API_ERROR_RESPONSES,
    )
    def jobs(request: Request) -> APIResponse[list[JobResponse]]:
        store_obj: InMemoryJobStore = request.app.state.job_store
        return APIResponse(
            success=True,
            data=[JobResponse.from_job(job) for job in store_obj.list()],
        )

    @app.get(
        "/api/v1/jobs/{job_id}",
        response_model=APIResponse[JobResponse],
        dependencies=[auth_dependency],
        responses=API_ERROR_RESPONSES | VALIDATION_ERROR_RESPONSE | NOT_FOUND_ERROR_RESPONSE,
    )
    def job_detail(request: Request, job_id: UUID) -> APIResponse[JobResponse]:
        store_obj: InMemoryJobStore = request.app.state.job_store
        job = store_obj.get(str(job_id))
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
        return APIResponse(success=True, data=JobResponse.from_job(job))

    return app


app = create_app()


def _probe(callback: Callable[[], object]) -> str:
    try:
        callback()
        return "ok"
    except Exception:
        return "unavailable"


def _validation_error_message(exc: RequestValidationError) -> str:
    messages: list[str] = []
    for error in exc.errors():
        location = ".".join(
            str(part) for part in error.get("loc", ()) if part not in {"body", "query", "path"}
        )
        message = str(error.get("msg", "invalid request"))
        messages.append(f"{location}: {message}" if location else message)
    return "; ".join(messages) or "request validation failed"


_TRANSPARENT_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff"
    b"\xff?\x00\x05\xfe\x02\xfeA\xe2&\xb3\x00\x00\x00\x00IEND\xaeB`\x82"
)
