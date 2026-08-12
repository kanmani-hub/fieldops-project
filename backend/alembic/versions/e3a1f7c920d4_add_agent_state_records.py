"""
add agent_state_records

Story 1.5 — Persistent Agent State.

Creates the ``agent_state_records`` table which stores safe operational
snapshots of FieldOps AI agent runtime state.

Privacy rules enforced at the application layer
------------------------------------------------
The following are forbidden from this table:
- API keys, authentication secrets, tokens, or passwords
- AI provider prompts or completions
- Customer names, addresses, phone numbers, or email addresses
- Technician GPS, coordinates, or private information
- Full stack traces (safe error summaries only, max 500 chars)
- Message body contents or full job payloads

Revision ID: e3a1f7c920d4
Revises: 6457b51379ff
Create Date: 2026-07-18 08:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# Revision identifiers used by Alembic.
revision: str = "e3a1f7c920d4"

down_revision: Union[
    str,
    Sequence[str],
    None,
] = "ab0f69b3399d"

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
    Create the agent_state_records table.

    Stores at most one row per (tenant_id, agent_id) pair.
    The unique constraint is created explicitly so downgrade can drop it
    by name on all supported databases.
    """

    op.create_table(
        "agent_state_records",

        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
            autoincrement=True,
        ),

        sa.Column(
            "agent_id",
            sa.String(length=36),
            nullable=False,
            comment="UUID4 agent instance identifier.",
        ),

        sa.Column(
            "agent_type",
            sa.String(length=50),
            nullable=False,
            comment="AITask string value for this agent.",
        ),

        sa.Column(
            "tenant_id",
            sa.String(length=50),
            nullable=False,
            comment="Tenant that owns this agent.",
        ),

        sa.Column(
            "agent_version",
            sa.String(length=50),
            nullable=False,
            server_default="1.0",
            comment="Agent implementation version.",
        ),

        sa.Column(
            "state",
            sa.String(length=30),
            nullable=False,
            comment="AgentState string value.",
        ),

        sa.Column(
            "correlation_id",
            sa.String(length=100),
            nullable=True,
            comment="Correlation ID from the last lifecycle event.",
        ),

        sa.Column(
            "last_error",
            sa.String(length=500),
            nullable=True,
            comment="Safe error summary — no stack traces or secrets.",
        ),

        sa.Column(
            "safe_metadata",
            sa.JSON(),
            nullable=True,
            comment="Safe operational metadata — no customer data or secrets.",
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

        sa.PrimaryKeyConstraint(
            "id",
            name="pk_agent_state_records",
        ),

        sa.UniqueConstraint(
            "tenant_id",
            "agent_id",
            name="uq_agent_state_tenant_agent",
        ),
    )

    # Named index — tenant_id for list_by_tenant queries.
    op.create_index(
        "idx_agent_state_tenant",
        "agent_state_records",
        ["tenant_id"],
        unique=False,
    )

    # Named index — agent_id for direct agent lookups.
    op.create_index(
        "idx_agent_state_agent_id",
        "agent_state_records",
        ["agent_id"],
        unique=False,
    )

    # Composite index — tenant + state for filtered operational queries.
    op.create_index(
        "idx_agent_state_tenant_state",
        "agent_state_records",
        ["tenant_id", "state"],
        unique=False,
    )


def downgrade() -> None:
    """
    Remove the agent_state_records table and all its indexes.

    On PostgreSQL the indexes are removed automatically with the table.
    On other databases the explicit drop_index calls ensure clean removal
    regardless of engine behaviour.
    """

    op.drop_index(
        "idx_agent_state_tenant_state",
        table_name="agent_state_records",
    )

    op.drop_index(
        "idx_agent_state_agent_id",
        table_name="agent_state_records",
    )

    op.drop_index(
        "idx_agent_state_tenant",
        table_name="agent_state_records",
    )

    op.drop_table("agent_state_records")
