"""add_partner_support

Revision ID: a1b2c3d4e5f6
Revises: 499000930544
Create Date: 2026-02-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '499000930544'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add partner columns to purchases and sessions."""
    # Purchases: num_people, partner_email, partner_user_id
    op.add_column('purchases', sa.Column('num_people', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('purchases', sa.Column('partner_email', sa.String(length=255), nullable=True))
    op.add_column('purchases', sa.Column('partner_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True))

    # Sessions: partner_user_id
    op.add_column('sessions', sa.Column('partner_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True))


def downgrade() -> None:
    """Remove partner columns."""
    op.drop_column('sessions', 'partner_user_id')
    op.drop_column('purchases', 'partner_user_id')
    op.drop_column('purchases', 'partner_email')
    op.drop_column('purchases', 'num_people')
