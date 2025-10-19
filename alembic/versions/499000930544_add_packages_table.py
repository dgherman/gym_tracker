"""add_packages_table

Revision ID: 499000930544
Revises: ed03ff5b3f5c
Create Date: 2025-10-19 12:14:06.820503

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '499000930544'
down_revision: Union[str, Sequence[str], None] = 'ed03ff5b3f5c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'packages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('duration_minutes', sa.Integer(), nullable=False),
        sa.Column('num_people', sa.Integer(), nullable=False),
        sa.Column('total_sessions', sa.Integer(), nullable=False),
        sa.Column('price_per_session', sa.Float(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_packages_id'), 'packages', ['id'], unique=False)
    op.create_index(op.f('ix_packages_duration_minutes'), 'packages', ['duration_minutes'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_packages_duration_minutes'), table_name='packages')
    op.drop_index(op.f('ix_packages_id'), table_name='packages')
    op.drop_table('packages')
