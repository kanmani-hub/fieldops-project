"""Create job_closures table

Revision ID: 7a8b9c0d1e2f
Revises: ff1122334455
Create Date: 2026-07-25 23:55:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '7a8b9c0d1e2f'
down_revision: Union[str, Sequence[str], None] = 'ff1122334455'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'job_closures',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=False),
        sa.Column('technician_id', sa.String(length=100), nullable=False),
        sa.Column('work_summary', sa.Text(), nullable=False),
        sa.Column('before_images', sa.JSON(), nullable=True),
        sa.Column('after_images', sa.JSON(), nullable=False),
        sa.Column('labour_cost', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('material_cost', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('subtotal', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_job_closures_id'), 'job_closures', ['id'], unique=False)
    op.create_index(op.f('ix_job_closures_job_id'), 'job_closures', ['job_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_job_closures_job_id'), table_name='job_closures')
    op.drop_index(op.f('ix_job_closures_id'), table_name='job_closures')
    op.drop_table('job_closures')
