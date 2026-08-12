"""Add communication config

Revision ID: 1a2b3c4d5e6f
Revises: 5a33c0bd93b5
Create Date: 2026-07-22

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1a2b3c4d5e6f'
down_revision = '5a33c0bd93b5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # communication_channel_configurations
    op.create_table(
        'communication_channel_configurations',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('channel', sa.String(length=50), nullable=False),
        sa.Column('state', sa.String(length=20), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('updated_by', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('channel', name='uq_communication_channel_configuration_channel'),
        sa.CheckConstraint("state IN ('ENABLED', 'DISABLED', 'EMERGENCY_ONLY')", name='ck_communication_channel_state'),
        sa.CheckConstraint("revision >= 1", name='ck_communication_channel_revision')
    )

    # communication_configuration_audits
    op.create_table(
        'communication_configuration_audits',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('channel', sa.String(length=50), nullable=False),
        sa.Column('previous_state', sa.String(length=20), nullable=True),
        sa.Column('new_state', sa.String(length=20), nullable=False),
        sa.Column('previous_revision', sa.Integer(), nullable=True),
        sa.Column('new_revision', sa.Integer(), nullable=False),
        sa.Column('actor_id', sa.String(length=100), nullable=False),
        sa.Column('actor_tenant_id', sa.String(length=50), nullable=False),
        sa.Column('reason', sa.String(length=500), nullable=False),
        sa.Column('correlation_id', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_communication_configuration_audits_channel'), 'communication_configuration_audits', ['channel'], unique=False)

    # Seed exactly one SMS row
    import uuid
    op.execute(
        f"""
        INSERT INTO communication_channel_configurations (id, channel, state, revision, updated_by, created_at, updated_at)
        VALUES ('{uuid.uuid4()}', 'SMS', 'ENABLED', 1, 'system_migration', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_communication_configuration_audits_channel'), table_name='communication_configuration_audits')
    op.drop_table('communication_configuration_audits')
    op.drop_table('communication_channel_configurations')
