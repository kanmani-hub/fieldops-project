"""add_email_communication_config

Revision ID: b15cb1f9d24e
Revises: 1a2b3c4d5e6f
Create Date: 2026-07-22 15:14:07.365846

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b15cb1f9d24e'
down_revision: Union[str, Sequence[str], None] = '1a2b3c4d5e6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        INSERT INTO communication_channel_configurations (id, channel, state, revision, updated_by, created_at, updated_at)
        VALUES ('email-config-id', 'EMAIL', 'ENABLED', 1, 'system_migration', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM communication_channel_configurations WHERE channel = 'EMAIL'")
