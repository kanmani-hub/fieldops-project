"""Add multi-tenant RBAC models (users, refresh_tokens, organizations, enterprise_audit_logs)

Revision ID: ff1122334455
Revises: 1ad86b0a4f3f
Create Date: 2026-07-25 15:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'ff1122334455'
down_revision: Union[str, Sequence[str], None] = '1ad86b0a4f3f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. organizations table
    op.create_table(
        'organizations',
        sa.Column('id', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('slug', sa.String(length=100), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='ACTIVE'),
        sa.Column('subscription_plan', sa.String(length=50), nullable=False, server_default='FREE'),
        sa.Column('max_users', sa.Integer(), nullable=False, server_default='10'),
        sa.Column('max_technicians', sa.Integer(), nullable=False, server_default='50'),
        sa.Column('max_jobs_per_month', sa.Integer(), nullable=False, server_default='500'),
        sa.Column('settings', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('contact_email', sa.String(length=255), nullable=True),
        sa.Column('contact_phone', sa.String(length=20), nullable=True),
        sa.Column('address', sa.String(length=500), nullable=True),
        sa.Column('logo_url', sa.String(length=500), nullable=True),
        sa.Column('primary_color', sa.String(length=7), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deleted_by', sa.String(length=36), nullable=True),
        sa.Column('suspended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('suspended_by', sa.String(length=36), nullable=True),
        sa.Column('suspension_reason', sa.String(length=500), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug', name='uq_organizations_slug'),
        sa.CheckConstraint("status IN ('ACTIVE', 'SUSPENDED', 'DELETED')", name='ck_organizations_status'),
        sa.CheckConstraint("subscription_plan IN ('FREE', 'STARTER', 'PROFESSIONAL', 'ENTERPRISE')", name='ck_organizations_plan')
    )
    op.create_index('idx_organizations_status', 'organizations', ['status'])
    op.create_index('idx_organizations_active', 'organizations', ['status', 'deleted_at'])

    # 2. users table
    op.create_table(
        'users',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('first_name', sa.String(length=100), nullable=False),
        sa.Column('last_name', sa.String(length=100), nullable=False),
        sa.Column('role', sa.String(length=30), nullable=False),
        sa.Column('tenant_id', sa.String(length=50), nullable=False),
        sa.Column('phone_number', sa.String(length=20), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('is_email_verified', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('failed_login_attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_login', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deleted_by', sa.String(length=36), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email', 'tenant_id', name='uq_users_email_tenant')
    )
    op.create_index('idx_users_email', 'users', ['email'])
    op.create_index('idx_users_role', 'users', ['role'])
    op.create_index('idx_users_tenant_id', 'users', ['tenant_id'])
    op.create_index('idx_users_tenant_role', 'users', ['tenant_id', 'role'])
    op.create_index('idx_users_active', 'users', ['is_active', 'deleted_at'])

    # 3. refresh_tokens table
    op.create_table(
        'refresh_tokens',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('token_hash', sa.String(length=255), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('device_info', sa.String(length=255), nullable=True),
        sa.Column('ip_address', sa.String(length=50), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash', name='uq_refresh_tokens_hash')
    )
    op.create_index('idx_refresh_tokens_user_id', 'refresh_tokens', ['user_id'])
    op.create_index('idx_refresh_tokens_user_active', 'refresh_tokens', ['user_id', 'revoked_at'])
    op.create_index('idx_refresh_tokens_expires', 'refresh_tokens', ['expires_at'])

    # 4. enterprise_audit_logs table
    op.create_table(
        'enterprise_audit_logs',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=True),
        sa.Column('user_email', sa.String(length=255), nullable=True),
        sa.Column('role', sa.String(length=30), nullable=True),
        sa.Column('tenant_id', sa.String(length=50), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('ip_address', sa.String(length=50), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('action', sa.String(length=100), nullable=False),
        sa.Column('entity_type', sa.String(length=50), nullable=True),
        sa.Column('entity_id', sa.String(length=100), nullable=True),
        sa.Column('old_value', sa.JSON(), nullable=True),
        sa.Column('new_value', sa.JSON(), nullable=True),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.Column('correlation_id', sa.String(length=100), nullable=True),
        sa.Column('severity', sa.String(length=20), nullable=False, server_default='INFO'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_enterprise_audit_tenant_time', 'enterprise_audit_logs', ['tenant_id', 'timestamp'])
    op.create_index('idx_enterprise_audit_action_time', 'enterprise_audit_logs', ['action', 'timestamp'])
    op.create_index('idx_enterprise_audit_entity', 'enterprise_audit_logs', ['entity_type', 'entity_id'])
    op.create_index('idx_enterprise_audit_user_time', 'enterprise_audit_logs', ['user_id', 'timestamp'])


def downgrade() -> None:
    op.drop_table('enterprise_audit_logs')
    op.drop_table('refresh_tokens')
    op.drop_table('users')
    op.drop_table('organizations')
