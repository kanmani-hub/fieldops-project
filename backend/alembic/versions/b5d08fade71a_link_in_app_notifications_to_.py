"""link in app notifications to organizations

Revision ID: b5d08fade71a
Revises: 9d0d85694834
Create Date: 2026-07-30 14:27:19.410429
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b5d08fade71a"
down_revision: Union[str, Sequence[str], None] = "9d0d85694834"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Link in-app notifications to organizations."""

    op.add_column(
        "notifications",
        sa.Column(
            "tenant_id",
            sa.String(length=50),
            nullable=True,
        ),
    )

    op.execute("""
        UPDATE notifications AS n
        SET tenant_id = t.tenant_id
        FROM technicians AS t
        WHERE n.tech_id = t.tech_id
          AND n.tenant_id IS NULL
          AND t.tenant_id IS NOT NULL
    """)

    op.execute("""
        UPDATE notifications AS n
        SET tenant_id = j.tenant_id
        FROM jobs AS j
        WHERE n.tenant_id IS NULL
          AND n.job_id IS NOT NULL
          AND n.job_id = j.id::text
          AND j.tenant_id IS NOT NULL
    """)

    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM notifications
                WHERE tenant_id IS NULL
            ) THEN
                RAISE EXCEPTION
                    'Cannot migrate notifications: some rows have no tenant_id';
            END IF;
        END
        $$;
    """)

    op.create_index(
        "ix_notifications_tenant_id",
        "notifications",
        ["tenant_id"],
        unique=False,
    )

    op.create_foreign_key(
        "fk_notifications_organization",
        "notifications",
        "organizations",
        ["tenant_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.alter_column(
        "notifications",
        "tenant_id",
        existing_type=sa.String(length=50),
        nullable=False,
    )


def downgrade() -> None:
    """Remove organization link from in-app notifications."""

    op.drop_constraint(
        "fk_notifications_organization",
        "notifications",
        type_="foreignkey",
    )

    op.drop_index(
        "ix_notifications_tenant_id",
        table_name="notifications",
    )

    op.drop_column(
        "notifications",
        "tenant_id",
    )