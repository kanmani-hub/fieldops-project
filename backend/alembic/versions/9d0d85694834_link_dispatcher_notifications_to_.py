"""link dispatcher notifications to organizations

Revision ID: 9d0d85694834
Revises: ff9988776655
Create Date: 2026-07-29 17:10:03.356670
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "9d0d85694834"
down_revision: Union[str, Sequence[str], None] = "ff9988776655"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


FK_NAME = "fk_dispatcher_notifications_tenant_id_organizations"


def upgrade() -> None:
    op.create_foreign_key(
        FK_NAME,
        "dispatcher_notifications",
        "organizations",
        ["tenant_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        FK_NAME,
        "dispatcher_notifications",
        type_="foreignkey",
    )