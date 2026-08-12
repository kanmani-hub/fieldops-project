"""add_prompt_template_uniqueness

Revision ID: 89cc7a683f0e
Revises: 98a1f0f3f6c0
Create Date: 2026-07-21 12:49:11.117652

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '89cc7a683f0e'
down_revision: Union[str, Sequence[str], None] = '98a1f0f3f6c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # First, handle any existing duplicates if there are any by keeping the one with highest ID
    op.execute('''
        DELETE FROM notification_templates a USING (
            SELECT MAX(id) as id, tenant_id, agent_type, channel, locale, type, version
            FROM notification_templates 
            GROUP BY tenant_id, agent_type, channel, locale, type, version
            HAVING COUNT(*) > 1
        ) b 
        WHERE a.tenant_id = b.tenant_id 
          AND a.agent_type = b.agent_type 
          AND a.channel = b.channel 
          AND a.locale = b.locale 
          AND a.type = b.type 
          AND a.version = b.version 
          AND a.id < b.id
    ''')

    op.create_unique_constraint(
        'uq_notification_templates_lookup',
        'notification_templates',
        ['tenant_id', 'agent_type', 'channel', 'locale', 'type', 'version']
    )


def downgrade() -> None:
    op.drop_constraint('uq_notification_templates_lookup', 'notification_templates', type_='unique')
