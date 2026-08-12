"""create_retention_and_audit_tables

Revision ID: 96fe28eeba86
Revises: 9e973eaa3f1c
Create Date: 2026-06-25 11:14:17.731086

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '96fe28eeba86'
down_revision: Union[str, Sequence[str], None] = '9e973eaa3f1c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create tenant_gps_configurations table safely
    op.execute("""
    CREATE TABLE IF NOT EXISTS tenant_gps_configurations (
        tenant_id VARCHAR(50) PRIMARY KEY,
        retention_days INTEGER NOT NULL DEFAULT 30,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW(),
        CONSTRAINT valid_retention_days CHECK (retention_days BETWEEN 1 AND 90)
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_tenant_gps_configurations_tenant_id ON tenant_gps_configurations (tenant_id);")

    # 2. Create gps_purge_audit_logs table safely
    op.execute("""
    CREATE TABLE IF NOT EXISTS gps_purge_audit_logs (
        id VARCHAR(36) PRIMARY KEY,
        tenant_id VARCHAR(50) NOT NULL,
        job_id VARCHAR(36) NULL,
        purge_type VARCHAR(20) NOT NULL,
        deleted_count INTEGER NOT NULL,
        correlation_id VARCHAR(36) NULL,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_gps_purge_audit_logs_tenant_id ON gps_purge_audit_logs (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_gps_purge_audit_logs_job_id ON gps_purge_audit_logs (job_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_gps_purge_audit_logs_created_at ON gps_purge_audit_logs (created_at);")


def downgrade() -> None:
    op.drop_index(op.f('ix_gps_purge_audit_logs_created_at'), table_name='gps_purge_audit_logs')
    op.drop_index(op.f('ix_gps_purge_audit_logs_job_id'), table_name='gps_purge_audit_logs')
    op.drop_index(op.f('ix_gps_purge_audit_logs_tenant_id'), table_name='gps_purge_audit_logs')
    op.drop_table('gps_purge_audit_logs')

    op.drop_index(op.f('ix_tenant_gps_configurations_tenant_id'), table_name='tenant_gps_configurations')
    op.drop_table('tenant_gps_configurations')
