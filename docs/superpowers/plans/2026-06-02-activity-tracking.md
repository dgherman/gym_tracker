# Activity Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users optionally log activities (grouped by admin-managed categories with structured metric fields) when creating a session, and add/edit/remove them retroactively on existing sessions, with a global activity library any user can extend.

**Architecture:** Four new tables (`activity_categories`, `category_fields`, `activities`, `session_activities`). Each category owns a metric-field schema; logged values are stored as a JSON dict on `session_activities` keyed by field `key`, validated server-side against the active fields at write time. Frontend reuses a single Jinja partial (`_activity_section.html`) in both the log-session modal (`index.html`) and the edit-session modal (`history.html`). Admin manages categories/fields/activities via a new admin page.

**Tech Stack:** FastAPI, SQLAlchemy (declarative), Pydantic v2, Alembic, Jinja2, Bootstrap 5 + vanilla JS, MySQL (prod) / in-memory SQLite (tests). `.NET`-free; run from repo root with the project venv.

**Spec:** `docs/superpowers/specs/2026-06-02-activity-tracking-design.md`

**Conventions baked in from the codebase:**
- Run tests: `python -m pytest gym_tracker/tests/ -v` from repo root.
- Soft delete via `is_active`; nullable `*_user_id` ownership FKs; `created_at = datetime.utcnow`.
- DB is MySQL by default; case-insensitive uniqueness is handled in CRUD (SQLite tests are case-sensitive), backed by a `UNIQUE(category_id, name)` constraint.
- `field_type` is one of `integer`, `decimal`, `duration`, `text`. `duration` is stored as an integer number of seconds.

---

## File Structure

- `gym_tracker/models.py` — add `ActivityCategory`, `CategoryField`, `Activity`, `SessionActivity`; add `activities` relationship to `Session`.
- `alembic/versions/<rev>_add_activity_tracking.py` — new migration: 4 tables + seed categories/fields.
- `gym_tracker/schemas.py` — category/field/activity/session-activity schemas; extend `SessionCreate` and `Session`.
- `gym_tracker/activities.py` — **new** module: value validation + activity/category/field CRUD + session-activity reconciliation. (Keeps `crud.py` from growing further; it is already 541 lines.)
- `gym_tracker/crud.py` — extend `create_session` to accept activities; load activities in `get_sessions`.
- `main.py` — new read endpoints, admin endpoints, admin page route; extend `/sessions/` and `/history/api/edit/session/{id}`.
- `templates/_activity_section.html` — **new** shared partial (markup + JS namespace `ActivitySection`).
- `templates/index.html` — include partial in log modal; send activities on create.
- `templates/history.html` — include partial in edit modal; prefill + submit activities; render activities per session.
- `templates/admin/activities.html` — **new** admin management page.
- `templates/admin/index.html` — add link to the new admin page.
- `gym_tracker/tests/test_activities.py` — **new** unit tests for validation, CRUD, reconciliation.
- `gym_tracker/tests/test_activity_api.py` — **new** endpoint tests (auth, admin guard, create/edit/delete flows).

---

## Task 1: Data models

**Files:**
- Modify: `gym_tracker/models.py`

- [ ] **Step 1: Add the four models and the Session relationship**

Add these imports at the top of `gym_tracker/models.py` (extend the existing `from sqlalchemy import (...)` block to include `JSON`, `UniqueConstraint`, `Text`):

```python
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    Float,
    Boolean,
    JSON,
    Text,
    UniqueConstraint,
)
```

Append at the end of the file (after `Package`):

```python
# ─────────────────────────────────────────────────────────────
# Activity tracking
# ─────────────────────────────────────────────────────────────
class ActivityCategory(Base):
    __tablename__ = "activity_categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True)
    slug = Column(String(255), nullable=False, unique=True)
    is_active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    fields = relationship(
        "CategoryField",
        back_populates="category",
        cascade="all, delete-orphan",
        order_by="CategoryField.sort_order",
    )
    activities = relationship("Activity", back_populates="category")


class CategoryField(Base):
    __tablename__ = "category_fields"
    __table_args__ = (UniqueConstraint("category_id", "key", name="uq_category_field_key"),)

    id = Column(Integer, primary_key=True, index=True)
    category_id = Column(Integer, ForeignKey("activity_categories.id"), nullable=False)
    key = Column(String(64), nullable=False)
    label = Column(String(255), nullable=False)
    field_type = Column(String(20), nullable=False)  # integer | decimal | duration | text
    unit = Column(String(32), nullable=True)
    is_required = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    category = relationship("ActivityCategory", back_populates="fields")


class Activity(Base):
    __tablename__ = "activities"
    __table_args__ = (UniqueConstraint("category_id", "name", name="uq_activity_name_per_category"),)

    id = Column(Integer, primary_key=True, index=True)
    category_id = Column(Integer, ForeignKey("activity_categories.id"), nullable=False)
    name = Column(String(255), nullable=False, index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    category = relationship("ActivityCategory", back_populates="activities")


class SessionActivity(Base):
    __tablename__ = "session_activities"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False)
    activity_id = Column(Integer, ForeignKey("activities.id"), nullable=False)
    values = Column(JSON, nullable=False, default=dict)
    notes = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    session = relationship("Session", back_populates="activities")
    activity = relationship("Activity")
```

Inside the existing `Session` class, add the relationship right after the `trainer_rel` relationship (line ~110):

```python
    activities = relationship(
        "SessionActivity",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="SessionActivity.sort_order",
    )
```

The `cascade="all, delete-orphan"` makes the existing `db.delete(s)` in `api_delete_session` (main.py:350) also delete the session's `session_activities` rows — satisfies the spec's cascade requirement with no endpoint change.

- [ ] **Step 2: Verify models import cleanly**

Run: `python -c "from gym_tracker import models; print(models.SessionActivity.__tablename__)"`
Expected: prints `session_activities` with no error.

- [ ] **Step 3: Commit**

```bash
git add gym_tracker/models.py
git commit -m "feat(models): add activity tracking tables"
```

---

## Task 2: Alembic migration

**Files:**
- Create: `alembic/versions/<rev>_add_activity_tracking.py` (generated filename)

- [ ] **Step 1: Find the current head revision**

Run: `python -m alembic heads`
Expected: prints one revision id (the latest). Note it — call it `<DOWN_REV>`. (At time of writing the latest migration in the repo is `499000930544_add_packages_table.py`; confirm with the command rather than assuming.)

- [ ] **Step 2: Create the migration file by hand**

Create `alembic/versions/ab12activity01_add_activity_tracking.py` with `down_revision` set to `<DOWN_REV>` from Step 1:

```python
"""Add activity tracking tables

Revision ID: ab12activity01
Revises: <DOWN_REV>
Create Date: 2026-06-02 00:00:00.000000

"""
from typing import Sequence, Union
from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision: str = "ab12activity01"
down_revision: Union[str, Sequence[str], None] = "<DOWN_REV>"
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
        {"category_id": 1, "key": "reps", "label": "Reps", "field_type": "integer", "unit": None, "is_required": False, "is_active": True, "sort_order": 1, "created_at": now},
        {"category_id": 1, "key": "weight", "label": "Weight", "field_type": "decimal", "unit": "lbs", "is_required": False, "is_active": True, "sort_order": 2, "created_at": now},
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
```

- [ ] **Step 3: Verify migration parses and is linear**

Run: `python -m alembic history | head -5`
Expected: lists `ab12activity01` as the new head, chained to `<DOWN_REV>`. No "multiple heads" error.

(Do not run `alembic upgrade` against MySQL here — tests use SQLite metadata directly. The container runs migrations on start per the repo's existing setup.)

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/ab12activity01_add_activity_tracking.py
git commit -m "feat(db): migration for activity tracking tables + seed"
```

---

## Task 3: Schemas

**Files:**
- Modify: `gym_tracker/schemas.py`

- [ ] **Step 1: Add activity schemas and extend session schemas**

Append to `gym_tracker/schemas.py`:

```python
# --------------------
# Activity Tracking Schemas
# --------------------

ALLOWED_FIELD_TYPES = {"integer", "decimal", "duration", "text"}


class CategoryFieldRead(BaseModel):
    id: int
    key: str
    label: str
    field_type: str
    unit: str | None = None
    is_required: bool
    sort_order: int
    model_config = {"from_attributes": True}


class ActivityCategoryRead(BaseModel):
    id: int
    name: str
    slug: str
    sort_order: int
    fields: list[CategoryFieldRead] = []
    model_config = {"from_attributes": True}


class ActivityRead(BaseModel):
    id: int
    category_id: int
    name: str
    is_active: bool
    model_config = {"from_attributes": True}


class ActivityCreate(BaseModel):
    category_id: int
    name: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        if not v or not v.strip():
            raise ValueError("Activity name is required and cannot be empty")
        return v.strip()


class SessionActivityInput(BaseModel):
    # id present => update existing row; absent => insert
    id: int | None = None
    activity_id: int
    values: dict = {}
    notes: str | None = None


class SessionActivityRead(BaseModel):
    id: int
    activity_id: int
    activity_name: str
    category_id: int
    category_name: str
    values: dict = {}
    notes: str | None = None
    sort_order: int
    model_config = {"from_attributes": True}


# ---- Admin management schemas ----

class CategoryCreate(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        if not v or not v.strip():
            raise ValueError("Category name is required")
        return v.strip()


class CategoryUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    sort_order: int | None = None


class CategoryFieldCreate(BaseModel):
    key: str
    label: str
    field_type: str
    unit: str | None = None
    is_required: bool = False
    sort_order: int = 0

    @field_validator("key")
    @classmethod
    def validate_key(cls, v):
        v = (v or "").strip().lower()
        if not v:
            raise ValueError("Field key is required")
        if not all(c.isalnum() or c == "_" for c in v):
            raise ValueError("Field key must be alphanumeric/underscore")
        return v

    @field_validator("field_type")
    @classmethod
    def validate_type(cls, v):
        if v not in ALLOWED_FIELD_TYPES:
            raise ValueError(f"field_type must be one of {sorted(ALLOWED_FIELD_TYPES)}")
        return v


class CategoryFieldUpdate(BaseModel):
    label: str | None = None
    unit: str | None = None
    is_required: bool | None = None
    is_active: bool | None = None
    sort_order: int | None = None


class ActivityUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        if v is not None and (not v or not v.strip()):
            raise ValueError("Activity name cannot be empty")
        return v.strip() if v else v
```

Now extend the existing session schemas. Change `SessionCreate` (currently at line 13) to add an `activities` field:

```python
class SessionCreate(BaseModel):
    duration_minutes: int
    trainer: str
    num_people: int = 1
    partner_email: str | None = None
    activities: list["SessionActivityInput"] = []

    @field_validator('trainer')
    @classmethod
    def validate_trainer(cls, v):
        if not v or not v.strip():
            raise ValueError('Trainer name is required and cannot be empty')
        return v.strip()

    @field_validator('partner_email')
    @classmethod
    def validate_partner_email(cls, v):
        if v is not None:
            v = v.strip().lower()
            if not v:
                return None
        return v
```

Change the read `Session` schema (currently at line 35) to expose activities:

```python
class Session(SessionBase):
    id: int
    purchase_id: int
    purchase_exhausted: bool = False
    partner_email: str | None = None
    partner_name: str | None = None
    num_people: int = 1
    is_owner: bool = True
    activities: list["SessionActivityRead"] = []
    model_config = {
        "from_attributes": True
    }
```

Because `SessionCreate`/`Session` reference `SessionActivityInput`/`SessionActivityRead` defined later in the file, add at the very end of the file:

```python
SessionCreate.model_rebuild()
Session.model_rebuild()
```

- [ ] **Step 2: Verify schemas import and rebuild cleanly**

Run: `python -c "from gym_tracker import schemas; schemas.SessionCreate(duration_minutes=30, trainer='X'); print('ok')"`
Expected: prints `ok` (default `activities=[]`).

- [ ] **Step 3: Commit**

```bash
git add gym_tracker/schemas.py
git commit -m "feat(schemas): activity tracking + extend session schemas"
```

---

## Task 4: Value validation (TDD)

**Files:**
- Create: `gym_tracker/activities.py`
- Test: `gym_tracker/tests/test_activities.py`

- [ ] **Step 1: Write the failing test**

Create `gym_tracker/tests/test_activities.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from gym_tracker.database import Base
from gym_tracker import models, activities

test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture
def db():
    Base.metadata.create_all(bind=test_engine)
    db = TestSessionLocal()
    # Seed a Strength category with reps (required int) + weight (decimal)
    cat = models.ActivityCategory(name="Strength", slug="strength", sort_order=1)
    db.add(cat)
    db.flush()
    db.add_all([
        models.CategoryField(category_id=cat.id, key="reps", label="Reps",
                             field_type="integer", is_required=True, sort_order=1),
        models.CategoryField(category_id=cat.id, key="weight", label="Weight",
                             field_type="decimal", unit="lbs", is_required=False, sort_order=2),
    ])
    act = models.Activity(category_id=cat.id, name="Bench Press")
    db.add(act)
    db.commit()
    db.refresh(act)
    try:
        yield db, act
    finally:
        db.close()
        Base.metadata.drop_all(bind=test_engine)


def test_valid_values_are_coerced(db):
    db_, act = db
    cleaned = activities.validate_activity_values(db_, act, {"reps": "8", "weight": "60.5"})
    assert cleaned == {"reps": 8, "weight": 60.5}


def test_missing_required_field_raises(db):
    db_, act = db
    with pytest.raises(ValueError, match="Reps"):
        activities.validate_activity_values(db_, act, {"weight": "60"})


def test_unknown_key_raises(db):
    db_, act = db
    with pytest.raises(ValueError, match="Unknown"):
        activities.validate_activity_values(db_, act, {"reps": "8", "bogus": "1"})


def test_wrong_type_raises(db):
    db_, act = db
    with pytest.raises(ValueError, match="number|integer"):
        activities.validate_activity_values(db_, act, {"reps": "eight"})


def test_optional_blank_is_omitted(db):
    db_, act = db
    cleaned = activities.validate_activity_values(db_, act, {"reps": "8", "weight": ""})
    assert cleaned == {"reps": 8}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest gym_tracker/tests/test_activities.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gym_tracker.activities'` (or `AttributeError`).

- [ ] **Step 3: Write minimal implementation**

Create `gym_tracker/activities.py`:

```python
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from gym_tracker import models, schemas


def _coerce(field_type: str, label: str, raw):
    if field_type in ("integer", "duration"):
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"Field '{label}' must be a whole number")
    if field_type == "decimal":
        try:
            return float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"Field '{label}' must be a number")
    # text
    return str(raw)


def validate_activity_values(db: Session, activity: models.Activity, values: dict) -> dict:
    """Validate a values dict against the activity's category active fields.
    Returns a cleaned dict (only present, coerced values). Raises ValueError."""
    values = values or {}
    fields = (
        db.query(models.CategoryField)
        .filter(
            models.CategoryField.category_id == activity.category_id,
            models.CategoryField.is_active == True,  # noqa: E712
        )
        .all()
    )
    by_key = {f.key: f for f in fields}

    for k in values:
        if k not in by_key:
            raise ValueError(f"Unknown field '{k}' for this activity")

    cleaned = {}
    for f in fields:
        raw = values.get(f.key)
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            if f.is_required:
                raise ValueError(f"Field '{f.label}' is required")
            continue
        cleaned[f.key] = _coerce(f.field_type, f.label, raw)
    return cleaned
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest gym_tracker/tests/test_activities.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add gym_tracker/activities.py gym_tracker/tests/test_activities.py
git commit -m "feat(activities): value validation against category fields"
```

---

## Task 5: Activity / category / field CRUD (TDD)

**Files:**
- Modify: `gym_tracker/activities.py`
- Test: `gym_tracker/tests/test_activities.py`

- [ ] **Step 1: Write the failing test**

Append to `gym_tracker/tests/test_activities.py`:

```python
def test_create_activity_dedup_case_insensitive(db):
    db_, act = db
    a1 = activities.create_activity(db_, schemas.ActivityCreate(category_id=act.category_id, name="Squat"))
    a2 = activities.create_activity(db_, schemas.ActivityCreate(category_id=act.category_id, name="squat"))
    assert a1.id == a2.id  # dedup returns existing


def test_create_activity_bad_category_raises(db):
    db_, act = db
    with pytest.raises(ValueError, match="category"):
        activities.create_activity(db_, schemas.ActivityCreate(category_id=999, name="Nope"))


def test_list_active_activities_in_category(db):
    db_, act = db
    activities.create_activity(db_, schemas.ActivityCreate(category_id=act.category_id, name="Deadlift"))
    rows = activities.list_activities(db_, category_id=act.category_id)
    names = {r.name for r in rows}
    assert "Bench Press" in names and "Deadlift" in names
```

Add `from gym_tracker import schemas` import already present at top of test file (it imports `models, activities`); add `schemas`:

```python
from gym_tracker import models, activities, schemas
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest gym_tracker/tests/test_activities.py -k "activity" -v`
Expected: FAIL — `module 'gym_tracker.activities' has no attribute 'create_activity'`.

- [ ] **Step 3: Write minimal implementation**

Append to `gym_tracker/activities.py`:

```python
# --------------------
# Category reads
# --------------------

def list_categories(db: Session):
    return (
        db.query(models.ActivityCategory)
        .filter(models.ActivityCategory.is_active == True)  # noqa: E712
        .order_by(models.ActivityCategory.sort_order, models.ActivityCategory.name)
        .all()
    )


def list_activities(db: Session, *, category_id: Optional[int] = None):
    q = db.query(models.Activity).filter(models.Activity.is_active == True)  # noqa: E712
    if category_id is not None:
        q = q.filter(models.Activity.category_id == category_id)
    return q.order_by(models.Activity.name).all()


# --------------------
# Activity create (dedup, global)
# --------------------

def create_activity(db: Session, activity_in: schemas.ActivityCreate, *, created_by_user_id=None):
    category = db.get(models.ActivityCategory, activity_in.category_id)
    if not category:
        raise ValueError("Unknown category")

    name = activity_in.name.strip()
    existing = (
        db.query(models.Activity)
        .filter(
            models.Activity.category_id == activity_in.category_id,
            func.lower(models.Activity.name) == name.lower(),
        )
        .first()
    )
    if existing:
        if not existing.is_active:
            existing.is_active = True
            db.commit()
            db.refresh(existing)
        return existing

    activity = models.Activity(
        category_id=activity_in.category_id,
        name=name,
        is_active=True,
        created_by_user_id=created_by_user_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity


def update_activity(db: Session, activity_id: int, update: "schemas.ActivityUpdate"):
    activity = db.get(models.Activity, activity_id)
    if not activity:
        return None
    data = update.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(activity, k, v)
    db.commit()
    db.refresh(activity)
    return activity
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest gym_tracker/tests/test_activities.py -v`
Expected: all passed (validation + activity tests).

- [ ] **Step 5: Add category/field admin CRUD**

Append to `gym_tracker/activities.py`:

```python
import re


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "category"


def create_category(db: Session, category_in: "schemas.CategoryCreate"):
    name = category_in.name.strip()
    slug = _slugify(name)
    max_order = db.query(func.max(models.ActivityCategory.sort_order)).scalar() or 0
    cat = models.ActivityCategory(
        name=name, slug=slug, is_active=True, sort_order=max_order + 1,
        created_at=datetime.now(timezone.utc),
    )
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


def update_category(db: Session, category_id: int, update: "schemas.CategoryUpdate"):
    cat = db.get(models.ActivityCategory, category_id)
    if not cat:
        return None
    for k, v in update.model_dump(exclude_unset=True).items():
        setattr(cat, k, v)
    db.commit()
    db.refresh(cat)
    return cat


def create_field(db: Session, category_id: int, field_in: "schemas.CategoryFieldCreate"):
    cat = db.get(models.ActivityCategory, category_id)
    if not cat:
        raise ValueError("Unknown category")
    dup = (
        db.query(models.CategoryField)
        .filter(models.CategoryField.category_id == category_id,
                models.CategoryField.key == field_in.key)
        .first()
    )
    if dup:
        raise ValueError(f"Field key '{field_in.key}' already exists in this category")
    field = models.CategoryField(
        category_id=category_id,
        key=field_in.key,
        label=field_in.label.strip(),
        field_type=field_in.field_type,
        unit=(field_in.unit or None),
        is_required=field_in.is_required,
        is_active=True,
        sort_order=field_in.sort_order,
        created_at=datetime.now(timezone.utc),
    )
    db.add(field)
    db.commit()
    db.refresh(field)
    return field


def update_field(db: Session, field_id: int, update: "schemas.CategoryFieldUpdate"):
    field = db.get(models.CategoryField, field_id)
    if not field:
        return None
    for k, v in update.model_dump(exclude_unset=True).items():
        setattr(field, k, v)
    db.commit()
    db.refresh(field)
    return field


def soft_delete_field(db: Session, field_id: int):
    field = db.get(models.CategoryField, field_id)
    if not field:
        return None
    field.is_active = False
    db.commit()
    return field
```

- [ ] **Step 6: Run full test file**

Run: `python -m pytest gym_tracker/tests/test_activities.py -v`
Expected: all passed.

- [ ] **Step 7: Commit**

```bash
git add gym_tracker/activities.py gym_tracker/tests/test_activities.py
git commit -m "feat(activities): category/field/activity CRUD with dedup"
```

---

## Task 6: Session-activity reconciliation (TDD)

**Files:**
- Modify: `gym_tracker/activities.py`
- Test: `gym_tracker/tests/test_activities.py`

- [ ] **Step 1: Write the failing test**

Append to `gym_tracker/tests/test_activities.py`:

```python
def _make_session(db_):
    # Minimal purchase + session to attach activities to
    pur = models.Purchase(duration_minutes=30, total_sessions=10, sessions_remaining=10)
    db_.add(pur)
    db_.flush()
    sess = models.Session(purchase_id=pur.id, duration_minutes=30, trainer="X")
    db_.add(sess)
    db_.commit()
    db_.refresh(sess)
    return sess


def test_reconcile_inserts_rows(db):
    db_, act = db
    sess = _make_session(db_)
    items = [schemas.SessionActivityInput(activity_id=act.id, values={"reps": "8", "weight": "60"})]
    activities.reconcile_session_activities(db_, sess, items)
    db_.commit()
    db_.refresh(sess)
    assert len(sess.activities) == 1
    assert sess.activities[0].values == {"reps": 8, "weight": 60.0}


def test_reconcile_updates_and_deletes(db):
    db_, act = db
    sess = _make_session(db_)
    activities.reconcile_session_activities(
        db_, sess, [schemas.SessionActivityInput(activity_id=act.id, values={"reps": "5"})]
    )
    db_.commit(); db_.refresh(sess)
    existing_id = sess.activities[0].id

    # Update the existing row, no new rows -> nothing else
    activities.reconcile_session_activities(
        db_, sess, [schemas.SessionActivityInput(id=existing_id, activity_id=act.id, values={"reps": "10"})]
    )
    db_.commit(); db_.refresh(sess)
    assert len(sess.activities) == 1
    assert sess.activities[0].values == {"reps": 10}

    # Empty list -> row removed
    activities.reconcile_session_activities(db_, sess, [])
    db_.commit(); db_.refresh(sess)
    assert len(sess.activities) == 0


def test_reconcile_foreign_row_id_rejected(db):
    db_, act = db
    sess = _make_session(db_)
    with pytest.raises(ValueError, match="not on this session"):
        activities.reconcile_session_activities(
            db_, sess, [schemas.SessionActivityInput(id=999, activity_id=act.id, values={"reps": "8"})]
        )


def test_reconcile_inactive_activity_rejected(db):
    db_, act = db
    sess = _make_session(db_)
    act.is_active = False
    db_.commit()
    with pytest.raises(ValueError, match="not available|inactive"):
        activities.reconcile_session_activities(
            db_, sess, [schemas.SessionActivityInput(activity_id=act.id, values={"reps": "8"})]
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest gym_tracker/tests/test_activities.py -k reconcile -v`
Expected: FAIL — `has no attribute 'reconcile_session_activities'`.

- [ ] **Step 3: Write minimal implementation**

Append to `gym_tracker/activities.py`:

```python
def reconcile_session_activities(db: Session, session: models.Session, items, *, created_by_user_id=None):
    """Reconcile a session's activities to the desired set `items`
    (list of schemas.SessionActivityInput). Upsert by id, delete omitted.
    Validates each row's values; raises ValueError on any problem.
    Does NOT commit — caller commits so the whole session edit is atomic."""
    existing = {sa.id: sa for sa in session.activities}
    seen_ids = set()

    for idx, item in enumerate(items):
        activity = db.get(models.Activity, item.activity_id)
        if not activity or not activity.is_active:
            raise ValueError("Selected activity is not available (inactive or unknown)")
        cleaned = validate_activity_values(db, activity, item.values)

        if item.id is not None:
            sa = existing.get(item.id)
            if sa is None:
                raise ValueError("Activity row is not on this session")
            sa.activity_id = activity.id
            sa.values = cleaned
            sa.notes = item.notes
            sa.sort_order = idx
            seen_ids.add(item.id)
        else:
            sa = models.SessionActivity(
                session_id=session.id,
                activity_id=activity.id,
                values=cleaned,
                notes=item.notes,
                sort_order=idx,
                created_at=datetime.now(timezone.utc),
            )
            db.add(sa)

    for sid, sa in existing.items():
        if sid not in seen_ids:
            db.delete(sa)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest gym_tracker/tests/test_activities.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add gym_tracker/activities.py gym_tracker/tests/test_activities.py
git commit -m "feat(activities): reconcile session activities (upsert/delete)"
```

---

## Task 7: Wire activities into session create + history loading (TDD)

**Files:**
- Modify: `gym_tracker/crud.py`
- Test: `gym_tracker/tests/test_activities.py`

- [ ] **Step 1: Write the failing test**

Append to `gym_tracker/tests/test_activities.py`:

```python
from gym_tracker import crud


def test_create_session_with_activities(db):
    db_, act = db
    # need a purchase to consume
    pur = models.Purchase(duration_minutes=45, total_sessions=10, sessions_remaining=10)
    db_.add(pur); db_.commit()

    items = [schemas.SessionActivityInput(activity_id=act.id, values={"reps": "8", "weight": "70"})]
    sess = crud.create_session(db_, 45, "Rachel", activities=items)
    assert len(sess.activities) == 1
    assert sess.activities[0].activity_name == "Bench Press"
    assert sess.activities[0].category_name == "Strength"


def test_create_session_bad_activity_rolls_back(db):
    db_, act = db
    pur = models.Purchase(duration_minutes=45, total_sessions=10, sessions_remaining=10)
    db_.add(pur); db_.commit()
    remaining_before = pur.sessions_remaining
    with pytest.raises(ValueError):
        crud.create_session(
            db_, 45, "Rachel",
            activities=[schemas.SessionActivityInput(activity_id=act.id, values={"reps": "oops"})],
        )
    db_.rollback()
    db_.refresh(pur)
    assert pur.sessions_remaining == remaining_before  # not consumed


def test_get_sessions_annotates_activities(db):
    db_, act = db
    pur = models.Purchase(duration_minutes=45, total_sessions=10, sessions_remaining=10)
    db_.add(pur); db_.commit()
    crud.create_session(db_, 45, "Rachel",
                        activities=[schemas.SessionActivityInput(activity_id=act.id, values={"reps": "8"})])
    sessions = crud.get_sessions(db_)
    target = [s for s in sessions if s.activities]
    assert target and target[0].activities[0].activity_name == "Bench Press"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest gym_tracker/tests/test_activities.py -k "session_with_activities or rolls_back or annotates" -v`
Expected: FAIL — `create_session() got an unexpected keyword argument 'activities'`.

- [ ] **Step 3: Modify `create_session` in `gym_tracker/crud.py`**

Add an import near the top of `crud.py` (after the existing `from gym_tracker import models, schemas`):

```python
from gym_tracker import activities as activities_mod
```

Change the `create_session` signature and body. Replace the current signature (line 202) and the commit block. New version:

```python
def create_session(
    db: Session,
    duration_minutes: int,
    trainer: str,
    *,
    created_by_user_id: Optional[int] = None,
    partner_email: Optional[str] = None,
    num_people: int = 1,
    activities: Optional[list] = None,
):
    """
    Creates a session by consuming one matching purchase (oldest first),
    records who created it, and optionally attaches activities.
    The purchase decrement + session + activities are committed atomically:
    a bad activity raises ValueError before commit and nothing is persisted.
    """
    if not trainer or not trainer.strip():
        raise ValueError("Trainer name is required and cannot be empty")
    pack_q = (
        db.query(models.Purchase)
        .filter(
            models.Purchase.duration_minutes == duration_minutes,
            models.Purchase.num_people == num_people,
            models.Purchase.sessions_remaining > 0,
        )
        .order_by(models.Purchase.purchase_date)
    )
    if created_by_user_id is not None:
        pack_q = pack_q.filter(_user_purchase_filter(created_by_user_id))

    purchase = pack_q.first()
    if not purchase:
        raise ValueError("No available purchase with remaining sessions for this duration")

    session_partner_id = None
    if partner_email:
        session_partner_id = _resolve_partner(db, partner_email)

    purchase.sessions_remaining -= 1
    db_session = models.Session(
        purchase_id=purchase.id,
        duration_minutes=duration_minutes,
        trainer=trainer,
        session_date=datetime.now(timezone.utc),
        created_by_user_id=created_by_user_id,
        partner_user_id=session_partner_id,
    )
    db.add(db_session)
    db.flush()  # assign db_session.id without committing

    if activities:
        # Raises ValueError on bad data -> caller/endpoint maps to 400; no commit happened
        activities_mod.reconcile_session_activities(
            db, db_session, activities, created_by_user_id=created_by_user_id
        )

    db.commit()
    db.refresh(db_session)

    db_session.purchase_exhausted = (purchase.sessions_remaining == 0)
    _annotate_session(db_session, purchase, created_by_user_id)
    _annotate_session_activities(db, db_session)
    return db_session
```

- [ ] **Step 4: Add the activity annotation helper + load in `get_sessions`**

Add this helper to `crud.py` (place it right after `_annotate_session`, around line 97):

```python
def _annotate_session_activities(db, sess):
    """Attach activity_name, category_id, category_name onto each
    SessionActivity so the SessionActivityRead schema can serialize it."""
    for sa in sess.activities:
        activity = sa.activity or db.get(models.Activity, sa.activity_id)
        sa.activity_name = activity.name if activity else "(unknown)"
        category = db.get(models.ActivityCategory, activity.category_id) if activity else None
        sa.category_id = category.id if category else 0
        sa.category_name = category.name if category else "(unknown)"
```

In `get_sessions` (line 260), add annotation inside the existing loop. Replace the loop body:

```python
    for sess in sessions:
        purchase = db.get(models.Purchase, sess.purchase_id)
        sess.purchase_exhausted = (purchase.sessions_remaining == 0)
        if user_id is not None:
            _annotate_session(sess, purchase, user_id)
        _annotate_session_activities(db, sess)
    return sessions
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest gym_tracker/tests/test_activities.py gym_tracker/tests/test_crud.py -v`
Expected: all passed (existing `test_crud.py` still green — `create_session` defaults `activities=None`).

- [ ] **Step 6: Commit**

```bash
git add gym_tracker/crud.py gym_tracker/tests/test_activities.py
git commit -m "feat(crud): attach activities on session create + history load"
```

---

## Task 8: API endpoints

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Add read endpoints + extend session create/edit**

Add an import at the top of `main.py` (after `from gym_tracker import crud, models, schemas`):

```python
from gym_tracker import activities as activities_mod
```

Extend the existing `create_session` endpoint (line 170) to pass activities through. Replace its body:

```python
@app.post("/sessions/", response_model=schemas.Session)
def create_session(
    request: Request,
    session_in: schemas.SessionCreate,
    db: Session = Depends(get_db),
):
    user_id = request.session.get("user_id")
    try:
        return crud.create_session(
            db,
            session_in.duration_minutes,
            session_in.trainer,
            created_by_user_id=user_id,
            partner_email=session_in.partner_email,
            num_people=session_in.num_people,
            activities=session_in.activities,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

In `api_edit_session` (line 245), after the line `s.trainer = data["trainer"]` and **before** `db.commit()`, insert activity reconciliation:

```python
    s.session_date = datetime.fromisoformat(data["session_date"])
    s.trainer = data["trainer"]

    # Reconcile activities if provided (full desired set)
    if "activities" in data and data["activities"] is not None:
        try:
            items = [schemas.SessionActivityInput(**row) for row in data["activities"]]
            activities_mod.reconcile_session_activities(db, s, items, created_by_user_id=user_id)
        except (ValueError, TypeError) as e:
            db.rollback()
            raise HTTPException(status_code=400, detail=str(e))

    db.commit()
    return {"success": True}
```

- [ ] **Step 2: Add activity read + create endpoints**

Add after the trainers API block (after line 459, before the Admin Console section):

```python
# -------------------------------------------------------------
# Activity Tracking API endpoints
# -------------------------------------------------------------

@app.get("/api/categories", response_model=List[schemas.ActivityCategoryRead])
def list_categories(db: Session = Depends(get_db)):
    """Active categories with their active fields (drives the log form)."""
    cats = activities_mod.list_categories(db)
    # Hide inactive fields from the form payload
    for c in cats:
        c.fields = [f for f in c.fields if f.is_active]
    return cats


@app.get("/api/activities", response_model=List[schemas.ActivityRead])
def list_activities(category_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Active activities, optionally filtered by category."""
    return activities_mod.list_activities(db, category_id=category_id)


@app.post("/api/activities", response_model=schemas.ActivityRead)
def create_activity(
    request: Request,
    activity_in: schemas.ActivityCreate,
    db: Session = Depends(get_db),
):
    """Create a global activity (any authenticated user). Dedups by name."""
    user_id = request.session.get("user_id")
    try:
        return activities_mod.create_activity(db, activity_in, created_by_user_id=user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

- [ ] **Step 3: Add admin endpoints + admin page route**

Add after the `admin_packages` route (end of file):

```python
# -------------------------------------------------------------
# Activity admin API + page
# -------------------------------------------------------------

@app.post("/api/admin/categories", response_model=schemas.ActivityCategoryRead)
def admin_create_category(
    category_in: schemas.CategoryCreate,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return activities_mod.create_category(db, category_in)


@app.patch("/api/admin/categories/{category_id}", response_model=schemas.ActivityCategoryRead)
def admin_update_category(
    category_id: int,
    update: schemas.CategoryUpdate,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    cat = activities_mod.update_category(db, category_id, update)
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    return cat


@app.post("/api/admin/categories/{category_id}/fields", response_model=schemas.CategoryFieldRead)
def admin_create_field(
    category_id: int,
    field_in: schemas.CategoryFieldCreate,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        return activities_mod.create_field(db, category_id, field_in)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.patch("/api/admin/categories/{category_id}/fields/{field_id}", response_model=schemas.CategoryFieldRead)
def admin_update_field(
    category_id: int,
    field_id: int,
    update: schemas.CategoryFieldUpdate,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    field = activities_mod.update_field(db, field_id, update)
    if not field:
        raise HTTPException(status_code=404, detail="Field not found")
    return field


@app.delete("/api/admin/categories/{category_id}/fields/{field_id}", response_model=schemas.CategoryFieldRead)
def admin_delete_field(
    category_id: int,
    field_id: int,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    field = activities_mod.soft_delete_field(db, field_id)
    if not field:
        raise HTTPException(status_code=404, detail="Field not found")
    return field


@app.patch("/api/admin/activities/{activity_id}", response_model=schemas.ActivityRead)
def admin_update_activity(
    activity_id: int,
    update: schemas.ActivityUpdate,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    activity = activities_mod.update_activity(db, activity_id, update)
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
    return activity


@app.get("/admin/activities", response_class=HTMLResponse)
def admin_activities(
    request: Request,
    admin_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin activity/category/field management page."""
    categories = activities_mod.list_categories(db)
    return templates.TemplateResponse(
        request,
        "admin/activities.html",
        {"current_user": admin_user, "categories": categories},
    )
```

- [ ] **Step 4: Smoke-check the app imports**

Run: `python -c "import main; print([r.path for r in main.app.routes if 'activit' in r.path or 'categor' in r.path])"`
Expected: lists `/api/categories`, `/api/activities`, `/admin/activities`, the admin category/field/activity routes — no import error.

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat(api): activity read/create + admin endpoints; wire session create/edit"
```

---

## Task 9: API integration tests (TDD)

**Files:**
- Create: `gym_tracker/tests/test_activity_api.py`

- [ ] **Step 1: Write the test**

Create `gym_tracker/tests/test_activity_api.py`:

```python
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from gym_tracker.database import Base
from gym_tracker import models

test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture
def client():
    Base.metadata.create_all(bind=test_engine)
    db = TestSessionLocal()
    # Seed category/field/activity + an admin and a client user + a purchase
    cat = models.ActivityCategory(name="Strength", slug="strength", sort_order=1)
    db.add(cat); db.flush()
    db.add(models.CategoryField(category_id=cat.id, key="reps", label="Reps",
                                field_type="integer", is_required=True, sort_order=1))
    act = models.Activity(category_id=cat.id, name="Bench Press"); db.add(act)
    admin = models.User(google_sub="admin-sub", email="a@x.com", role="admin")
    user = models.User(google_sub="user-sub", email="u@x.com", role="client")
    db.add_all([admin, user]); db.flush()
    db.add(models.Purchase(duration_minutes=30, total_sessions=10, sessions_remaining=10,
                           logged_by_user_id=user.id))
    db.commit()
    ids = {"cat": cat.id, "act": act.id, "admin": admin.id, "user": user.id}
    db.close()

    def override_get_db():
        d = TestSessionLocal()
        try:
            yield d
        finally:
            d.close()

    main.app.dependency_overrides[main.get_db] = override_get_db
    c = TestClient(main.app)
    c._ids = ids
    try:
        yield c
    finally:
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=test_engine)


def _login(client, user_id):
    # Set the session cookie by hitting a route after stuffing session.
    # SessionMiddleware persists request.session; we set it via a helper route.
    client.cookies.clear()
    # Use the test client's session by posting through a login shim:
    with client as c:
        # Directly set session via Starlette test client is not exposed;
        # instead, use the JSON-accept path which bypasses login redirect,
        # and pass user via session by monkeypatching get_current_user.
        pass


def test_list_categories_includes_fields(client):
    r = client.get("/api/categories", headers={"accept": "application/json"})
    assert r.status_code == 200
    cats = r.json()
    assert cats[0]["name"] == "Strength"
    assert any(f["key"] == "reps" for f in cats[0]["fields"])


def test_list_activities_filtered(client):
    cat_id = client._ids["cat"]
    r = client.get(f"/api/activities?category_id={cat_id}", headers={"accept": "application/json"})
    assert r.status_code == 200
    assert any(a["name"] == "Bench Press" for a in r.json())


def test_admin_endpoints_require_admin(client):
    # No session user -> require_admin raises 401
    r = client.post("/api/admin/categories", json={"name": "Yoga"},
                    headers={"accept": "application/json"})
    assert r.status_code in (401, 403)
```

> **Note for the implementer:** Authenticated/admin-session flows depend on `request.session["user_id"]`, set by `SessionMiddleware`. The simplest robust approach is to override `main.get_current_user` and `main.require_admin` via `main.app.dependency_overrides` (for `require_admin`) and monkeypatch `main.get_current_user` for session-create/edit tests, rather than driving the OAuth flow. Implement the three tests above first (they need no login), then add admin-path tests by overriding `main.require_admin` to return the seeded admin `models.User`. Do **not** leave `_login` as a no-op — either implement the override approach or delete the unused helper. Keep each added test green before moving on.

- [ ] **Step 2: Run the no-login tests**

Run: `python -m pytest gym_tracker/tests/test_activity_api.py -v`
Expected: `test_list_categories_includes_fields`, `test_list_activities_filtered`, `test_admin_endpoints_require_admin` pass.

- [ ] **Step 3: Add admin + session-activity flow tests**

Add to `test_activity_api.py` (using `dependency_overrides` for `require_admin`):

```python
def test_admin_create_category_and_field(client):
    admin_id = client._ids["admin"]

    def fake_admin():
        d = TestSessionLocal()
        try:
            return d.get(models.User, admin_id)
        finally:
            d.close()

    main.app.dependency_overrides[main.require_admin] = fake_admin
    try:
        r = client.post("/api/admin/categories", json={"name": "Yoga"},
                        headers={"accept": "application/json"})
        assert r.status_code == 200, r.text
        new_cat_id = r.json()["id"]
        rf = client.post(f"/api/admin/categories/{new_cat_id}/fields",
                         json={"key": "minutes", "label": "Minutes", "field_type": "duration"},
                         headers={"accept": "application/json"})
        assert rf.status_code == 200, rf.text
        assert rf.json()["key"] == "minutes"
    finally:
        main.app.dependency_overrides.pop(main.require_admin, None)
```

- [ ] **Step 4: Run all API tests**

Run: `python -m pytest gym_tracker/tests/ -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add gym_tracker/tests/test_activity_api.py
git commit -m "test(api): activity endpoints + admin guard"
```

---

## Task 10: Shared frontend partial

**Files:**
- Create: `templates/_activity_section.html`

- [ ] **Step 1: Create the partial**

Create `templates/_activity_section.html`. It renders a self-contained, collapsible activity picker and defines a global `ActivitySection` JS namespace. It is included **once per page** (log modal on index, edit modal on history).

```html
<!-- Reusable optional activity logging section. Include once per page. -->
<div class="border rounded mt-3">
  <button type="button" class="btn btn-link text-decoration-none w-100 text-start fw-bold"
          data-bs-toggle="collapse" data-bs-target="#actSectionBody">
    Log activities <span class="text-muted fw-normal">(optional)</span>
  </button>
  <div class="collapse" id="actSectionBody">
    <div class="p-2">
      <ul class="list-group mb-2" id="actList"></ul>

      <div class="border-top pt-2">
        <div class="row g-2">
          <div class="col-12">
            <select id="actCategory" class="form-select form-select-sm">
              <option value="">-- Category --</option>
            </select>
          </div>
          <div class="col-12">
            <input id="actActivity" list="actActivityList" class="form-control form-control-sm"
                   placeholder="Activity (type to search or add new)">
            <datalist id="actActivityList"></datalist>
          </div>
          <div class="col-12" id="actFields"></div>
          <div class="col-12">
            <input id="actNote" class="form-control form-control-sm" placeholder="Note (optional)">
          </div>
          <div class="col-12">
            <button type="button" id="actAddBtn" class="btn btn-sm btn-outline-success">+ Add to session</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>

<script>
const ActivitySection = (function () {
  let categories = [];          // [{id,name,fields:[{key,label,field_type,unit,is_required}]}]
  let activitiesByCat = {};     // catId -> [{id,name}]
  let rows = [];                // pending {id?, activity_id, activity_name, category_name, values, notes}

  function fieldMap() {
    const m = {};
    categories.forEach(c => c.fields.forEach(f => { m[c.id + ':' + f.key] = f; }));
    return m;
  }

  function fmtValue(catName, f, val) {
    if (f.field_type === 'duration') {
      const s = parseInt(val) || 0;
      const mm = Math.floor(s / 60), ss = s % 60;
      return mm + ':' + String(ss).padStart(2, '0');
    }
    return val + (f.unit ? ' ' + f.unit : '');
  }

  async function load() {
    const res = await fetch('/api/categories', { headers: { accept: 'application/json' } });
    categories = await res.json();
    const sel = document.getElementById('actCategory');
    if (sel) {
      sel.innerHTML = '<option value="">-- Category --</option>' +
        categories.map(c => `<option value="${c.id}">${c.name}</option>`).join('');
    }
  }

  async function loadActivities(catId) {
    if (!activitiesByCat[catId]) {
      const res = await fetch('/api/activities?category_id=' + catId, { headers: { accept: 'application/json' } });
      activitiesByCat[catId] = await res.json();
    }
    const dl = document.getElementById('actActivityList');
    dl.innerHTML = activitiesByCat[catId].map(a => `<option value="${a.name}">`).join('');
  }

  function renderFields(catId) {
    const cat = categories.find(c => c.id == catId);
    const wrap = document.getElementById('actFields');
    if (!cat) { wrap.innerHTML = ''; return; }
    wrap.innerHTML = '<div class="row g-1">' + cat.fields.map(f => `
      <div class="col">
        <input class="form-control form-control-sm act-field" data-key="${f.key}"
               data-type="${f.field_type}"
               placeholder="${f.label}${f.unit ? ' (' + f.unit + ')' : ''}${f.is_required ? ' *' : ''}">
      </div>`).join('') + '</div>';
  }

  function renderRows() {
    const ul = document.getElementById('actList');
    if (!rows.length) { ul.innerHTML = ''; return; }
    ul.innerHTML = rows.map((r, i) => {
      const parts = Object.entries(r.values).map(([k, v]) => `${k}: ${v}`).join(' · ');
      const note = r.notes ? ` <span class="text-muted">· ${r.notes}</span>` : '';
      return `<li class="list-group-item d-flex justify-content-between align-items-center py-1 small">
        <span><strong>${r.activity_name}</strong> <span class="text-muted">· ${r.category_name}</span> ${parts}${note}</span>
        <button type="button" class="btn btn-sm btn-link text-danger p-0" onclick="ActivitySection.remove(${i})">✕</button>
      </li>`;
    }).join('');
  }

  async function ensureActivity(catId, name) {
    const list = activitiesByCat[catId] || [];
    const found = list.find(a => a.name.toLowerCase() === name.toLowerCase());
    if (found) return found;
    const res = await fetch('/api/activities', {
      method: 'POST', headers: { 'Content-Type': 'application/json', accept: 'application/json' },
      body: JSON.stringify({ category_id: parseInt(catId), name })
    });
    if (!res.ok) { alert('Could not create activity'); return null; }
    const created = await res.json();
    activitiesByCat[catId] = list.concat([created]);
    return created;
  }

  async function add() {
    const catSel = document.getElementById('actCategory');
    const catId = catSel.value;
    const name = document.getElementById('actActivity').value.trim();
    if (!catId || !name) { alert('Pick a category and activity'); return; }
    const cat = categories.find(c => c.id == catId);

    const values = {};
    let missingRequired = null;
    document.querySelectorAll('#actFields .act-field').forEach(inp => {
      const v = inp.value.trim();
      const f = cat.fields.find(x => x.key === inp.dataset.key);
      if (v !== '') values[inp.dataset.key] = v;
      else if (f && f.is_required) missingRequired = f.label;
    });
    if (missingRequired) { alert(`"${missingRequired}" is required`); return; }

    const activity = await ensureActivity(catId, name);
    if (!activity) return;

    rows.push({
      activity_id: activity.id,
      activity_name: activity.name,
      category_name: cat.name,
      values,
      notes: document.getElementById('actNote').value.trim() || null,
    });
    renderRows();
    // reset inputs
    document.getElementById('actActivity').value = '';
    document.getElementById('actNote').value = '';
    document.querySelectorAll('#actFields .act-field').forEach(i => i.value = '');
  }

  function remove(i) { rows.splice(i, 1); renderRows(); }

  function collect() {
    return rows.map(r => ({
      id: r.id || undefined,
      activity_id: r.activity_id,
      values: r.values,
      notes: r.notes,
    }));
  }

  function reset() { rows = []; renderRows(); }

  function prefill(serverRows) {
    // serverRows from /history/sessions/ : {id, activity_id, activity_name, category_id, category_name, values, notes}
    rows = (serverRows || []).map(r => ({
      id: r.id,
      activity_id: r.activity_id,
      activity_name: r.activity_name,
      category_name: r.category_name,
      values: r.values || {},
      notes: r.notes || null,
    }));
    renderRows();
  }

  function init() {
    const catSel = document.getElementById('actCategory');
    if (!catSel) return;
    catSel.addEventListener('change', async (e) => {
      const id = e.target.value;
      renderFields(id);
      if (id) await loadActivities(id);
    });
    document.getElementById('actAddBtn').addEventListener('click', add);
  }

  return { load, init, add, remove, collect, reset, prefill, fieldMap, fmtValue };
})();
</script>
```

- [ ] **Step 2: Commit**

```bash
git add templates/_activity_section.html
git commit -m "feat(ui): shared activity logging partial"
```

(No automated test — verified via the manual checks in Task 13.)

---

## Task 11: Wire partial into the log-session modal

**Files:**
- Modify: `templates/index.html`

- [ ] **Step 1: Include the partial in the modal body**

In `templates/index.html`, inside the Log Session Modal body, after the `sessionPartnerGroup` div (closes at line 63) and before the `</div>` that closes `modal-body` (line 64), insert:

```html
          {% include "_activity_section.html" %}
```

- [ ] **Step 2: Initialize and send activities on create**

In the `<script>` block, in the `DOMContentLoaded` handler (line 356), add the activity-section setup after `await refreshSummary();`:

```javascript
      await ActivitySection.load();
      ActivitySection.init();
```

In the `confirmLogSession` click handler, change the payload assembly (around line 282) to include activities:

```javascript
      const payload = { duration_minutes: durationMinutes, trainer, num_people: numPeople };
      if (partnerEmail) {
        payload.partner_email = partnerEmail;
      }
      payload.activities = ActivitySection.collect();
```

And after a successful log (inside the `if (res.ok)` branch, after setting `notif.innerHTML`), reset the section:

```javascript
        ActivitySection.reset();
```

- [ ] **Step 3: Manual smoke check**

Run the app locally (see Task 13) and confirm the "Log activities (optional)" section appears collapsed in the Log Session modal, expands, lists categories, and adding a row works. Full verification is in Task 13.

- [ ] **Step 4: Commit**

```bash
git add templates/index.html
git commit -m "feat(ui): activity logging in log-session modal"
```

---

## Task 12: Wire partial into history (edit modal + display)

**Files:**
- Modify: `templates/history.html`

- [ ] **Step 1: Include the partial in the edit modal**

In `templates/history.html`, inside the Edit Session Modal body, after the trainer `<div class="mb-3">...</div>` (closes at line 56) and before the closing `</div>` of `modal-body` (line 57), insert:

```html
            {% include "_activity_section.html" %}
```

- [ ] **Step 2: Load categories on page init + keep a session map**

In the `window.addEventListener('DOMContentLoaded', ...)` handler (line 109), add after `loadTrainers();`:

```javascript
      ActivitySection.load().then(() => ActivitySection.init());
```

Add a module-level cache of the last loaded sessions. Near the top of the `<script>` (after `function pad...`), add:

```javascript
    let sessionsById = {};
```

In `loadHistory`, after `const sessions = await sRes.json();`, add:

```javascript
      sessionsById = {};
      sessions.forEach(s => { sessionsById[s.id] = s; });
```

- [ ] **Step 3: Render activities under each session in the list**

In `loadHistory`, inside `sessions.forEach(s => { ... })`, after the `<li ...>` template is built (replace the existing `html += \`<li ...>...</li>\`` block for sessions) so each session also shows its activities. Replace the session list-item template with:

```javascript
          let actHtml = '';
          if (s.activities && s.activities.length) {
            const byCat = {};
            s.activities.forEach(a => { (byCat[a.category_name] = byCat[a.category_name] || []).push(a); });
            actHtml = '<div class="ms-3 mt-1 w-100">' + Object.entries(byCat).map(([cat, items]) => {
              const lines = items.map(a => {
                const parts = Object.entries(a.values || {}).map(([k, v]) => `${k}: ${v}`).join(' · ');
                const note = a.notes ? ` <span class="text-muted">· ${a.notes}</span>` : '';
                return `<div class="small">• ${a.activity_name} <span class="text-muted">${parts}</span>${note}</div>`;
              }).join('');
              return `<div class="small text-uppercase text-muted">${cat}</div>${lines}`;
            }).join('') + '</div>';
          }

          html += `
            <li class="list-group-item small" id="session-card-${s.id}">
              <div class="d-flex justify-content-between align-items-center">
                <span class="flex-grow-1 text-wrap">
                  ${label} — ${s.duration_minutes} min with ${s.trainer}${partnerBadge}
                </span>
                <span class="d-flex gap-1">
                  ${canEdit ? `
                  <button class="btn btn-sm btn-outline-primary action-btn py-0 px-2"
                          data-bs-toggle="modal" data-bs-target="#editSessionModal"
                          data-id="${s.id}"
                          data-date="${dtLocal}"
                          data-duration="${s.duration_minutes}"
                          data-trainer="${s.trainer}">
                    Edit
                  </button>
                  <button class="btn btn-sm btn-outline-danger action-btn py-0 px-2"
                          onclick="confirmDelete('session', ${s.id})">Delete</button>
                  ` : ''}
                </span>
              </div>
              ${actHtml}
            </li>`;
```

- [ ] **Step 4: Prefill activities when opening the edit modal**

In the `editSessionModal` `show.bs.modal` handler (line 258), after `document.getElementById('sessionTrainer').value = btn.dataset.trainer;`, add:

```javascript
        const sess = sessionsById[btn.dataset.id];
        ActivitySection.prefill(sess ? sess.activities : []);
```

- [ ] **Step 5: Send activities on edit submit**

In `submitSessionEdit` (line 270), change the payload to include activities:

```javascript
      const payload = {
        session_date:     document.getElementById('sessionDate').value,
        duration_minutes: +document.getElementById('sessionDuration').value,
        trainer:          document.getElementById('sessionTrainer').value,
        activities:       ActivitySection.collect()
      };
```

- [ ] **Step 6: Commit**

```bash
git add templates/history.html
git commit -m "feat(ui): retroactive activity edit + display in history"
```

---

## Task 13: Admin page + nav link + manual verification

**Files:**
- Create: `templates/admin/activities.html`
- Modify: `templates/admin/index.html`

- [ ] **Step 1: Inspect the existing admin templates for styling**

Read `templates/admin/index.html` and `templates/admin/packages.html` to match their layout/markup conventions (nav include, container classes, how lists/forms are rendered). Build the new page in the same style.

- [ ] **Step 2: Create `templates/admin/activities.html`**

Create a page that, using the same Bootstrap/layout conventions as `admin/packages.html`:
- Lists categories (from the `categories` context var, each with `.fields`).
- For each category: shows its fields (label · type · unit · required) with a "Deactivate" button (`DELETE /api/admin/categories/{cat}/fields/{fid}`) and an "Add field" form (`POST /api/admin/categories/{cat}/fields` with `key,label,field_type,unit,is_required,sort_order`).
- An "Add category" form (`POST /api/admin/categories`).
- For each category, lists its activities (fetch `GET /api/activities?category_id=`) with a "Deactivate" button (`PATCH /api/admin/activities/{id}` body `{is_active:false}`) and rename (`PATCH` body `{name}`).
- All fetches send header `accept: application/json` and `Content-Type: application/json`; show a simple alert + reload on success, alert on failure. Reuse the exact fetch/alert idiom already in `admin/packages.html`.

Use this as the concrete skeleton (adapt classes to match `packages.html`):

```html
{% extends-or-include pattern from packages.html %}
<!-- Category list -->
{% for c in categories %}
  <h4>{{ c.name }}</h4>
  <ul>
    {% for f in c.fields %}
      <li>{{ f.label }} — {{ f.field_type }}{% if f.unit %} ({{ f.unit }}){% endif %}{% if f.is_required %} *required{% endif %}
        {% if f.is_active %}
        <button onclick="deleteField({{ c.id }}, {{ f.id }})">Deactivate</button>
        {% endif %}
      </li>
    {% endfor %}
  </ul>
  <!-- add-field form posting to /api/admin/categories/{{ c.id }}/fields -->
  <!-- activities list container populated by loadActivities({{ c.id }}) -->
{% endfor %}
<!-- add-category form posting to /api/admin/categories -->
```

With JS helpers:

```javascript
async function postJSON(url, body, method='POST') {
  const r = await fetch(url, { method, headers: {'Content-Type':'application/json', accept:'application/json'}, body: JSON.stringify(body) });
  if (!r.ok) { const e = await r.json(); alert(e.detail || 'Error'); return null; }
  return r.json();
}
async function deleteField(catId, fieldId) {
  if (!confirm('Deactivate this field? Existing logs keep their values.')) return;
  const r = await fetch(`/api/admin/categories/${catId}/fields/${fieldId}`, { method:'DELETE', headers:{accept:'application/json'} });
  if (r.ok) location.reload(); else alert('Failed');
}
// addCategory, addField, loadActivities(catId), deactivateActivity(id), renameActivity(id) follow the same postJSON idiom.
```

- [ ] **Step 3: Add a nav link in `templates/admin/index.html`**

Match the existing link markup for Trainers/Packages and add a link to `/admin/activities` labeled "Activities".

- [ ] **Step 4: Run the full test suite**

Run: `python -m pytest gym_tracker/tests/ -v`
Expected: all passed.

- [ ] **Step 5: Manual end-to-end verification**

Start the app:

```bash
uvicorn main:app --reload
```

Verify (as a logged-in user; if OAuth is not configured locally, set a session/user per the repo's dev login, or temporarily seed a user and session):
1. Log Session modal shows collapsed "Log activities (optional)"; expand → pick Strength → fields `Reps`, `Weight (lbs)` render; type "Bench Press" → add a row → log session.
2. History page shows the session with "STRENGTH • Bench Press reps: 8 …" beneath it.
3. Edit the session → activity rows are prefilled; add a Cardio activity, edit reps on the existing row, remove one → Save → reload shows the reconciled set.
4. Delete the session → its activities disappear (no orphan error).
5. As admin: `/admin/activities` → add a category, add a field, deactivate a field (old logs still render their stored value), deactivate an activity (it stops appearing in the picker).
6. As a second user: the activity created in step 1 is selectable (global library).

- [ ] **Step 6: Commit**

```bash
git add templates/admin/activities.html templates/admin/index.html
git commit -m "feat(ui): admin activity/category/field management page"
```

---

## Self-Review Notes (for the implementer)

- **Spec coverage:** tables (Task 1–2), structured per-category metrics + validation (Task 4), admin-managed categories/fields (Task 5, 8, 13), one-row-per-activity (model + reconcile), global activities with dedup (Task 5), log-at-create (Task 7, 11), retroactive add/edit/remove (Task 6, 8, 12), cascade on delete (Task 1 relationship), history display (Task 12). All spec sections map to a task.
- **Type consistency:** `reconcile_session_activities(db, session, items, *, created_by_user_id=None)` used identically in `crud.create_session` and the edit endpoint. `validate_activity_values(db, activity, values)` used in reconcile. `SessionActivityInput`/`SessionActivityRead` names match between schemas, crud annotation, and JS payloads. `ActivitySection.collect()` emits `{id?, activity_id, values, notes}` exactly matching `SessionActivityInput`.
- **Field `key` immutability:** `CategoryFieldUpdate` intentionally omits `key` so stored JSON values never orphan; deletes are soft (`is_active=False`).
