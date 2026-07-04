from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from json import dumps
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, text


@dataclass(slots=True)
class RasterOutputRecord:
    id: str | None
    scene_id: str
    output_kind: str
    object_path: str
    index_name: str | None = None
    checksum_sha256: str | None = None
    formula_version: str | None = None
    processing_profile_version: str | None = None
    dtype: str | None = None
    scale_factor: float | None = None
    offset: float | None = None
    nodata_value: float | int | None = None
    min_value: float | None = None
    max_value: float | None = None
    native_resolution: float | None = None
    processing_resolution: float | None = None
    display_resolution: float | None = None
    crs: str | None = None
    cloud_mask_version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None


class InMemoryRasterRepository:
    def __init__(self) -> None:
        self._outputs: dict[tuple[str, str, str, str, str, float], RasterOutputRecord] = {}

    def upsert_derived_index(self, output: RasterOutputRecord) -> RasterOutputRecord:
        key = _derived_key(output)
        existing = self._outputs.get(key)
        output.id = existing.id if existing else output.id or str(uuid4())
        output.created_at = existing.created_at if existing else datetime.now(UTC)
        self._outputs[key] = output
        return output

    def get_for_scene_index(
        self,
        *,
        scene_id: str,
        index_name: str,
    ) -> RasterOutputRecord | None:
        normalized = index_name.lower()
        return next(
            (
                output
                for output in self._outputs.values()
                if output.scene_id == scene_id and output.index_name == normalized
            ),
            None,
        )

    def get(self, output_id: str) -> RasterOutputRecord | None:
        return next(
            (output for output in self._outputs.values() if output.id == output_id),
            None,
        )

    def list_for_scene_ids(
        self,
        scene_ids: list[str],
        *,
        index_name: str | None = None,
    ) -> list[RasterOutputRecord]:
        scene_id_set = set(scene_ids)
        normalized = index_name.lower() if index_name is not None else None
        outputs = [
            output
            for output in self._outputs.values()
            if output.scene_id in scene_id_set
            and output.output_kind == "derived_index"
            and (normalized is None or output.index_name == normalized)
        ]
        outputs.sort(key=lambda output: output.created_at or datetime.min.replace(tzinfo=UTC))
        return outputs


class DatabaseRasterRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert_derived_index(self, output: RasterOutputRecord) -> RasterOutputRecord:
        _derived_key(output)
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO akasha.raster_outputs (
                        scene_id,
                        output_kind,
                        index_name,
                        object_path,
                        checksum_sha256,
                        formula_version,
                        processing_profile_version,
                        metadata,
                        dtype,
                        scale_factor,
                        offset_value,
                        nodata_value,
                        min_value,
                        max_value,
                        native_resolution,
                        processing_resolution,
                        display_resolution,
                        crs,
                        cloud_mask_version
                    )
                    VALUES (
                        CAST(:scene_id AS uuid),
                        :output_kind,
                        :index_name,
                        :object_path,
                        :checksum_sha256,
                        :formula_version,
                        :processing_profile_version,
                        CAST(:metadata AS jsonb),
                        :dtype,
                        :scale_factor,
                        :offset_value,
                        :nodata_value,
                        :min_value,
                        :max_value,
                        :native_resolution,
                        :processing_resolution,
                        :display_resolution,
                        :crs,
                        :cloud_mask_version
                    )
                    ON CONFLICT (
                        scene_id,
                        output_kind,
                        index_name,
                        formula_version,
                        processing_profile_version,
                        processing_resolution
                    )
                    WHERE output_kind = 'derived_index'
                    DO UPDATE
                    SET object_path = EXCLUDED.object_path,
                        checksum_sha256 = EXCLUDED.checksum_sha256,
                        metadata = EXCLUDED.metadata,
                        dtype = EXCLUDED.dtype,
                        scale_factor = EXCLUDED.scale_factor,
                        offset_value = EXCLUDED.offset_value,
                        nodata_value = EXCLUDED.nodata_value,
                        min_value = EXCLUDED.min_value,
                        max_value = EXCLUDED.max_value,
                        native_resolution = EXCLUDED.native_resolution,
                        display_resolution = EXCLUDED.display_resolution,
                        crs = EXCLUDED.crs,
                        cloud_mask_version = EXCLUDED.cloud_mask_version
                    RETURNING *
                    """
                ),
                _raster_params(output),
            ).mappings().one()
        return _row_to_raster(row)

    def get_for_scene_index(
        self,
        *,
        scene_id: str,
        index_name: str,
    ) -> RasterOutputRecord | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT *
                    FROM akasha.raster_outputs
                    WHERE scene_id = CAST(:scene_id AS uuid)
                      AND output_kind = 'derived_index'
                      AND index_name = :index_name
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"scene_id": scene_id, "index_name": index_name.lower()},
            ).mappings().first()
        return _row_to_raster(row) if row else None

    def get(self, output_id: str) -> RasterOutputRecord | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT *
                    FROM akasha.raster_outputs
                    WHERE id = CAST(:output_id AS uuid)
                    """
                ),
                {"output_id": output_id},
            ).mappings().first()
        return _row_to_raster(row) if row else None

    def list_for_scene_ids(
        self,
        scene_ids: list[str],
        *,
        index_name: str | None = None,
    ) -> list[RasterOutputRecord]:
        if not scene_ids:
            return []
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT *
                    FROM akasha.raster_outputs
                    WHERE scene_id = ANY(CAST(:scene_ids AS uuid[]))
                      AND output_kind = 'derived_index'
                      AND (
                          CAST(:index_name AS text) IS NULL
                          OR index_name = CAST(:index_name AS text)
                      )
                    ORDER BY created_at ASC
                    """
                ),
                {
                    "scene_ids": scene_ids,
                    "index_name": index_name.lower() if index_name is not None else None,
                },
            ).mappings().all()
        return [_row_to_raster(row) for row in rows]


def _derived_key(output: RasterOutputRecord) -> tuple[str, str, str, str, str, float]:
    if output.output_kind != "derived_index":
        raise ValueError("upsert_derived_index only accepts output_kind='derived_index'")
    if (
        output.index_name is None
        or output.formula_version is None
        or output.processing_profile_version is None
        or output.processing_resolution is None
    ):
        raise ValueError("derived index outputs require deterministic identity fields")
    return (
        output.scene_id,
        output.output_kind,
        output.index_name,
        output.formula_version,
        output.processing_profile_version,
        output.processing_resolution,
    )


def _raster_params(output: RasterOutputRecord) -> dict[str, Any]:
    return {
        "scene_id": output.scene_id,
        "output_kind": output.output_kind,
        "index_name": output.index_name,
        "object_path": output.object_path,
        "checksum_sha256": output.checksum_sha256,
        "formula_version": output.formula_version,
        "processing_profile_version": output.processing_profile_version,
        "metadata": dumps(output.metadata, sort_keys=True),
        "dtype": output.dtype,
        "scale_factor": output.scale_factor,
        "offset_value": output.offset,
        "nodata_value": output.nodata_value,
        "min_value": output.min_value,
        "max_value": output.max_value,
        "native_resolution": output.native_resolution,
        "processing_resolution": output.processing_resolution,
        "display_resolution": output.display_resolution,
        "crs": output.crs,
        "cloud_mask_version": output.cloud_mask_version,
    }


def _row_to_raster(row: Any) -> RasterOutputRecord:
    return RasterOutputRecord(
        id=str(row.id),
        scene_id=str(row.scene_id),
        output_kind=row.output_kind,
        index_name=row.index_name,
        object_path=row.object_path,
        checksum_sha256=row.checksum_sha256,
        formula_version=row.formula_version,
        processing_profile_version=row.processing_profile_version,
        dtype=row.dtype,
        scale_factor=float(row.scale_factor) if row.scale_factor is not None else None,
        offset=float(row.offset_value) if row.offset_value is not None else None,
        nodata_value=row.nodata_value,
        min_value=float(row.min_value) if row.min_value is not None else None,
        max_value=float(row.max_value) if row.max_value is not None else None,
        native_resolution=(
            float(row.native_resolution) if row.native_resolution is not None else None
        ),
        processing_resolution=(
            float(row.processing_resolution) if row.processing_resolution is not None else None
        ),
        display_resolution=(
            float(row.display_resolution) if row.display_resolution is not None else None
        ),
        crs=row.crs,
        cloud_mask_version=row.cloud_mask_version,
        metadata=dict(row.metadata or {}),
        created_at=row.created_at,
    )
