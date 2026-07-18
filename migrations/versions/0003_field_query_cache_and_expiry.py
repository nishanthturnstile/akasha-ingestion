"""Add bounded field-query cache identity.

Revision ID: 0003_field_query_cache_and_expiry
Revises: 0002_phase2_s2_slice
"""

from __future__ import annotations

from alembic import op

revision = "0003_field_query_cache_and_expiry"
down_revision = "0002_phase2_s2_slice"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE akasha.field_queries ADD COLUMN geometry_hash text")
    op.execute("ALTER TABLE akasha.field_queries ADD COLUMN analysis_version text")
    op.execute(
        """
        CREATE INDEX ix_field_queries_sar_cache
        ON akasha.field_queries (
            selected_scene_id,
            geometry_hash,
            index_name,
            analysis_version,
            expires_at
        )
        WHERE geometry_hash IS NOT NULL AND analysis_version IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX ix_field_queries_expiry
        ON akasha.field_queries (expires_at)
        WHERE expires_at IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS akasha.ix_field_queries_expiry")
    op.execute("DROP INDEX IF EXISTS akasha.ix_field_queries_sar_cache")
    op.execute("ALTER TABLE akasha.field_queries DROP COLUMN IF EXISTS analysis_version")
    op.execute("ALTER TABLE akasha.field_queries DROP COLUMN IF EXISTS geometry_hash")
