from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, text


@dataclass(frozen=True, slots=True)
class ProviderRoute:
    id: str | None
    source_id: str
    provider_adapter: str
    provider_collection: str
    provider_priority: int
    provider_role: str
    status: str
    access_mode: str
    execution_policy_ref: str | None = None
    license_profile: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def route_key(self) -> str:
        return f"{self.provider_adapter}:{self.provider_collection}"


class InMemorySourceProviderRouteRepository:
    def __init__(self, routes: list[ProviderRoute] | None = None) -> None:
        self._routes = routes or []

    def list_by_source(
        self,
        source_id: str,
        *,
        include_inactive: bool = False,
    ) -> list[ProviderRoute]:
        routes = [route for route in self._routes if route.source_id == source_id]
        if not include_inactive:
            routes = [route for route in routes if route.status in {"manual_only", "active"}]
        return sorted(routes, key=lambda route: route.provider_priority)

    def get_by_route_key(self, source_id: str, route_key: str) -> ProviderRoute | None:
        for route in self._routes:
            if route.source_id == source_id and route.route_key == route_key:
                return route
        return None


class DatabaseSourceProviderRouteRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def list_by_source(
        self,
        source_id: str,
        *,
        include_inactive: bool = False,
    ) -> list[ProviderRoute]:
        status_sql = "" if include_inactive else "AND status IN ('manual_only', 'active')"
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT *
                    FROM akasha.source_provider_routes
                    WHERE source_id = :source_id
                    {status_sql}
                    ORDER BY provider_priority, provider_adapter, provider_collection
                    """
                ),
                {"source_id": source_id},
            ).mappings().all()
        return [_row_to_route(row) for row in rows]

    def get_by_route_key(self, source_id: str, route_key: str) -> ProviderRoute | None:
        provider_adapter, provider_collection = _split_route_key(route_key)
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT *
                    FROM akasha.source_provider_routes
                    WHERE source_id = :source_id
                      AND provider_adapter = :provider_adapter
                      AND provider_collection = :provider_collection
                    """
                ),
                {
                    "source_id": source_id,
                    "provider_adapter": provider_adapter,
                    "provider_collection": provider_collection,
                },
            ).mappings().first()
        return _row_to_route(row) if row else None


def _row_to_route(row: Any) -> ProviderRoute:
    return ProviderRoute(
        id=str(row.id),
        source_id=row.source_id,
        provider_adapter=row.provider_adapter,
        provider_collection=row.provider_collection,
        provider_priority=row.provider_priority,
        provider_role=row.provider_role,
        status=row.status,
        access_mode=row.access_mode,
        execution_policy_ref=row.execution_policy_ref,
        license_profile=row.license_profile,
        metadata=dict(row.metadata or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _split_route_key(route_key: str) -> tuple[str, str]:
    provider_adapter, separator, provider_collection = route_key.partition(":")
    if not separator or not provider_adapter or not provider_collection:
        raise ValueError("provider route key must use '<provider>:<collection>' format")
    return provider_adapter, provider_collection


def build_memory_routes(route_dicts: tuple[dict[str, Any], ...]) -> list[ProviderRoute]:
    now = datetime.now(UTC)
    return [
        ProviderRoute(
            id=None,
            source_id=str(route["source_id"]),
            provider_adapter=str(route["provider_adapter"]),
            provider_collection=str(route["provider_collection"]),
            provider_priority=int(route["provider_priority"]),
            provider_role=str(route["provider_role"]),
            status=str(route["status"]),
            access_mode=str(route["access_mode"]),
            execution_policy_ref=route.get("execution_policy_ref"),
            license_profile=route.get("license_profile"),
            metadata=dict(route.get("metadata", {})),
            created_at=now,
            updated_at=now,
        )
        for route in route_dicts
    ]
