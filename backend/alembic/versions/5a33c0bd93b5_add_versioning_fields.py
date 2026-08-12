"""Add soft deletion and full template-version snapshots.

Revision ID: 5a33c0bd93b5
Revises: 89cc7a683f0e
Create Date: 2026-07-21 11:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# Alembic revision identifiers.
revision: str = "5a33c0bd93b5"
down_revision: Union[str, None] = "89cc7a683f0e"
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


def _normalize_variables(value):
    """Return a safe JSON list for legacy variable values."""

    if value is None:
        return []

    if isinstance(value, list):
        return value

    if isinstance(value, tuple):
        return list(value)

    if isinstance(value, str):
        import json

        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []

        if isinstance(parsed, list):
            return parsed

    return []


def _is_deleted_value(value) -> bool:
    """Normalize legacy boolean representations."""

    return value in (
        True,
        1,
        "1",
        "true",
        "True",
    )


def upgrade() -> None:
    """Add Task 5.2 fields, backfill history, and add invariants."""

    # ------------------------------------------------------
    # 1. Add soft-delete fields to notification_templates.
    # ------------------------------------------------------

    op.add_column(
        "notification_templates",
        sa.Column(
            "is_deleted",
            sa.Boolean(),
            nullable=True,
        ),
    )

    op.add_column(
        "notification_templates",
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.add_column(
        "notification_templates",
        sa.Column(
            "deleted_by",
            sa.String(length=100),
            nullable=True,
        ),
    )

    op.create_index(
        op.f(
            "ix_notification_templates_is_deleted"
        ),
        "notification_templates",
        ["is_deleted"],
        unique=False,
    )

    # ------------------------------------------------------
    # 2. Add snapshot fields to template_versions.
    #
    # Do not add title_template, body_template,
    # created_by, created_at, change_summary, or
    # is_active because they already existed.
    # ------------------------------------------------------

    op.add_column(
        "template_versions",
        sa.Column(
            "name",
            sa.String(length=100),
            nullable=True,
        ),
    )

    op.add_column(
        "template_versions",
        sa.Column(
            "type",
            sa.String(length=50),
            nullable=True,
        ),
    )

    op.add_column(
        "template_versions",
        sa.Column(
            "channel",
            sa.String(length=20),
            nullable=True,
        ),
    )

    op.add_column(
        "template_versions",
        sa.Column(
            "locale",
            sa.String(length=10),
            nullable=True,
        ),
    )

    op.add_column(
        "template_versions",
        sa.Column(
            "format",
            sa.String(length=20),
            nullable=True,
        ),
    )

    op.add_column(
        "template_versions",
        sa.Column(
            "agent_type",
            sa.String(length=50),
            nullable=True,
        ),
    )

    op.add_column(
        "template_versions",
        sa.Column(
            "variables",
            sa.JSON(),
            nullable=True,
        ),
    )

    op.add_column(
        "template_versions",
        sa.Column(
            "template_is_active",
            sa.Boolean(),
            nullable=True,
        ),
    )

    op.add_column(
        "template_versions",
        sa.Column(
            "restored_from_version",
            sa.Integer(),
            nullable=True,
        ),
    )

    # ------------------------------------------------------
    # 3. Add soft-delete fields to template_versions.
    # ------------------------------------------------------

    op.add_column(
        "template_versions",
        sa.Column(
            "is_deleted",
            sa.Boolean(),
            nullable=True,
        ),
    )

    op.add_column(
        "template_versions",
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.add_column(
        "template_versions",
        sa.Column(
            "deleted_by",
            sa.String(length=100),
            nullable=True,
        ),
    )

    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    false_value = 0 if is_sqlite else False
    true_value = 1 if is_sqlite else True

    # ------------------------------------------------------
    # 4. Initialize soft-delete values.
    # ------------------------------------------------------

    bind.execute(
        text(
            """
            UPDATE notification_templates
            SET is_deleted = :false_value
            WHERE is_deleted IS NULL
            """
        ),
        {
            "false_value": false_value,
        },
    )

    bind.execute(
        text(
            """
            UPDATE template_versions
            SET is_deleted = :false_value
            WHERE is_deleted IS NULL
            """
        ),
        {
            "false_value": false_value,
        },
    )

    # ------------------------------------------------------
    # 5. Backfill existing historical snapshots.
    #
    # Existing title/body, author, timestamp, summary,
    # and active-history state are preserved.
    # ------------------------------------------------------

    bind.execute(
        text(
            """
            UPDATE template_versions
            SET
                name = (
                    SELECT name
                    FROM notification_templates
                    WHERE id =
                        template_versions.template_id
                ),
                type = (
                    SELECT type
                    FROM notification_templates
                    WHERE id =
                        template_versions.template_id
                ),
                channel = (
                    SELECT channel
                    FROM notification_templates
                    WHERE id =
                        template_versions.template_id
                ),
                locale = (
                    SELECT locale
                    FROM notification_templates
                    WHERE id =
                        template_versions.template_id
                ),
                format = (
                    SELECT format
                    FROM notification_templates
                    WHERE id =
                        template_versions.template_id
                ),
                agent_type = (
                    SELECT agent_type
                    FROM notification_templates
                    WHERE id =
                        template_versions.template_id
                ),
                variables = (
                    SELECT variables
                    FROM notification_templates
                    WHERE id =
                        template_versions.template_id
                ),
                template_is_active = (
                    SELECT is_active
                    FROM notification_templates
                    WHERE id =
                        template_versions.template_id
                )
            WHERE EXISTS (
                SELECT 1
                FROM notification_templates
                WHERE id =
                    template_versions.template_id
            )
            """
        )
    )

    # ------------------------------------------------------
    # 6. Create a baseline history row for templates
    # that have no version history.
    # ------------------------------------------------------

    templates_without_history = bind.execute(
        text(
            """
            SELECT
                id,
                name,
                type,
                channel,
                locale,
                format,
                title_template,
                body_template,
                variables,
                agent_type,
                version,
                is_active
            FROM notification_templates AS template
            WHERE NOT EXISTS (
                SELECT 1
                FROM template_versions AS version_row
                WHERE version_row.template_id =
                    template.id
            )
            ORDER BY id
            """
        )
    ).mappings().all()

    baseline_insert = text(
        """
        INSERT INTO template_versions (
            template_id,
            version_number,
            name,
            type,
            channel,
            locale,
            format,
            title_template,
            body_template,
            variables,
            agent_type,
            created_by,
            change_summary,
            is_active,
            template_is_active,
            restored_from_version,
            is_deleted
        ) VALUES (
            :template_id,
            :version_number,
            :name,
            :type,
            :channel,
            :locale,
            :format,
            :title_template,
            :body_template,
            :variables,
            :agent_type,
            'system_migration',
            'Legacy baseline import',
            :version_active,
            :template_active,
            NULL,
            :is_deleted
        )
        """
    ).bindparams(
        sa.bindparam(
            "variables",
            type_=sa.JSON(),
        )
    )

    for template_row in templates_without_history:
        existing_version = template_row["version"]

        if (
            existing_version is None
            or existing_version < 1
        ):
            existing_version = 1

        template_active = bool(
            template_row["is_active"]
        )

        bind.execute(
            baseline_insert,
            {
                "template_id": template_row["id"],
                "version_number": existing_version,
                "name": template_row["name"],
                "type": template_row["type"],
                "channel": template_row["channel"],
                "locale": template_row["locale"],
                "format": template_row["format"],
                "title_template": (
                    template_row["title_template"]
                ),
                "body_template": (
                    template_row["body_template"]
                ),
                "variables": _normalize_variables(
                    template_row["variables"]
                ),
                "agent_type": (
                    template_row["agent_type"]
                ),
                # This baseline row is the current
                # active history version.
                "version_active": true_value,
                # This records whether the live prompt
                # itself was enabled or disabled.
                "template_active": (
                    1 if is_sqlite
                    and template_active
                    else 0 if is_sqlite
                    else template_active
                ),
                "is_deleted": false_value,
            },
        )

    # ------------------------------------------------------
    # 7. Repair duplicate or invalid version numbers.
    #
    # Existing valid version numbers are preserved.
    # Duplicate or invalid rows receive the next available
    # positive version number. No history row is deleted.
    # ------------------------------------------------------

    template_ids = bind.execute(
        text(
            """
            SELECT DISTINCT template_id
            FROM template_versions
            ORDER BY template_id
            """
        )
    ).scalars().all()

    active_versions = {}

    for template_id in template_ids:
        version_rows = bind.execute(
            text(
                """
                SELECT
                    id,
                    version_number,
                    created_at,
                    is_deleted
                FROM template_versions
                WHERE template_id = :template_id
                ORDER BY
                    CASE
                        WHEN version_number IS NULL
                             OR version_number < 1
                        THEN 1
                        ELSE 0
                    END,
                    version_number ASC,
                    created_at ASC,
                    id ASC
                """
            ),
            {
                "template_id": template_id,
            },
        ).mappings().all()

        valid_numbers = [
            row["version_number"]
            for row in version_rows
            if (
                row["version_number"] is not None
                and row["version_number"] >= 1
            )
        ]

        next_available = (
            max(valid_numbers) + 1
            if valid_numbers
            else 1
        )

        used_numbers = set()
        non_deleted_candidates = []

        for version_row in version_rows:
            current_number = (
                version_row["version_number"]
            )

            number_is_valid = (
                current_number is not None
                and current_number >= 1
                and current_number
                not in used_numbers
            )

            if number_is_valid:
                assigned_number = current_number
            else:
                while (
                    next_available in used_numbers
                ):
                    next_available += 1

                assigned_number = next_available
                next_available += 1

                bind.execute(
                    text(
                        """
                        UPDATE template_versions
                        SET version_number =
                            :version_number
                        WHERE id = :version_id
                        """
                    ),
                    {
                        "version_number": (
                            assigned_number
                        ),
                        "version_id": (
                            version_row["id"]
                        ),
                    },
                )

            used_numbers.add(
                assigned_number
            )

            if not _is_deleted_value(
                version_row["is_deleted"]
            ):
                non_deleted_candidates.append(
                    (
                        assigned_number,
                        version_row["id"],
                    )
                )

        if non_deleted_candidates:
            active_number, active_id = max(
                non_deleted_candidates,
                key=lambda item: (
                    item[0],
                    item[1],
                ),
            )

            active_versions[
                template_id
            ] = (
                active_id,
                active_number,
            )

    # ------------------------------------------------------
    # 8. Ensure exactly one active, non-deleted
    # history version per template.
    # ------------------------------------------------------

    bind.execute(
        text(
            """
            UPDATE template_versions
            SET is_active = :false_value
            """
        ),
        {
            "false_value": false_value,
        },
    )

    for (
        template_id,
        (
            active_id,
            active_number,
        ),
    ) in active_versions.items():
        bind.execute(
            text(
                """
                UPDATE template_versions
                SET is_active = :true_value
                WHERE id = :active_id
                """
            ),
            {
                "true_value": true_value,
                "active_id": active_id,
            },
        )

        bind.execute(
            text(
                """
                UPDATE notification_templates
                SET version = :active_number
                WHERE id = :template_id
                """
            ),
            {
                "active_number": active_number,
                "template_id": template_id,
            },
        )

    # ------------------------------------------------------
    # 9. Make soft-delete flags non-null and add the
    # per-template version-number uniqueness constraint.
    # ------------------------------------------------------

    with op.batch_alter_table(
        "notification_templates",
        schema=None,
    ) as batch_op:
        batch_op.alter_column(
            "is_deleted",
            existing_type=sa.Boolean(),
            nullable=False,
        )

    with op.batch_alter_table(
        "template_versions",
        schema=None,
    ) as batch_op:
        batch_op.alter_column(
            "is_deleted",
            existing_type=sa.Boolean(),
            nullable=False,
        )

        batch_op.create_unique_constraint(
            "uq_template_version",
            [
                "template_id",
                "version_number",
            ],
        )

    # ------------------------------------------------------
    # 10. Add the partial unique index that prevents two
    # active, non-deleted history versions.
    # ------------------------------------------------------

    op.create_index(
        "idx_active_template_version",
        "template_versions",
        ["template_id"],
        unique=True,
        postgresql_where=sa.text(
            """
            is_active IS TRUE
            AND is_deleted IS FALSE
            """
        ),
        sqlite_where=sa.text(
            """
            is_active = 1
            AND is_deleted = 0
            """
        ),
    )


def downgrade() -> None:
    """Remove only fields and constraints added by Task 5.2."""

    op.drop_index(
        "idx_active_template_version",
        table_name="template_versions",
    )

    with op.batch_alter_table(
        "template_versions",
        schema=None,
    ) as batch_op:
        batch_op.drop_constraint(
            "uq_template_version",
            type_="unique",
        )

        batch_op.drop_column(
            "deleted_by"
        )
        batch_op.drop_column(
            "deleted_at"
        )
        batch_op.drop_column(
            "is_deleted"
        )
        batch_op.drop_column(
            "restored_from_version"
        )
        batch_op.drop_column(
            "template_is_active"
        )
        batch_op.drop_column(
            "variables"
        )
        batch_op.drop_column(
            "agent_type"
        )
        batch_op.drop_column(
            "format"
        )
        batch_op.drop_column(
            "locale"
        )
        batch_op.drop_column(
            "channel"
        )
        batch_op.drop_column(
            "type"
        )
        batch_op.drop_column(
            "name"
        )

    op.drop_index(
        op.f(
            "ix_notification_templates_is_deleted"
        ),
        table_name="notification_templates",
    )

    with op.batch_alter_table(
        "notification_templates",
        schema=None,
    ) as batch_op:
        batch_op.drop_column(
            "deleted_by"
        )
        batch_op.drop_column(
            "deleted_at"
        )
        batch_op.drop_column(
            "is_deleted"
        )