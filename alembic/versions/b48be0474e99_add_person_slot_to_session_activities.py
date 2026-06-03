"""add person_slot to session_activities

Revision ID: b48be0474e99
Revises: ab12activity01
Create Date: 2026-06-03 11:22:04.192442

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b48be0474e99'
down_revision: Union[str, Sequence[str], None] = 'ab12activity01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("session_activities", sa.Column("person_slot", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("session_activities", "person_slot")
