"""create_gps_rejected_ping_logs_table

Revision ID: cd7d0313e199
Revises: 96fe28eeba86
Create Date: 2026-06-25 11:25:16.041104

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cd7d0313e199'
down_revision: Union[str, Sequence[str], None] = '96fe28eeba86'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create gps_rejected_ping_logs table safely
    op.execute("""
    CREATE TABLE IF NOT EXISTS gps_rejected_ping_logs (
        id VARCHAR(36) PRIMARY KEY,
        technician_id VARCHAR(50) NULL,
        job_id VARCHAR(36) NULL,
        reason VARCHAR(200) NOT NULL,
        timestamp TIMESTAMPTZ DEFAULT NOW(),
        tenant_id VARCHAR(50) NULL
    );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_gps_rejected_ping_logs_technician_id ON gps_rejected_ping_logs (technician_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_gps_rejected_ping_logs_job_id ON gps_rejected_ping_logs (job_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_gps_rejected_ping_logs_timestamp ON gps_rejected_ping_logs (timestamp);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_gps_rejected_ping_logs_tenant_id ON gps_rejected_ping_logs (tenant_id);")


def downgrade() -> None:
    op.drop_index(op.f('ix_gps_rejected_ping_logs_tenant_id'), table_name='gps_rejected_ping_logs')
    op.drop_index(op.f('ix_gps_rejected_ping_logs_timestamp'), table_name='gps_rejected_ping_logs')
    op.drop_index(op.f('ix_gps_rejected_ping_logs_job_id'), table_name='gps_rejected_ping_logs')
    op.drop_index(op.f('ix_gps_rejected_ping_logs_technician_id'), table_name='gps_rejected_ping_logs')
    op.drop_table('gps_rejected_ping_logs')
