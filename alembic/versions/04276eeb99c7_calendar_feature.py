"""calendar_feature

Revision ID: 04276eeb99c7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-27 12:28:23.139571

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = '04276eeb99c7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    existing_trainers_cols = [c["name"] for c in inspector.get_columns("trainers")]
    existing_sessions_cols = [c["name"] for c in inspector.get_columns("sessions")]

    # 1. Add email and user_id to trainers
    if "email" not in existing_trainers_cols:
        op.add_column("trainers", sa.Column("email", sa.String(255), nullable=True))
        op.create_unique_constraint("uq_trainers_email", "trainers", ["email"])
    if "user_id" not in existing_trainers_cols:
        op.add_column("trainers", sa.Column("user_id", sa.Integer(), nullable=True))
        op.create_foreign_key(None, "trainers", "users", ["user_id"], ["id"])

    # 2. Create recurrence_groups table
    if "recurrence_groups" not in existing_tables:
        op.create_table(
            "recurrence_groups",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("frequency", sa.String(20), nullable=False),
            sa.Column("day_of_week", sa.Integer(), nullable=False),
            sa.Column("time_of_day", sa.Time(), nullable=False),
            sa.Column("duration_minutes", sa.Integer(), nullable=False),
            sa.Column("trainer_id", sa.Integer(), nullable=False),
            sa.Column("client_user_id", sa.Integer(), nullable=False),
            sa.Column("purchase_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("horizon_through", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["client_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["purchase_id"], ["purchases.id"]),
            sa.ForeignKeyConstraint(["trainer_id"], ["trainers.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_recurrence_groups_id", "recurrence_groups", ["id"])

    # 3. Create user_invites table
    if "user_invites" not in existing_tables:
        op.create_table(
            "user_invites",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("email", sa.String(255), nullable=False),
            sa.Column("role", sa.String(50), nullable=False),
            sa.Column("trainer_id", sa.Integer(), nullable=True),
            sa.Column("invited_by_user_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("accepted_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["invited_by_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["trainer_id"], ["trainers.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("email"),
        )
        op.create_index("ix_user_invites_id", "user_invites", ["id"])
        op.create_index("ix_user_invites_email", "user_invites", ["email"])

    # 4. Add new columns to sessions
    if "status" not in existing_sessions_cols:
        op.add_column("sessions", sa.Column("status", sa.String(20), nullable=True))
    if "client_user_id" not in existing_sessions_cols:
        op.add_column("sessions", sa.Column("client_user_id", sa.Integer(), nullable=True))
        op.create_foreign_key(None, "sessions", "users", ["client_user_id"], ["id"])
    if "scheduled_by_user_id" not in existing_sessions_cols:
        op.add_column("sessions", sa.Column("scheduled_by_user_id", sa.Integer(), nullable=True))
        op.create_foreign_key(None, "sessions", "users", ["scheduled_by_user_id"], ["id"])
    if "recurrence_group_id" not in existing_sessions_cols:
        op.add_column("sessions", sa.Column("recurrence_group_id", sa.Integer(), nullable=True))
        op.create_foreign_key(None, "sessions", "recurrence_groups", ["recurrence_group_id"], ["id"])
    if "notes" not in existing_sessions_cols:
        op.add_column("sessions", sa.Column("notes", sa.Text(), nullable=True))

    # Make purchase_id nullable (it may already be; alter_column is idempotent for nullable)
    op.alter_column("sessions", "purchase_id", existing_type=sa.Integer(), nullable=True)

    # Backfill status = "completed" for all existing sessions
    op.execute("UPDATE sessions SET status = 'completed' WHERE status IS NULL")
    op.alter_column("sessions", "status", existing_type=sa.String(20), nullable=False)

    # Backfill client_user_id from purchase owner where available
    op.execute(
        "UPDATE sessions s "
        "JOIN purchases p ON s.purchase_id = p.id "
        "SET s.client_user_id = p.logged_by_user_id "
        "WHERE s.client_user_id IS NULL AND p.logged_by_user_id IS NOT NULL"
    )

    # Backfill user_invites from all existing users (so no one is locked out)
    # Use u.email alias to avoid ambiguity in ON DUPLICATE KEY UPDATE
    op.execute(
        "INSERT INTO user_invites (email, role, created_at, accepted_at) "
        "SELECT u.email, u.role, u.created_at, u.created_at "
        "FROM users u "
        "WHERE u.email IS NOT NULL AND u.email != '' "
        "ON DUPLICATE KEY UPDATE accepted_at = VALUES(accepted_at)"
    )


def downgrade() -> None:
    op.drop_index("ix_user_invites_email", table_name="user_invites")
    op.drop_index("ix_user_invites_id", table_name="user_invites")
    op.drop_table("user_invites")
    op.drop_column("sessions", "notes")
    op.drop_column("sessions", "recurrence_group_id")
    op.drop_column("sessions", "scheduled_by_user_id")
    op.drop_column("sessions", "client_user_id")
    op.drop_column("sessions", "status")
    op.alter_column("sessions", "purchase_id", existing_type=sa.Integer(), nullable=False)
    op.drop_index("ix_recurrence_groups_id", table_name="recurrence_groups")
    op.drop_table("recurrence_groups")
    op.drop_column("trainers", "user_id")
    op.drop_constraint("uq_trainers_email", "trainers", type_="unique")
    op.drop_column("trainers", "email")
