from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis import Redis
from starlette.responses import Response

from akasha.config import RuntimeBackend, Settings, get_settings
from akasha.jobs.store import InMemoryJobStore
from akasha.logging import configure_logging
from akasha.providers.mock import MockProvider
from akasha.runtime import (
    create_engine_if_needed,
    create_job_store,
    create_object_store,
    create_source_catalog,
)
from akasha.schemas import (
    APIResponse,
    ErrorPayload,
    HealthResponse,
    JobResponse,
    SourceResponse,
    SyncRequest,
)
from akasha.security import require_api_key
from akasha.services.ingestion import MockIngestionService
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
    app.state.redis = redis_client
    app.state.ingestion_service = service

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
        service_obj: MockIngestionService = request.app.state.ingestion_service
        job = service_obj.start_mock_sync(payload)
        return APIResponse(success=True, data=JobResponse.from_job(job))

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
