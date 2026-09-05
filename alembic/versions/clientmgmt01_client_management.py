"""client management: invite/status columns on users + ALLOWED_EMAILS cutover

Revision ID: clientmgmt01
Revises: pe01standalone
Create Date: 2026-09-05

Hand-written (no autogenerate) so the data cutover is explicit and the schema
step is safe on both production MySQL and the SQLite test databases.

Schema:
  * add users.status (VARCHAR(20) NOT NULL DEFAULT 'active')
  * add users.invite_token_hash (VARCHAR(64) NULL, UNIQUE)
  * add users.invited_by_id (INT NULL, FK users.id ON DELETE SET NULL)
  * add users.invited_at / users.confirmed_at (DATETIME NULL)
  * users.google_sub -> nullable (keep the unique index)
  * users.email -> case-insensitive UNIQUE for non-NULL values
    (``uq_users_email_ci``). Aborts with RuntimeError if the table already
    holds case-insensitive duplicate emails -- a human must resolve those
    first; this migration never deletes or merges rows.

Data cutover (retire ALLOWED_EMAILS):
  * every existing users row -> status 'active', confirmed_at = created_at (or now)
  * each ALLOWED_EMAILS entry with no matching users.email (case-insensitive) ->
    new active client row, google_sub NULL, so it can sign in with Google
    immediately (first login backfills google_sub) exactly as before.
"""
import os
from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "clientmgmt01"
down_revision: Union[str, Sequence[str], None] = "pe01standalone"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_COLUMNS = {
    "status": sa.Column("status", sa.String(20), nullable=False, server_default="active"),
    "invite_token_hash": sa.Column("invite_token_hash", sa.String(64), nullable=True),
    "invited_by_id": sa.Column("invited_by_id", sa.Integer(), nullable=True),
    "invited_at": sa.Column("invited_at", sa.DateTime(), nullable=True),
    "confirmed_at": sa.Column("confirmed_at", sa.DateTime(), nullable=True),
}


def _existing_column_names(inspector) -> set:
    return {c["name"] for c in inspector.get_columns("users")}


def _has_invite_hash_unique(inspector) -> bool:
    if any(c["name"] == "uq_users_invite_token_hash"
           for c in inspector.get_unique_constraints("users")):
        return True
    return any(
        ix.get("unique") and ix.get("column_names") == ["invite_token_hash"]
        for ix in inspector.get_indexes("users")
    )


def _has_email_ci_unique(inspector) -> bool:
    names = {c["name"] for c in inspector.get_unique_constraints("users")}
    names |= {ix["name"] for ix in inspector.get_indexes("users")}
    return "uq_users_email_ci" in names


def _assert_no_ci_duplicate_emails(bind) -> None:
    """Fail the migration if two rows already share an email case-insensitively.

    We must not add the CI-uniqueness invariant on top of dirty data, and we
    must not silently pick a winner. List the offenders and stop BEFORE any DDL.

    Only NULL emails are allowed to repeat (a unique index permits multiple
    NULLs). Empty strings are NOT NULL, so ``''`` counts as a real value and two
    ``''`` rows are a duplicate -- otherwise they would sail past this guard and
    fail with IntegrityError after the additive DDL had partially applied.
    """
    dups = bind.execute(sa.text(
        "SELECT lower(email) AS e FROM users "
        "WHERE email IS NOT NULL "
        "GROUP BY lower(email) HAVING COUNT(*) > 1"
    )).fetchall()
    if dups:
        offenders = ", ".join(sorted(repr(r[0]) for r in dups))
        raise RuntimeError(
            "clientmgmt01 aborted: users.email has case-insensitive duplicate(s): "
            f"{offenders}. Resolve these rows by hand (this migration will not "
            "delete or merge users) and re-run."
        )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    is_sqlite = bind.dialect.name == "sqlite"

    # Guard BEFORE adding any uniqueness invariant.
    _assert_no_ci_duplicate_emails(bind)

    existing = _existing_column_names(inspector)
    for name, column in _NEW_COLUMNS.items():
        if name not in existing:
            op.add_column("users", column.copy())

    # google_sub -> nullable, plus the unique constraint + self-FK.
    # SQLite cannot ALTER COLUMN in place; recreate the table via batch mode.
    if is_sqlite:
        with op.batch_alter_table("users", recreate="always") as batch:
            batch.alter_column("google_sub", existing_type=sa.String(255), nullable=True)
            if not _has_invite_hash_unique(inspector):
                batch.create_unique_constraint("uq_users_invite_token_hash", ["invite_token_hash"])
            existing_fks = {fk["name"] for fk in inspector.get_foreign_keys("users")}
            if "fk_users_invited_by" not in existing_fks:
                batch.create_foreign_key(
                    "fk_users_invited_by", "users",
                    ["invited_by_id"], ["id"], ondelete="SET NULL",
                )
        # Functional, partial unique index: case-insensitive uniqueness on
        # non-NULL emails. Created after the batch recreate so it survives.
        if not _has_email_ci_unique(sa.inspect(bind)):
            op.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_ci "
                "ON users (lower(email)) WHERE email IS NOT NULL"
            )
    else:
        op.alter_column("users", "google_sub", existing_type=sa.String(length=255), nullable=True)
        if not _has_invite_hash_unique(inspector):
            op.create_unique_constraint("uq_users_invite_token_hash", "users", ["invite_token_hash"])
        existing_fks = {fk["name"] for fk in inspector.get_foreign_keys("users")}
        if "fk_users_invited_by" not in existing_fks:
            op.create_foreign_key(
                "fk_users_invited_by", "users", "users",
                ["invited_by_id"], ["id"], ondelete="SET NULL",
            )
        # MySQL's default collation is case-insensitive and a unique index
        # permits multiple NULLs, so a plain unique constraint on `email` gives
        # us case-insensitive uniqueness for non-NULL values.
        if not _has_email_ci_unique(inspector):
            op.create_unique_constraint("uq_users_email_ci", "users", ["email"])

    _data_cutover(bind)


def _data_cutover(bind) -> None:
    now = datetime.utcnow()

    # Spec 5.2: every row that already exists at cutover is an established,
    # already-logged-in user -> mark active unconditionally. New rows seeded
    # from ALLOWED_EMAILS below are inserted with status 'active' directly.
    bind.execute(sa.text("UPDATE users SET status = 'active'"))
    bind.execute(
        sa.text("UPDATE users SET confirmed_at = COALESCE(created_at, :now) WHERE confirmed_at IS NULL"),
        {"now": now},
    )

    raw = os.getenv("ALLOWED_EMAILS", "") or ""
    seen = set()
    for entry in raw.split(","):
        email = entry.strip().lower()
        if not email or email in seen:
            continue
        seen.add(email)
        already = bind.execute(
            sa.text("SELECT 1 FROM users WHERE lower(email) = :e LIMIT 1"), {"e": email}
        ).first()
        if already:
            continue
        bind.execute(
            sa.text(
                "INSERT INTO users "
                "(google_sub, email, email_verified, full_name, avatar_url, role, is_active, "
                " status, invite_token_hash, invited_by_id, invited_at, confirmed_at, "
                " created_at, last_login_at) "
                "VALUES (NULL, :email, :false, NULL, NULL, 'client', :true, "
                " 'active', NULL, NULL, :now, :now, :now, :now)"
            ),
            {"email": email, "now": now, "false": False, "true": True},
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    # Rows seeded from ALLOWED_EMAILS (google_sub NULL) are intentionally left in
    # place -- this downgrade never deletes users. google_sub is only restored to
    # NOT NULL when no NULL values remain; otherwise it stays nullable (a rollback
    # after the cutover cannot un-invite those addresses without data loss).
    has_null_sub = bind.execute(
        sa.text("SELECT 1 FROM users WHERE google_sub IS NULL LIMIT 1")
    ).first() is not None

    if is_sqlite:
        op.execute("DROP INDEX IF EXISTS uq_users_email_ci")
        with op.batch_alter_table("users", recreate="always") as batch:
            batch.drop_constraint("fk_users_invited_by", type_="foreignkey")
            batch.drop_constraint("uq_users_invite_token_hash", type_="unique")
            if not has_null_sub:
                batch.alter_column("google_sub", existing_type=sa.String(255), nullable=False)
            for name in _NEW_COLUMNS:
                batch.drop_column(name)
    else:
        op.drop_constraint("uq_users_email_ci", "users", type_="unique")
        op.drop_constraint("fk_users_invited_by", "users", type_="foreignkey")
        op.drop_constraint("uq_users_invite_token_hash", "users", type_="unique")
        if not has_null_sub:
            op.alter_column("users", "google_sub", existing_type=sa.String(length=255), nullable=False)
        for name in _NEW_COLUMNS:
            op.drop_column("users", name)
