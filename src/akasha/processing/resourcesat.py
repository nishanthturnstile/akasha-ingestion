from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Final

import numpy as np
from numpy.typing import NDArray

from akasha.processing.indices import IndexOutputProfile, calculate_index

RESOURCESAT_LISS3_BOA_SOURCE_ID: Final[str] = "resourcesat-2a-liss3-boa"
RESOURCESAT_LISS4_MX70_L2_SOURCE_ID: Final[str] = "resourcesat-2a-liss4-mx70-l2"
RESOURCESAT_AWIFS_BOA_SOURCE_ID: Final[str] = "resourcesat-2a-awifs-boa"

RESOURCESAT_SOURCE_IDS: Final[tuple[str, ...]] = (
    RESOURCESAT_LISS3_BOA_SOURCE_ID,
    RESOURCESAT_LISS4_MX70_L2_SOURCE_ID,
    RESOURCESAT_AWIFS_BOA_SOURCE_ID,
)

BHOONIDHI_LISS3_BOA_COLLECTION_ID: Final[str] = "ResourceSat-2A_LISS3_BOA"
BHOONIDHI_LISS4_MX70_L2_COLLECTION_ID: Final[str] = "ResourceSat-2A_LISS4-MX70_L2"
BHOONIDHI_AWIFS_BOA_COLLECTION_ID: Final[str] = "ResourceSat-2A_AWIFS_BOA"

BHOONIDHI_COLLECTION_IDS: Final[tuple[str, ...]] = (
    BHOONIDHI_LISS3_BOA_COLLECTION_ID,
    BHOONIDHI_LISS4_MX70_L2_COLLECTION_ID,
    BHOONIDHI_AWIFS_BOA_COLLECTION_ID,
)

INGESTION_BANGALORE_60KM_AOI_ID: Final[str] = "bangalore_60km_geodesic_aoi"
PRODUCT_BANGALORE_60KM_AOI_LABEL: Final[str] = "bangalore-60km"

INGESTION_TO_PRODUCT_AOI: Final[Mapping[str, str]] = MappingProxyType(
    {INGESTION_BANGALORE_60KM_AOI_ID: PRODUCT_BANGALORE_60KM_AOI_LABEL}
)

RESOURCESAT_SOURCE_COLLECTIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        RESOURCESAT_LISS3_BOA_SOURCE_ID: BHOONIDHI_LISS3_BOA_COLLECTION_ID,
        RESOURCESAT_LISS4_MX70_L2_SOURCE_ID: BHOONIDHI_LISS4_MX70_L2_COLLECTION_ID,
        RESOURCESAT_AWIFS_BOA_SOURCE_ID: BHOONIDHI_AWIFS_BOA_COLLECTION_ID,
    }
)

GREEN: Final[str] = "GREEN"
RED: Final[str] = "RED"
NIR: Final[str] = "NIR"
SWIR1: Final[str] = "SWIR1"

NDVI: Final[str] = "ndvi"
MSAVI: Final[str] = "msavi"
NDMI: Final[str] = "ndmi"
NDWI_GREEN_NIR: Final[str] = "ndwi_green_nir"

RESOURCESAT_MASK_METHOD: Final[str] = "akasha-threshold-mask-v1"
RESOURCESAT_REFLECTANCE_SCALE: Final[float] = 0.0001
RESOURCESAT_REFLECTANCE_OFFSET: Final[float] = 0.0
RESOURCESAT_VALID_MASK_CLASSES: Final[tuple[int, ...]] = (1, 4)


def has_exact_date_composite_provenance(
    acquisition_at: datetime | None,
    provider_metadata: Mapping[str, Any],
) -> bool:
    """Reject legacy composites that blended scenes from multiple dates."""

    if provider_metadata.get("composite") is not True:
        return True
    contributors = provider_metadata.get("contributing_scenes")
    # Older valid single-date fixtures/items may not carry contributor detail.
    # When detail is present, however, it must agree exactly with the published
    # acquisition date; this specifically quarantines the legacy rolling-window
    # composites that mislabeled temporal blends as a single observation.
    if not isinstance(contributors, list) or not contributors:
        return True
    if acquisition_at is None:
        return False
    expected = acquisition_at.date()
    for contributor in contributors:
        if not isinstance(contributor, Mapping):
            return False
        raw_datetime = contributor.get("acquisition_datetime")
        if not raw_datetime:
            return False
        try:
            actual = datetime.fromisoformat(str(raw_datetime).replace("Z", "+00:00")).date()
        except ValueError:
            return False
        if actual != expected:
            return False
    return True

RESOURCESAT_INDEX_BAND_ROLES: Final[Mapping[str, tuple[str, str]]] = MappingProxyType(
    {
        NDVI: (NIR, RED),
        MSAVI: (NIR, RED),
        NDMI: (NIR, SWIR1),
        NDWI_GREEN_NIR: (GREEN, NIR),
    }
)

RESOURCESAT_FORMULA_VERSION: Final[Mapping[str, str]] = MappingProxyType(
    {
        NDVI: "ndvi-resourcesat-v1",
        MSAVI: "msavi-resourcesat-v1",
        NDMI: "ndmi-resourcesat-v1",
        NDWI_GREEN_NIR: "ndwi-green-nir-default-v1",
    }
)


@dataclass(frozen=True, slots=True)
class ResourceSatMaskClass:
    value: int
    label: str
    description: str
    valid_for_analytics: bool


RESOURCESAT_MASK_CLASSES: Final[tuple[ResourceSatMaskClass, ...]] = (
    ResourceSatMaskClass(
        value=0,
        label="nodata",
        description="No source data or no composite coverage.",
        valid_for_analytics=False,
    ),
    ResourceSatMaskClass(
        value=1,
        label="valid",
        description="Clear land or vegetation pixel retained for analytics.",
        valid_for_analytics=True,
    ),
    ResourceSatMaskClass(
        value=2,
        label="cloud",
        description="Cloud pixel from Akasha threshold mask v1.",
        valid_for_analytics=False,
    ),
    ResourceSatMaskClass(
        value=3,
        label="shadow",
        description="Cloud-shadow pixel from Akasha threshold mask v1.",
        valid_for_analytics=False,
    ),
    ResourceSatMaskClass(
        value=4,
        label="water",
        description="Water pixel retained for NDWI and statistics provenance.",
        valid_for_analytics=True,
    ),
)

RESOURCESAT_MASK_CLASS_BY_VALUE: Final[Mapping[int, ResourceSatMaskClass]] = MappingProxyType(
    {mask_class.value: mask_class for mask_class in RESOURCESAT_MASK_CLASSES}
)


@dataclass(frozen=True, slots=True)
class ResourceSatProfile:
    source_id: str
    collection_id: str
    instrument: str
    analysis_level: str
    band_order: tuple[str, ...]
    band_roles: Mapping[str, str]
    native_resolution_m: float
    native_resolution_tolerance_m: float
    processing_resolution_setting: str
    supported_indices: tuple[str, ...]
    processing_profile_version: str
    validation_profile_version: str
    pgstac_collection: str
    source_notes: str

    def processing_resolution_m(self, settings: object | None = None) -> float:
        if settings is None:
            return self.native_resolution_m
        override = getattr(settings, self.processing_resolution_setting, None)
        return float(override) if override is not None else self.native_resolution_m

    def require_index(self, index_name: str) -> str:
        normalized = index_name.lower()
        if normalized not in self.supported_indices:
            raise ValueError(
                f"unsupported ResourceSat index for {self.source_id}: {index_name}"
            )
        return normalized

    def band_roles_for_index(self, index_name: str) -> tuple[str, str]:
        normalized = self.require_index(index_name)
        roles = RESOURCESAT_INDEX_BAND_ROLES[normalized]
        missing = [role for role in roles if role not in self.band_roles]
        if missing:
            raise ValueError(
                f"ResourceSat profile {self.source_id} has no bands for "
                f"{normalized}: {', '.join(missing)}"
            )
        return roles

    def band_names_for_index(self, index_name: str) -> tuple[str, str]:
        first_role, second_role = self.band_roles_for_index(index_name)
        return self.band_roles[first_role], self.band_roles[second_role]

    def supports_index(self, index_name: str) -> bool:
        return index_name.lower() in self.supported_indices


LISS3_PROFILE: Final[ResourceSatProfile] = ResourceSatProfile(
    source_id=RESOURCESAT_LISS3_BOA_SOURCE_ID,
    collection_id=BHOONIDHI_LISS3_BOA_COLLECTION_ID,
    instrument="LISS-3",
    analysis_level="BOA",
    band_order=("BAND2", "BAND3", "BAND4", "BAND5"),
    band_roles=MappingProxyType({GREEN: "BAND2", RED: "BAND3", NIR: "BAND4", SWIR1: "BAND5"}),
    native_resolution_m=23.5,
    native_resolution_tolerance_m=2.0,
    processing_resolution_setting="resourcesat_liss3_processing_resolution_m",
    supported_indices=(NDVI, MSAVI, NDMI, NDWI_GREEN_NIR),
    processing_profile_version="resourcesat-liss3-boa-processing-v1",
    validation_profile_version="phase3-resourcesat-liss3-boa-validation-v1",
    pgstac_collection="akasha-resourcesat-2a-liss3-boa-derived-v1",
    source_notes=(
        "LISS-3 BOA production profile; runtime readiness remains subject to "
        "successful, fresh, complete staging outputs."
    ),
)

LISS4_PROFILE: Final[ResourceSatProfile] = ResourceSatProfile(
    source_id=RESOURCESAT_LISS4_MX70_L2_SOURCE_ID,
    collection_id=BHOONIDHI_LISS4_MX70_L2_COLLECTION_ID,
    instrument="LISS-4",
    analysis_level="L2",
    band_order=("BAND2", "BAND3", "BAND4"),
    band_roles=MappingProxyType({GREEN: "BAND2", RED: "BAND3", NIR: "BAND4"}),
    native_resolution_m=5.8,
    native_resolution_tolerance_m=1.0,
    processing_resolution_setting="resourcesat_liss4_processing_resolution_m",
    supported_indices=(NDVI, MSAVI, NDWI_GREEN_NIR),
    processing_profile_version="resourcesat-liss4-mx70-l2-processing-v1",
    validation_profile_version="phase3-resourcesat-liss4-mx70-l2-validation-v1",
    pgstac_collection="akasha-resourcesat-2a-liss4-mx70-l2-derived-v1",
    source_notes="LISS-4 narrow-swath source; partial AOI coverage must be surfaced to users.",
)

AWIFS_PROFILE: Final[ResourceSatProfile] = ResourceSatProfile(
    source_id=RESOURCESAT_AWIFS_BOA_SOURCE_ID,
    collection_id=BHOONIDHI_AWIFS_BOA_COLLECTION_ID,
    instrument="AWiFS",
    analysis_level="BOA",
    band_order=("BAND2", "BAND3", "BAND4", "BAND5"),
    band_roles=MappingProxyType({GREEN: "BAND2", RED: "BAND3", NIR: "BAND4", SWIR1: "BAND5"}),
    native_resolution_m=56.0,
    native_resolution_tolerance_m=2.0,
    processing_resolution_setting="resourcesat_awifs_processing_resolution_m",
    supported_indices=(NDVI, MSAVI, NDMI, NDWI_GREEN_NIR),
    processing_profile_version="resourcesat-awifs-boa-processing-v1",
    validation_profile_version="phase3-resourcesat-awifs-boa-validation-v1",
    pgstac_collection="akasha-resourcesat-2a-awifs-boa-derived-v1",
    source_notes="AWiFS coarse regional source; field-scale quality warnings are required.",
)

RESOURCESAT_PROFILES: Final[Mapping[str, ResourceSatProfile]] = MappingProxyType(
    {
        LISS3_PROFILE.source_id: LISS3_PROFILE,
        LISS4_PROFILE.source_id: LISS4_PROFILE,
        AWIFS_PROFILE.source_id: AWIFS_PROFILE,
    }
)

RESOURCESAT_COLLECTION_PROFILES: Final[Mapping[str, ResourceSatProfile]] = MappingProxyType(
    {profile.collection_id: profile for profile in RESOURCESAT_PROFILES.values()}
)


def source_collection(source_id: str) -> str:
    return profile_for_source(source_id).collection_id


def profile_for_source(source_id: str) -> ResourceSatProfile:
    try:
        return RESOURCESAT_PROFILES[source_id]
    except KeyError as exc:
        raise ValueError(f"unsupported ResourceSat source: {source_id}") from exc


def profile_for_collection(collection_id: str) -> ResourceSatProfile:
    try:
        return RESOURCESAT_COLLECTION_PROFILES[collection_id]
    except KeyError as exc:
        raise ValueError(f"unsupported ResourceSat collection: {collection_id}") from exc


def reflectance_from_dn(
    values: NDArray[np.number],
    valid_mask: NDArray[np.bool_] | None = None,
) -> NDArray[np.float32]:
    source_values = values.astype("float32")
    mask = np.isfinite(source_values)
    if valid_mask is not None:
        mask &= valid_mask
    output = np.full(source_values.shape, np.nan, dtype="float32")
    output[mask] = (
        source_values[mask] * RESOURCESAT_REFLECTANCE_SCALE + RESOURCESAT_REFLECTANCE_OFFSET
    )
    return output


def resourcesat_valid_mask(mask_values: NDArray[np.integer]) -> NDArray[np.bool_]:
    valid_classes = np.array(RESOURCESAT_VALID_MASK_CLASSES, dtype=mask_values.dtype)
    return np.isin(mask_values, valid_classes).astype(bool)


def calculate_resourcesat_index(
    profile: ResourceSatProfile,
    index_name: str,
    bands_by_role: Mapping[str, NDArray[np.floating]],
    *,
    valid_mask: NDArray[np.bool_] | None = None,
) -> NDArray[np.float32]:
    normalized_bands = {role.upper(): values for role, values in bands_by_role.items()}
    first_role, second_role = profile.band_roles_for_index(index_name)
    try:
        first = normalized_bands[first_role]
        second = normalized_bands[second_role]
    except KeyError as exc:
        raise ValueError(
            f"missing ResourceSat band role for {profile.source_id}: {exc.args[0]}"
        ) from exc
    return calculate_index(
        profile.require_index(index_name),
        first,
        second,
        valid_mask=valid_mask,
    )


def resourcesat_output_profile(
    profile: ResourceSatProfile,
    index_name: str,
    *,
    settings: object | None = None,
) -> IndexOutputProfile:
    normalized = profile.require_index(index_name)
    return IndexOutputProfile(
        index_name=normalized,
        formula_version=RESOURCESAT_FORMULA_VERSION[normalized],
        dtype="int16",
        scale_factor=10000,
        nodata_value=-32768,
        clip_min=-1.0,
        clip_max=1.0,
        processing_resolution=profile.processing_resolution_m(settings),
    )
