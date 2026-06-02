"""Add activity tracking tables

Revision ID: ab12activity01
Revises: a1b2c3d4e5f6
Create Date: 2026-06-02 00:00:00.000000

"""
from typing import Sequence, Union
from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision: str = "ab12activity01"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "activity_categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index(op.f("ix_activity_categories_id"), "activity_categories", ["id"], unique=False)

    op.create_table(
        "category_fields",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("field_type", sa.String(length=20), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("is_required", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["activity_categories.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("category_id", "key", name="uq_category_field_key"),
    )
    op.create_index(op.f("ix_category_fields_id"), "category_fields", ["id"], unique=False)

    op.create_table(
        "activities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["activity_categories.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("category_id", "name", name="uq_activity_name_per_category"),
    )
    op.create_index(op.f("ix_activities_id"), "activities", ["id"], unique=False)
    op.create_index(op.f("ix_activities_name"), "activities", ["name"], unique=False)

    op.create_table(
        "session_activities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("activity_id", sa.Integer(), nullable=False),
        sa.Column("values", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.ForeignKeyConstraint(["activity_id"], ["activities.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_session_activities_id"), "session_activities", ["id"], unique=False)

    # ---- Seed categories + fields ----
    now = datetime.utcnow()
    categories = sa.table(
        "activity_categories",
        sa.column("id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("slug", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("sort_order", sa.Integer),
        sa.column("created_at", sa.DateTime),
    )
    op.bulk_insert(categories, [
        {"id": 1, "name": "Strength", "slug": "strength", "is_active": True, "sort_order": 1, "created_at": now},
        {"id": 2, "name": "Cardio", "slug": "cardio", "is_active": True, "sort_order": 2, "created_at": now},
        {"id": 3, "name": "Mobility", "slug": "mobility", "is_active": True, "sort_order": 3, "created_at": now},
        {"id": 4, "name": "Other", "slug": "other", "is_active": True, "sort_order": 4, "created_at": now},
    ])

    fields = sa.table(
        "category_fields",
        sa.column("category_id", sa.Integer),
        sa.column("key", sa.String),
        sa.column("label", sa.String),
        sa.column("field_type", sa.String),
        sa.column("unit", sa.String),
        sa.column("is_required", sa.Boolean),
        sa.column("is_active", sa.Boolean),
        sa.column("sort_order", sa.Integer),
        sa.column("created_at", sa.DateTime),
    )
    op.bulk_insert(fields, [
        {"category_id": 1, "key": "reps", "label": "Reps", "field_type": "integer", "unit": None, "is_required": True, "is_active": True, "sort_order": 1, "created_at": now},
        {"category_id": 1, "key": "weight", "label": "Weight", "field_type": "decimal", "unit": "kg", "is_required": False, "is_active": True, "sort_order": 2, "created_at": now},
        {"category_id": 2, "key": "distance", "label": "Distance", "field_type": "decimal", "unit": "km", "is_required": False, "is_active": True, "sort_order": 1, "created_at": now},
        {"category_id": 2, "key": "duration", "label": "Duration", "field_type": "duration", "unit": None, "is_required": False, "is_active": True, "sort_order": 2, "created_at": now},
        {"category_id": 2, "key": "pace", "label": "Pace", "field_type": "text", "unit": None, "is_required": False, "is_active": True, "sort_order": 3, "created_at": now},
        {"category_id": 3, "key": "duration", "label": "Duration", "field_type": "duration", "unit": None, "is_required": False, "is_active": True, "sort_order": 1, "created_at": now},
    ])


def downgrade() -> None:
    op.drop_index(op.f("ix_session_activities_id"), table_name="session_activities")
    op.drop_table("session_activities")
    op.drop_index(op.f("ix_activities_name"), table_name="activities")
    op.drop_index(op.f("ix_activities_id"), table_name="activities")
    op.drop_table("activities")
    op.drop_index(op.f("ix_category_fields_id"), table_name="category_fields")
    op.drop_table("category_fields")
    op.drop_index(op.f("ix_activity_categories_id"), table_name="activity_categories")
    op.drop_table("activity_categories")
