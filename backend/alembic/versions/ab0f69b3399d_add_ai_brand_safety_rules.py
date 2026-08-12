"""add ai brand safety rules

Revision ID: ab0f69b3399d
Revises: 6457b51379ff
Create Date: 2026-07-14 10:41:36.692731

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'ab0f69b3399d'

down_revision: Union[
    str,
    Sequence[str],
    None,
] = "6457b51379ff"

branch_labels: Union[
    str,
    Sequence[str],
    None,
] = None

depends_on: Union[
    str,
    Sequence[str],
    None,
] = None


def upgrade() -> None:
    """
    Create tenant-configurable AI brand-safety rules.
    """

    op.create_table(
        "ai_brand_safety_rules",

        sa.Column(
            "id",
            sa.String(length=36),
            nullable=False,
        ),

        sa.Column(
            "tenant_id",
            sa.String(length=50),
            nullable=False,
        ),

        sa.Column(
            "rule_id",
            sa.String(length=100),
            nullable=False,
        ),

        sa.Column(
            "category",
            sa.String(length=30),
            nullable=False,
        ),

        sa.Column(
            "match_type",
            sa.String(length=20),
            nullable=False,
        ),

        sa.Column(
            "pattern",
            sa.String(length=200),
            nullable=False,
        ),

        sa.Column(
            "severity",
            sa.String(length=20),
            nullable=False,
        ),

        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
        ),

        sa.Column(
            "case_sensitive",
            sa.Boolean(),
            nullable=False,
        ),

        sa.Column(
            "created_by",
            sa.String(length=100),
            nullable=False,
        ),

        sa.Column(
            "updated_by",
            sa.String(length=100),
            nullable=True,
        ),

        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),

        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),

        sa.CheckConstraint(
            "category IN ("
            "'COMPETITOR', "
            "'POLITICAL', "
            "'OFF_BRAND', "
            "'BLOCKED_PHRASE'"
            ")",
            name="ck_ai_brand_safety_category",
        ),

        sa.CheckConstraint(
            "match_type IN ("
            "'WORD', "
            "'PHRASE'"
            ")",
            name="ck_ai_brand_safety_match_type",
        ),

        sa.CheckConstraint(
            "severity IN ("
            "'INFO', "
            "'WARNING', "
            "'ERROR', "
            "'CRITICAL'"
            ")",
            name="ck_ai_brand_safety_severity",
        ),

        sa.PrimaryKeyConstraint(
            "id",
        ),

        sa.UniqueConstraint(
            "tenant_id",
            "rule_id",
            name="uq_ai_brand_safety_tenant_rule",
        ),
    )

    op.create_index(
        "ix_ai_brand_safety_rules_tenant_id",
        "ai_brand_safety_rules",
        ["tenant_id"],
        unique=False,
    )

    op.create_index(
        "idx_ai_brand_safety_tenant_active",
        "ai_brand_safety_rules",
        [
            "tenant_id",
            "active",
        ],
        unique=False,
    )

    op.create_index(
        "idx_ai_brand_safety_tenant_category",
        "ai_brand_safety_rules",
        [
            "tenant_id",
            "category",
        ],
        unique=False,
    )


def downgrade() -> None:
    """
    Remove tenant-configurable AI brand-safety rules.
    """

    op.drop_index(
        "idx_ai_brand_safety_tenant_category",
        table_name="ai_brand_safety_rules",
    )

    op.drop_index(
        "idx_ai_brand_safety_tenant_active",
        table_name="ai_brand_safety_rules",
    )

    op.drop_index(
        "ix_ai_brand_safety_rules_tenant_id",
        table_name="ai_brand_safety_rules",
    )

    op.drop_table(
        "ai_brand_safety_rules"
    )