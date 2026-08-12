"""Add technician profiles, customer profiles, service requests, and job rejection fields

Revision ID: ff9988776655
Revises: 7a8b9c0d1e2f
Create Date: 2026-07-26 01:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'ff9988776655'
down_revision: Union[str, Sequence[str], None] = '7a8b9c0d1e2f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. technician_profiles
    op.create_table(
        'technician_profiles',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=50), nullable=False),
        sa.Column('full_name', sa.String(length=200), nullable=False),
        sa.Column('profile_photo', sa.Text(), nullable=True),
        sa.Column('mobile_number', sa.String(length=20), nullable=False),
        sa.Column('date_of_birth', sa.Date(), nullable=True),
        sa.Column('gender', sa.String(length=20), nullable=True),
        sa.Column('address', sa.Text(), nullable=True),
        sa.Column('city', sa.String(length=100), nullable=True),
        sa.Column('state', sa.String(length=100), nullable=True),
        sa.Column('pincode', sa.String(length=10), nullable=True),
        sa.Column('emergency_contact', sa.String(length=100), nullable=True),
        sa.Column('skills', sa.JSON(), nullable=True),
        sa.Column('experience', sa.String(length=200), nullable=True),
        sa.Column('certifications', sa.JSON(), nullable=True),
        sa.Column('profile_completed', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id')
    )
    op.create_index('idx_tech_profile_tenant', 'technician_profiles', ['tenant_id'])
    op.create_index('idx_tech_profile_user', 'technician_profiles', ['user_id'])

    # 2. customer_profiles_extended
    op.create_table(
        'customer_profiles_extended',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=50), nullable=False),
        sa.Column('full_name', sa.String(length=200), nullable=False),
        sa.Column('mobile_number', sa.String(length=20), nullable=False),
        sa.Column('address', sa.Text(), nullable=True),
        sa.Column('city', sa.String(length=100), nullable=True),
        sa.Column('state', sa.String(length=100), nullable=True),
        sa.Column('pincode', sa.String(length=10), nullable=True),
        sa.Column('company_name', sa.String(length=200), nullable=True),
        sa.Column('profile_completed', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id')
    )
    op.create_index('idx_cust_profile_tenant', 'customer_profiles_extended', ['tenant_id'])
    op.create_index('idx_cust_profile_user', 'customer_profiles_extended', ['user_id'])

    # 3. service_requests
    op.create_table(
        'service_requests',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('request_number', sa.String(length=50), nullable=False),
        sa.Column('customer_user_id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=50), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('service_type', sa.String(length=100), nullable=True),
        sa.Column('priority', sa.String(length=20), nullable=False, server_default='MEDIUM'),
        sa.Column('preferred_visit_date', sa.Date(), nullable=True),
        sa.Column('images', sa.JSON(), nullable=True),
        sa.Column('location', sa.String(length=255), nullable=True),
        sa.Column('contact_number', sa.String(length=20), nullable=True),
        sa.Column('status', sa.String(length=30), nullable=False, server_default='PENDING'),
        sa.Column('linked_job_id', sa.Integer(), nullable=True),
        sa.Column('cancellation_reason', sa.Text(), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['customer_user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['linked_job_id'], ['jobs.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('request_number')
    )
    op.create_index('idx_sr_customer', 'service_requests', ['customer_user_id'])
    op.create_index('idx_sr_tenant_status', 'service_requests', ['tenant_id', 'status'])
    op.create_index('idx_sr_linked_job', 'service_requests', ['linked_job_id'])

    # 4. Add rejection columns to jobs table if they don't exist
    with op.batch_alter_table('jobs') as batch_op:
        batch_op.add_column(sa.Column('rejection_reason', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('rejected_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('rejected_by_tech_id', sa.String(length=50), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('jobs') as batch_op:
        batch_op.drop_column('rejected_by_tech_id')
        batch_op.drop_column('rejected_at')
        batch_op.drop_column('rejection_reason')

    op.drop_table('service_requests')
    op.drop_table('customer_profiles_extended')
    op.drop_table('technician_profiles')
