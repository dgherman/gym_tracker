"""last_seen_at: per-user activity timestamp on users

Revision ID: lastseen01
Revises: onboard01
Create Date: 2026-09-07

Hand-written (no autogenerate). Adds a nullable ``users.last_seen_at``
(DATETIME). There is no backfill: NULL means "not seen since this column
shipped". The value is maintained by ``LoginRequiredMiddleware`` on
authenticated requests (see ``main._record_activity``), throttled to at most
one write per user per 5 minutes via a single atomic conditional UPDATE.

``onboard01`` is the current Alembic head; this revision chains directly off
it. (The additive DDL is dialect-agnostic, so ``upgrade`` needs no SQLite vs
MySQL branch -- same as ``onboard01``; only ``downgrade`` uses batch mode so
SQLite can drop the column.)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "lastseen01"
down_revision: Union[str, Sequence[str], None] = "onboard01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_seen_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite cannot always DROP COLUMN in place; recreate the table.
        with op.batch_alter_table("users", recreate="always") as batch:
            batch.drop_column("last_seen_at")
    else:
        op.drop_column("users", "last_seen_at")
