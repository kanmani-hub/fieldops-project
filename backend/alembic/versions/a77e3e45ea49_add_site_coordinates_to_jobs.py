"""add site coordinates to jobs

Revision ID: a77e3e45ea49
Revises: cd7d0313e199
Create Date: 2026-06-25 12:20:46.626455

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a77e3e45ea49'
down_revision: Union[str, Sequence[str], None] = 'cd7d0313e199'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns('jobs')]
    if 'site_latitude' not in columns:
        op.add_column('jobs', sa.Column('site_latitude', sa.Float(), nullable=True))
    if 'site_longitude' not in columns:
        op.add_column('jobs', sa.Column('site_longitude', sa.Float(), nullable=True))
    if 'site_address' not in columns:
        op.add_column('jobs', sa.Column('site_address', sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns('jobs')]
    if 'site_address' in columns:
        op.drop_column('jobs', 'site_address')
    if 'site_longitude' in columns:
        op.drop_column('jobs', 'site_longitude')
    if 'site_latitude' in columns:
        op.drop_column('jobs', 'site_latitude')
