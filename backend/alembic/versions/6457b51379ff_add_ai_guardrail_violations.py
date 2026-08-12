"""
add ai guardrail violations

Revision ID: 6457b51379ff
Revises: 5255bea12852
Create Date: 2026-07-14 07:49:41.185521
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# Revision identifiers used by Alembic.
revision: str = "6457b51379ff"

down_revision: Union[
    str,
    Sequence[str],
    None,
] = "5255bea12852"

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
    Create the immutable AI guardrail violation audit table.
    """

    op.create_table(
        "ai_guardrail_violations",

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
            "correlation_id",
            sa.String(length=100),
            nullable=True,
        ),

        sa.Column(
            "job_id",
            sa.String(length=100),
            nullable=True,
        ),

        sa.Column(
            "agent_name",
            sa.String(length=100),
            nullable=False,
        ),

        sa.Column(
            "notification_type",
            sa.String(length=100),
            nullable=True,
        ),

        sa.Column(
            "channel",
            sa.String(length=20),
            nullable=False,
        ),

        sa.Column(
            "checker_name",
            sa.String(length=100),
            nullable=False,
        ),

        sa.Column(
            "violation_code",
            sa.String(length=100),
            nullable=False,
        ),

        sa.Column(
            "category",
            sa.String(length=50),
            nullable=False,
        ),

        sa.Column(
            "severity",
            sa.String(length=20),
            nullable=False,
        ),

        sa.Column(
            "affected_field",
            sa.String(length=50),
            nullable=True,
        ),

        sa.Column(
            "safe_message",
            sa.Text(),
            nullable=False,
        ),

        sa.Column(
            "safe_metadata",
            sa.JSON(),
            nullable=False,
        ),

        sa.Column(
            "pipeline_decision",
            sa.String(length=20),
            nullable=False,
        ),

        sa.Column(
            "fallback_triggered",
            sa.Boolean(),
            nullable=False,
        ),

        sa.Column(
            "prompt_hash",
            sa.String(length=64),
            nullable=False,
        ),

        sa.Column(
            "output_hash",
            sa.String(length=64),
            nullable=False,
        ),

        sa.Column(
            "checker_latency_ms",
            sa.Float(),
            nullable=False,
        ),

        sa.Column(
            "total_latency_ms",
            sa.Float(),
            nullable=False,
        ),

        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),

        sa.PrimaryKeyConstraint(
            "id",
        ),
    )

    # Single-column lookup indexes.

    op.create_index(
        "ix_ai_guardrail_violations_tenant_id",
        "ai_guardrail_violations",
        ["tenant_id"],
        unique=False,
    )

    op.create_index(
        "ix_ai_guardrail_violations_correlation_id",
        "ai_guardrail_violations",
        ["correlation_id"],
        unique=False,
    )

    op.create_index(
        "ix_ai_guardrail_violations_job_id",
        "ai_guardrail_violations",
        ["job_id"],
        unique=False,
    )

    op.create_index(
        "ix_ai_guardrail_violations_agent_name",
        "ai_guardrail_violations",
        ["agent_name"],
        unique=False,
    )

    op.create_index(
        "ix_ai_guardrail_violations_notification_type",
        "ai_guardrail_violations",
        ["notification_type"],
        unique=False,
    )

    op.create_index(
        "ix_ai_guardrail_violations_channel",
        "ai_guardrail_violations",
        ["channel"],
        unique=False,
    )

    op.create_index(
        "ix_ai_guardrail_violations_checker_name",
        "ai_guardrail_violations",
        ["checker_name"],
        unique=False,
    )

    op.create_index(
        "ix_ai_guardrail_violations_violation_code",
        "ai_guardrail_violations",
        ["violation_code"],
        unique=False,
    )

    op.create_index(
        "ix_ai_guardrail_violations_category",
        "ai_guardrail_violations",
        ["category"],
        unique=False,
    )

    op.create_index(
        "ix_ai_guardrail_violations_severity",
        "ai_guardrail_violations",
        ["severity"],
        unique=False,
    )

    op.create_index(
        "ix_ai_guardrail_violations_pipeline_decision",
        "ai_guardrail_violations",
        ["pipeline_decision"],
        unique=False,
    )

    op.create_index(
        "ix_ai_guardrail_violations_fallback_triggered",
        "ai_guardrail_violations",
        ["fallback_triggered"],
        unique=False,
    )

    op.create_index(
        "ix_ai_guardrail_violations_prompt_hash",
        "ai_guardrail_violations",
        ["prompt_hash"],
        unique=False,
    )

    op.create_index(
        "ix_ai_guardrail_violations_output_hash",
        "ai_guardrail_violations",
        ["output_hash"],
        unique=False,
    )

    op.create_index(
        "ix_ai_guardrail_violations_created_at",
        "ai_guardrail_violations",
        ["created_at"],
        unique=False,
    )

    # Composite indexes used by audit searches.

    op.create_index(
        "idx_ai_guardrail_tenant_created",
        "ai_guardrail_violations",
        [
            "tenant_id",
            "created_at",
        ],
        unique=False,
    )

    op.create_index(
        "idx_ai_guardrail_job_created",
        "ai_guardrail_violations",
        [
            "job_id",
            "created_at",
        ],
        unique=False,
    )

    op.create_index(
        "idx_ai_guardrail_code_created",
        "ai_guardrail_violations",
        [
            "violation_code",
            "created_at",
        ],
        unique=False,
    )


def downgrade() -> None:
    """
    Remove the AI guardrail violation audit table.

    PostgreSQL automatically removes the table's indexes when
    the table is dropped.
    """

    op.drop_table(
        "ai_guardrail_violations"
    )