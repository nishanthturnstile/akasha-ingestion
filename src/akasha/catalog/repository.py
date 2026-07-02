from __future__ import annotations

from json import loads
from typing import Any

from sqlalchemy import Engine, text

from akasha.catalog.seed import list_seed_sources
from akasha.schemas import SourceResponse


def _json_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        return [str(item) for item in loads(value)]
    return list(value)


class StaticSourceCatalog:
    def list_sources(self) -> list[SourceResponse]:
        return list_seed_sources()


class DatabaseSourceCatalog:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def list_sources(self) -> list[SourceResponse]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT source_id,
                           catalog_slug,
                           provider_adapter,
                           instrument_mode,
                           analysis_level,
                           schedule_state,
                           product_exposure,
                           supported_indices
                    FROM akasha.satellite_sources
                    ORDER BY source_id
                    """
                )
            ).mappings().all()
        return [
            SourceResponse(
                source_id=row.source_id,
                catalog_slug=row.catalog_slug,
                provider_adapter=row.provider_adapter,
                instrument_mode=row.instrument_mode,
                analysis_level=row.analysis_level,
                schedule_state=row.schedule_state,
                product_exposure=row.product_exposure,
                supported_indices=_json_list(row.supported_indices),
            )
            for row in rows
        ]
