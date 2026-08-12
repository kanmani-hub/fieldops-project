"""create_eta_history_table

Revision ID: 5255bea12852
Revises: a77e3e45ea49
Create Date: 2026-06-25 14:51:29.429177

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5255bea12852'
down_revision: Union[str, Sequence[str], None] = 'a77e3e45ea49'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('eta_history',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=False),
        sa.Column('eta', sa.DateTime(timezone=True), nullable=False),
        sa.Column('duration_minutes', sa.Float(), nullable=False),
        sa.Column('distance_km', sa.Float(), nullable=False),
        sa.Column('traffic_delay_minutes', sa.Float(), nullable=True),
        sa.Column('source_ping_id', sa.String(length=36), nullable=False),
        sa.Column('calculated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('tenant_id', sa.String(length=50), nullable=False),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_eta_history_calculated_at'), 'eta_history', ['calculated_at'], unique=False)
    op.create_index(op.f('ix_eta_history_job_id'), 'eta_history', ['job_id'], unique=False)
    op.create_index(op.f('ix_eta_history_source_ping_id'), 'eta_history', ['source_ping_id'], unique=False)
    op.create_index(op.f('ix_eta_history_tenant_id'), 'eta_history', ['tenant_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_eta_history_tenant_id'), table_name='eta_history')
    op.drop_index(op.f('ix_eta_history_source_ping_id'), table_name='eta_history')
    op.drop_index(op.f('ix_eta_history_job_id'), table_name='eta_history')
    op.drop_index(op.f('ix_eta_history_calculated_at'), table_name='eta_history')
    op.drop_table('eta_history')
