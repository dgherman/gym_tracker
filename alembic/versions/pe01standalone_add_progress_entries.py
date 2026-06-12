"""add progress_entries table

Revision ID: pe01standalone
Revises: 4093faf32ea1
Create Date: 2026-06-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "pe01standalone"
down_revision: Union[str, Sequence[str], None] = "4093faf32ea1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "progress_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("activity_id", sa.Integer(), sa.ForeignKey("activities.id"), nullable=False),
        sa.Column("entry_date", sa.DateTime(), nullable=False),
        sa.Column("values", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_progress_entries_user_id", "progress_entries", ["user_id"])
    op.create_index("ix_progress_entries_entry_date", "progress_entries", ["entry_date"])


def downgrade() -> None:
    op.drop_index("ix_progress_entries_entry_date", table_name="progress_entries")
    op.drop_index("ix_progress_entries_user_id", table_name="progress_entries")
    op.drop_table("progress_entries")
