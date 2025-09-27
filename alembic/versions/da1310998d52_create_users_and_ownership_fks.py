"""create users and ownership fks

Revision ID: da1310998d52
Revises: 
Create Date: 2025-09-27 14:44:00.713676

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "da1310998d52"
down_revision = None
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    return bool(
        bind.execute(
            text(
                """
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_schema = DATABASE() AND table_name = :t
                """
            ),
            {"t": table_name},
        ).scalar()
    )


def _column_missing(bind, table: str, column: str) -> bool:
    return not bool(
        bind.execute(
            text(
                """
                SELECT COUNT(*) FROM information_schema.columns
                WHERE table_schema = DATABASE()
                  AND table_name = :t
                  AND column_name = :c
                """
            ),
            {"t": table, "c": column},
        ).scalar()
    )


def _fk_missing(bind, table: str, fk_name: str) -> bool:
    return not bool(
        bind.execute(
            text(
                """
                SELECT COUNT(*) FROM information_schema.table_constraints
                WHERE table_schema = DATABASE()
                  AND table_name = :t
                  AND constraint_type = 'FOREIGN KEY'
                  AND constraint_name = :n
                """
            ),
            {"t": table, "n": fk_name},
        ).scalar()
    )


def _index_missing(bind, table: str, index_name: str) -> bool:
    return not bool(
        bind.execute(
            text(
                """
                SELECT COUNT(*) FROM information_schema.statistics
                WHERE table_schema = DATABASE()
                  AND table_name = :t
                  AND index_name = :i
                """
            ),
            {"t": table, "i": index_name},
        ).scalar()
    )


def upgrade():
    bind = op.get_bind()

    # 1) Ensure users table exists
    if not _table_exists(bind, "users"):
        op.create_table(
            "users",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("email", sa.String(length=255), nullable=False, unique=True),
            sa.Column("full_name", sa.String(length=255), nullable=True),
            sa.Column("picture_url", sa.String(length=512), nullable=True),
            sa.Column("role", sa.String(length=32), nullable=False, server_default="user"),
            sa.Column("created_at", sa.DateTime, server_default=sa.text("CURRENT_TIMESTAMP")),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )

    # 2) Add ownership columns if missing
    if _column_missing(bind, "purchases", "logged_by_user_id"):
        op.add_column("purchases", sa.Column("logged_by_user_id", sa.Integer(), nullable=True))
    if _column_missing(bind, "sessions", "created_by_user_id"):
        op.add_column("sessions", sa.Column("created_by_user_id", sa.Integer(), nullable=True))

    # 3) Add indexes if missing (helps performance and MySQL can’t add FK without index)
    if _index_missing(bind, "purchases", "ix_purchases_logged_by_user_id"):
        op.create_index("ix_purchases_logged_by_user_id", "purchases", ["logged_by_user_id"])
    if _index_missing(bind, "sessions", "ix_sessions_created_by_user_id"):
        op.create_index("ix_sessions_created_by_user_id", "sessions", ["created_by_user_id"])

    # 4) Add FKs if missing (name them deterministically)
    if _fk_missing(bind, "purchases", "fk_purchases_logged_by_user_id"):
        op.create_foreign_key(
            "fk_purchases_logged_by_user_id",
            source_table="purchases",
            referent_table="users",
            local_cols=["logged_by_user_id"],
            remote_cols=["id"],
            ondelete="SET NULL",
        )
    if _fk_missing(bind, "sessions", "fk_sessions_created_by_user_id"):
        op.create_foreign_key(
            "fk_sessions_created_by_user_id",
            source_table="sessions",
            referent_table="users",
            local_cols=["created_by_user_id"],
            remote_cols=["id"],
            ondelete="SET NULL",
        )


def downgrade():
    bind = op.get_bind()

    # Drop FKs if present
    if not _fk_missing(bind, "sessions", "fk_sessions_created_by_user_id"):
        op.drop_constraint("fk_sessions_created_by_user_id", "sessions", type_="foreignkey")
    if not _fk_missing(bind, "purchases", "fk_purchases_logged_by_user_id"):
        op.drop_constraint("fk_purchases_logged_by_user_id", "purchases", type_="foreignkey")

    # Drop indexes if present
    if not _index_missing(bind, "sessions", "ix_sessions_created_by_user_id"):
        op.drop_index("ix_sessions_created_by_user_id", table_name="sessions")
    if not _index_missing(bind, "purchases", "ix_purchases_logged_by_user_id"):
        op.drop_index("ix_purchases_logged_by_user_id", table_name="purchases")

    # Drop columns if present
    if not _column_missing(bind, "sessions", "created_by_user_id"):
        op.drop_column("sessions", "created_by_user_id")
    if not _column_missing(bind, "purchases", "logged_by_user_id"):
        op.drop_column("purchases", "logged_by_user_id")

    # (Optionally) drop users table (only if present)
    if _table_exists(bind, "users"):
        op.drop_table("users")
