"""app_settings

Revision ID: 8a6f4103eec8
Revises: 04276eeb99c7
Create Date: 2026-03-29 11:23:32.890116

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = '8a6f4103eec8'
down_revision: Union[str, Sequence[str], None] = '04276eeb99c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "app_settings" not in existing_tables:
        op.create_table(
            "app_settings",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("key", sa.String(100), nullable=False),
            sa.Column("value", sa.String(255), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("key"),
        )
        op.create_index("ix_app_settings_id", "app_settings", ["id"])
        op.create_index("ix_app_settings_key", "app_settings", ["key"])
        # Seed the auto_complete_sessions setting
        op.execute("INSERT INTO app_settings (`key`, `value`, created_at) VALUES ('auto_complete_sessions', 'false', NOW())")


def downgrade() -> None:
    op.drop_index("ix_app_settings_key", table_name="app_settings")
    op.drop_index("ix_app_settings_id", table_name="app_settings")
    op.drop_table("app_settings")
