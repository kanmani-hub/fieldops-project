"""add_managed_prompt_template_registry

Revision ID: 98a1f0f3f6c0
Revises: e3a1f7c920d4
Create Date: 2026-07-21 11:57:04.914512

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import json

# revision identifiers, used by Alembic.
revision: str = '98a1f0f3f6c0'
down_revision: Union[str, Sequence[str], None] = 'e3a1f7c920d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add columns safely (nullable first)
    op.add_column('notification_templates', sa.Column('variables', sa.JSON(), nullable=True))
    op.add_column('notification_templates', sa.Column('tenant_id', sa.String(length=50), nullable=True))
    op.add_column('notification_templates', sa.Column('agent_type', sa.String(length=50), nullable=True))
    
    # Backfill existing rows
    # Existing variables should contain: customer_name, technician_name, job_title, eta, action_urls
    default_vars = json.dumps(["customer_name", "technician_name", "job_title", "eta", "action_urls"])
    
    op.execute(f"UPDATE notification_templates SET tenant_id = '**platform**' WHERE tenant_id IS NULL")
    op.execute(f"UPDATE notification_templates SET agent_type = 'CommsAgent' WHERE agent_type IS NULL")
    op.execute(f"UPDATE notification_templates SET variables = '{default_vars}' WHERE variables IS NULL")

    # Make them non-null now
    op.alter_column('notification_templates', 'variables', nullable=False)
    op.alter_column('notification_templates', 'tenant_id', nullable=False)
    op.alter_column('notification_templates', 'agent_type', nullable=False)

    # Add the index
    op.create_index('idx_managed_prompt_lookup', 'notification_templates', ['tenant_id', 'agent_type', 'channel', 'locale', 'type', 'is_active'], unique=False)
    op.create_index(op.f('ix_notification_templates_agent_type'), 'notification_templates', ['agent_type'], unique=False)
    op.create_index(op.f('ix_notification_templates_tenant_id'), 'notification_templates', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_notification_templates_tenant_id'), table_name='notification_templates')
    op.drop_index(op.f('ix_notification_templates_agent_type'), table_name='notification_templates')
    op.drop_index('idx_managed_prompt_lookup', table_name='notification_templates')
    
    op.drop_column('notification_templates', 'agent_type')
    op.drop_column('notification_templates', 'tenant_id')
    op.drop_column('notification_templates', 'variables')
