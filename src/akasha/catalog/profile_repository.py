from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, text


@dataclass(frozen=True, slots=True)
class VisualizationProfile:
    id: str | None
    index_name: str
    value_domain_min: float
    value_domain_max: float
    display_min: float
    display_max: float
    palette_json: list[dict[str, Any]]
    nodata_color: str
    version: str
    is_default: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ThresholdProfile:
    id: str | None
    profile_key: str
    index_name: str
    classes_json: list[dict[str, Any]]
    version: str
    crop: str | None = None
    season: str | None = None
    aoi_id: str | None = None
    source_id: str | None = None
    is_default: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


class InMemoryProfileRepository:
    def __init__(
        self,
        *,
        visualization_profiles: list[VisualizationProfile] | None = None,
        threshold_profiles: list[ThresholdProfile] | None = None,
    ) -> None:
        self._visualization_profiles = visualization_profiles or []
        self._threshold_profiles = threshold_profiles or []

    def get_default_visualization(self, index_name: str) -> VisualizationProfile | None:
        normalized = index_name.lower()
        return next(
            (
                profile
                for profile in self._visualization_profiles
                if profile.index_name == normalized and profile.is_default
            ),
            None,
        )

    def get_default_threshold(
        self,
        index_name: str,
        *,
        source_id: str | None = None,
    ) -> ThresholdProfile | None:
        normalized = index_name.lower()
        candidates = [
            profile
            for profile in self._threshold_profiles
            if profile.index_name == normalized and profile.is_default
        ]
        if source_id is not None:
            source_match = next(
                (profile for profile in candidates if profile.source_id == source_id),
                None,
            )
            if source_match is not None:
                return source_match
        return next((profile for profile in candidates if profile.source_id is None), None)


class DatabaseProfileRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_default_visualization(self, index_name: str) -> VisualizationProfile | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT *
                    FROM akasha.visualization_profiles
                    WHERE index_name = :index_name
                      AND is_default = true
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"index_name": index_name.lower()},
            ).mappings().first()
        return _row_to_visualization(row) if row else None

    def get_default_threshold(
        self,
        index_name: str,
        *,
        source_id: str | None = None,
    ) -> ThresholdProfile | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT *
                    FROM akasha.threshold_profiles
                    WHERE index_name = :index_name
                      AND is_default = true
                      AND (source_id = :source_id OR source_id IS NULL)
                    ORDER BY (source_id = :source_id) DESC NULLS LAST, created_at DESC
                    LIMIT 1
                    """
                ),
                {"index_name": index_name.lower(), "source_id": source_id},
            ).mappings().first()
        return _row_to_threshold(row) if row else None


def build_memory_profiles(
    visualization_dicts: tuple[dict[str, Any], ...],
    threshold_dicts: tuple[dict[str, Any], ...],
) -> tuple[list[VisualizationProfile], list[ThresholdProfile]]:
    visualization_profiles = [
        VisualizationProfile(
            id=None,
            index_name=str(profile["index_name"]),
            value_domain_min=float(profile["value_domain_min"]),
            value_domain_max=float(profile["value_domain_max"]),
            display_min=float(profile["display_min"]),
            display_max=float(profile["display_max"]),
            palette_json=[dict(item) for item in profile["palette_json"]],
            nodata_color=str(profile["nodata_color"]),
            version=str(profile["version"]),
            is_default=bool(profile["is_default"]),
        )
        for profile in visualization_dicts
    ]
    threshold_profiles = [
        ThresholdProfile(
            id=None,
            profile_key=str(profile["profile_key"]),
            index_name=str(profile["index_name"]),
            classes_json=[dict(item) for item in profile["classes_json"]],
            version=str(profile["version"]),
            crop=profile.get("crop"),
            season=profile.get("season"),
            aoi_id=profile.get("aoi_id"),
            source_id=profile.get("source_id"),
            is_default=bool(profile["is_default"]),
        )
        for profile in threshold_dicts
    ]
    return visualization_profiles, threshold_profiles


def _row_to_visualization(row: Any) -> VisualizationProfile:
    return VisualizationProfile(
        id=str(row.id),
        index_name=row.index_name,
        value_domain_min=float(row.value_domain_min),
        value_domain_max=float(row.value_domain_max),
        display_min=float(row.display_min),
        display_max=float(row.display_max),
        palette_json=[dict(item) for item in row.palette_json],
        nodata_color=row.nodata_color,
        version=row.version,
        is_default=row.is_default,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _row_to_threshold(row: Any) -> ThresholdProfile:
    return ThresholdProfile(
        id=str(row.id),
        profile_key=row.profile_key,
        index_name=row.index_name,
        classes_json=[dict(item) for item in row.classes_json],
        version=row.version,
        crop=row.crop,
        season=row.season,
        aoi_id=row.aoi_id,
        source_id=row.source_id,
        is_default=row.is_default,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
