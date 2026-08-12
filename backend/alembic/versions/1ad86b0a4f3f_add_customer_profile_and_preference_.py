"""Add customer profile and preference audits

Revision ID: 1ad86b0a4f3f
Revises: b15cb1f9d24e
Create Date: 2026-07-23 00:10:22.665349

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '1ad86b0a4f3f'
down_revision: Union[str, Sequence[str], None] = 'b15cb1f9d24e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('customer_profiles',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=50), nullable=False),
    sa.Column('customer_id', sa.String(length=50), nullable=False),
    sa.Column('preferred_locale', sa.String(length=10), nullable=False, server_default='en'),
    sa.Column('sms_enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
    sa.Column('email_enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
    sa.Column('push_enabled', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    sa.Column('portal_enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
    sa.Column('revision', sa.Integer(), nullable=False, server_default='1'),
    sa.Column('updated_by', sa.String(length=100), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint('revision >= 1', name='chk_customer_profiles_revision_positive'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'customer_id', name='uq_customer_profiles_tenant_customer')
    )
    op.create_index('ix_customer_profiles_tenant_id', 'customer_profiles', ['tenant_id'], unique=False)
    
    op.create_table('customer_preference_audits',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('customer_profile_id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=50), nullable=False),
    sa.Column('previous_revision', sa.Integer(), nullable=False),
    sa.Column('new_revision', sa.Integer(), nullable=False),
    sa.Column('changed_fields', sa.JSON(), nullable=False),
    sa.Column('actor_id', sa.String(length=100), nullable=False),
    sa.Column('actor_source', sa.String(length=50), nullable=False),
    sa.Column('correlation_id', sa.String(length=100), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("actor_source IN ('CUSTOMER', 'ADMIN', 'SYSTEM')", name='chk_audit_actor_source'),
    sa.CheckConstraint('previous_revision >= 0', name='chk_audit_prev_revision'),
    sa.CheckConstraint('new_revision >= 1', name='chk_audit_new_revision'),
    sa.CheckConstraint('new_revision > previous_revision', name='chk_audit_revision_progression'),
    sa.ForeignKeyConstraint(['customer_profile_id'], ['customer_profiles.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_customer_preference_audits_tenant_id', 'customer_preference_audits', ['tenant_id'], unique=False)
    op.create_index('ix_customer_preference_audits_profile_id', 'customer_preference_audits', ['customer_profile_id'], unique=False)
    op.create_index('ix_customer_preference_audits_tenant_profile', 'customer_preference_audits', ['tenant_id', 'customer_profile_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_customer_preference_audits_tenant_profile', table_name='customer_preference_audits', if_exists=True)
    op.drop_index('ix_customer_preference_audits_profile_id', table_name='customer_preference_audits', if_exists=True)
    op.drop_index('ix_customer_preference_audits_tenant_id', table_name='customer_preference_audits', if_exists=True)
    op.drop_table('customer_preference_audits')
    op.drop_index('ix_customer_profiles_tenant_id', table_name='customer_profiles', if_exists=True)
    op.drop_table('customer_profiles')
