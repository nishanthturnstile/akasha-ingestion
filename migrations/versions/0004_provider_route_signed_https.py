"""Allow expiring signed HTTPS provider routes.

Revision ID: 0004_route_signed_https
Revises: 0003_field_query_cache_expiry
"""

from __future__ import annotations

from alembic import op

revision = "0004_route_signed_https"
down_revision = "0003_field_query_cache_expiry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE akasha.source_provider_routes
        DROP CONSTRAINT ck_source_provider_routes_access
        """
    )
    op.execute(
        """
        ALTER TABLE akasha.source_provider_routes
        ADD CONSTRAINT ck_source_provider_routes_access CHECK (
            access_mode IN (
                'public_https',
                'signed_https',
                'requester_pays_s3',
                'official_api',
                'authenticated_download'
            )
        )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE akasha.source_provider_routes
        DROP CONSTRAINT ck_source_provider_routes_access
        """
    )
    op.execute(
        """
        UPDATE akasha.source_provider_routes
        SET access_mode = 'public_https'
        WHERE access_mode = 'signed_https'
        """
    )
    op.execute(
        """
        ALTER TABLE akasha.source_provider_routes
        ADD CONSTRAINT ck_source_provider_routes_access CHECK (
            access_mode IN (
                'public_https',
                'requester_pays_s3',
                'official_api',
                'authenticated_download'
            )
        )
        """
    )
