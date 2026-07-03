from __future__ import annotations

from akasha.jobs.sql_store import _public_result_metadata


def test_public_result_metadata_strips_internal_object_fields() -> None:
    metadata = _public_result_metadata(
        {
            "object_path": "raw/mock/sentinel-2-l2a/object.mock",
            "checksum_sha256": "abc123",
            "backfill_summary": {"searched_count": 0},
            "mode": "full_pipeline",
        }
    )

    assert metadata == {
        "backfill_summary": {"searched_count": 0},
        "mode": "full_pipeline",
    }
