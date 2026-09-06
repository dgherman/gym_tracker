"""onboarded_at: first-login onboarding tour marker on users

Revision ID: onboard01
Revises: clientmgmt01
Create Date: 2026-09-05

Hand-written. Adds a nullable ``users.onboarded_at`` (DATETIME) and backfills
every row that exists at upgrade time to a single ``utcnow()`` so established
accounts are treated as already-onboarded; only genuinely new accounts (column
defaults NULL) see the tour. ``onboarded_at`` is otherwise set exclusively by
``POST /api/onboarding/complete``.
"""
from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "onboard01"
down_revision: Union[str, Sequence[str], None] = "clientmgmt01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("onboarded_at", sa.DateTime(), nullable=True))

    # Data step: backfill existing rows to a fixed instant so they never trigger
    # the tour. New rows keep the column default (NULL).
    bind = op.get_bind()
    bind.execute(
        sa.text("UPDATE users SET onboarded_at = :now WHERE onboarded_at IS NULL"),
        {"now": datetime.utcnow()},
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite cannot always DROP COLUMN in place; recreate the table.
        with op.batch_alter_table("users", recreate="always") as batch:
            batch.drop_column("onboarded_at")
    else:
        op.drop_column("users", "onboarded_at")
