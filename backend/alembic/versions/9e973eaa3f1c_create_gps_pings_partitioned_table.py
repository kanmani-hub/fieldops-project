"""create_gps_pings_partitioned_table

Revision ID: 9e973eaa3f1c
Revises: 
Create Date: 2026-06-25 11:00:55.048579

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9e973eaa3f1c'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create tenants table if it does not exist
    op.execute("""
    CREATE TABLE IF NOT EXISTS tenants (
        id VARCHAR(50) PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    """)

    # 2. Insert default tenant
    op.execute("""
    INSERT INTO tenants (id, name)
    VALUES ('d7b38d38-2d88-468f-9a1b-3f4119d8544e', 'Default Tenant')
    ON CONFLICT (id) DO NOTHING;
    """)

    # 3. Drop existing non-partitioned gps_pings table if it exists
    op.execute("DROP TABLE IF EXISTS gps_pings CASCADE;")

    # 4. Create range partitioned gps_pings table
    op.execute("""
    CREATE TABLE gps_pings (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        technician_id VARCHAR(36) NOT NULL REFERENCES technicians(tech_id),
        job_id INT NOT NULL REFERENCES jobs(id),
        latitude DECIMAL(10,8) NOT NULL CHECK (latitude BETWEEN -90 AND 90),
        longitude DECIMAL(11,8) NOT NULL CHECK (longitude BETWEEN -180 AND 180),
        timestamp TIMESTAMPTZ NOT NULL,
        accuracy DECIMAL(6,2),
        altitude DECIMAL(8,2),
        created_at TIMESTAMPTZ DEFAULT NOW(),
        tenant_id VARCHAR(50) NOT NULL REFERENCES tenants(id)
    );
    """)

    # 5. Create partitioned indexes
    # Add composite index on (tenant_id, technician_id, timestamp)
    op.execute("CREATE INDEX idx_gps_pings_tenant_tech_time ON gps_pings (tenant_id, technician_id, timestamp);")
    # Add composite index on (job_id, timestamp)
    op.execute("CREATE INDEX idx_gps_pings_job_time ON gps_pings (job_id, timestamp);")
    # Add index on id field for quick UUID lookup
    op.execute("CREATE INDEX idx_gps_pings_id ON gps_pings (id);")

    


def downgrade() -> None:
    # 1. Drop gps_pings table (automatically drops all its partitions and indexes)
    op.execute("DROP TABLE IF EXISTS gps_pings CASCADE;")
    
    # 2. Drop the partition creator helper function

    # 3. Drop tenants table
    op.execute("DROP TABLE IF EXISTS tenants CASCADE;")

