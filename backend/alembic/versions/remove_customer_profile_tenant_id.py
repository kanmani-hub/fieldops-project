
"""Remove tenant_id from customer_profiles_extended.

Revision ID: remove_customer_profile_tenant_id
Revises: YOUR_CURRENT_HEAD
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "z7a91c4d62b4"
down_revision =  'e7a91c4d62b8'
branch_labels = None
depends_on = None


TABLE_NAME = "customer_profiles_extended"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    # Drop every foreign key involving tenant_id.
    foreign_keys = inspector.get_foreign_keys(TABLE_NAME)

    for fk in foreign_keys:
        if "tenant_id" in (fk.get("constrained_columns") or []):
            if fk.get("name"):
                op.drop_constraint(
                    fk["name"],
                    TABLE_NAME,
                    type_="foreignkey",
                )

    # Drop every index involving tenant_id.
    indexes = inspector.get_indexes(TABLE_NAME)

    for index in indexes:
        if "tenant_id" in (index.get("column_names") or []):
            if index.get("name"):
                op.drop_index(
                    index["name"],
                    table_name=TABLE_NAME,
                )

    # Remove the tenant_id column.
    op.drop_column(TABLE_NAME, "tenant_id")


def downgrade() -> None:
    # Restore tenant_id as nullable for existing customer profiles.
    op.add_column(
        TABLE_NAME,
        sa.Column("tenant_id", sa.String(length=50), nullable=True),
    )

    # Restore organization foreign key.
    op.create_foreign_key(
        "fk_customer_profiles_extended_tenant_id",
        TABLE_NAME,
        "organizations",
        ["tenant_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # Restore the original model indexes.
    op.create_index(
        "ix_customer_profiles_extended_tenant_id",
        TABLE_NAME,
        ["tenant_id"],
        unique=False,
    )

    op.create_index(
        "idx_cust_profile_tenant",
        TABLE_NAME,
        ["tenant_id"],
        unique=False,
    )
