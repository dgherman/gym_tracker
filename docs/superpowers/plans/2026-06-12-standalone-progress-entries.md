# Standalone Progress Entries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users retroactively record activity progress without attaching it to a training session, merged seamlessly into existing Progress charts/stats.

**Architecture:** New `progress_entries` table holds dated standalone entries per user. A new `gym_tracker/progress_entries.py` module owns CRUD + permission checks (self always; `admin`/`trainer` roles may act for other users). `crud.user_activity_rows` unions standalone rows into the row dicts consumed by `progress.summarize` (which stays untouched). REST endpoints in `main.py` follow existing patterns. UI lives on the Reports → Progress tab, reusing the `_activity_section.html` partial.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Pydantic v2, pytest + TestClient, Bootstrap 5 + vanilla JS (Jinja templates).

**Spec:** `docs/superpowers/specs/2026-06-12-standalone-progress-entries-design.md`

**Working dir:** repo root (`gym_tracker` project). **Test command:** `.venv/bin/python -m pytest` (71 tests pass at baseline).

**Spec deviation (intentional):** `entry_date` is a `DateTime` column (stored at midnight), not `Date`. Reason: standalone rows merge with session rows whose `session_date` is `DateTime`; Python raises `TypeError` when sorting `datetime.date` against `datetime.datetime` inside `progress.summarize`. The API still accepts plain `YYYY-MM-DD` strings (Pydantic parses them to midnight datetimes).

---

## File map

| File | Action | Responsibility |
|---|---|---|
| `gym_tracker/models.py` | modify | add `ProgressEntry` ORM model |
| `alembic/versions/pe01standalone_add_progress_entries.py` | create | migration for the new table |
| `gym_tracker/schemas.py` | modify | add `ProgressEntryCreate/Update/Read` |
| `gym_tracker/progress_entries.py` | create | CRUD + permission logic (single responsibility, keeps `crud.py` from growing) |
| `gym_tracker/crud.py` | modify | union standalone rows in `user_activity_rows` |
| `main.py` | modify | REST endpoints + `GET /api/users` |
| `templates/reports.html` | modify | Add-progress panel on Progress tab |
| `gym_tracker/tests/test_progress_entries.py` | create | all new tests |
| `README.md` | modify | changelog entry |

---

### Task 1: ProgressEntry model + migration

**Files:**
- Modify: `gym_tracker/models.py` (append after `SessionActivity`, line 209)
- Create: `alembic/versions/pe01standalone_add_progress_entries.py`

- [ ] **Step 1: Add the ORM model**

Append to `gym_tracker/models.py` after the `SessionActivity` class:

```python
class ProgressEntry(Base):
    """Standalone progress entry — activity values recorded for a date without a session.
    Never consumes package sessions. user_id = whose progress; created_by_user_id = who
    typed it (differs when an admin/trainer logs on someone's behalf)."""
    __tablename__ = "progress_entries"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    activity_id = Column(Integer, ForeignKey("activities.id"), nullable=False)
    # DateTime (midnight), not Date: must sort against Session.session_date in progress rows
    entry_date = Column(DateTime, nullable=False, index=True)
    values = Column(JSON, nullable=False, default=dict)
    notes = Column(Text, nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    user = relationship("User", foreign_keys=[user_id])
    activity = relationship("Activity")
```

- [ ] **Step 2: Create the migration**

Create `alembic/versions/pe01standalone_add_progress_entries.py` (current head is `4093faf32ea1`):

```python
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
```

- [ ] **Step 3: Verify model imports and existing tests still pass**

Run: `.venv/bin/python -c "from gym_tracker import models; print(models.ProgressEntry.__tablename__)"`
Expected: `progress_entries`

Run: `.venv/bin/python -m pytest -q`
Expected: 71 passed (no regressions; tests use `Base.metadata.create_all`, so the new table is created automatically in the test DB — no migration run needed for tests)

- [ ] **Step 4: Commit**

```bash
git add gym_tracker/models.py alembic/versions/pe01standalone_add_progress_entries.py
git commit -m "feat: ProgressEntry model + migration for standalone progress entries"
```

---

### Task 2: Pydantic schemas

**Files:**
- Modify: `gym_tracker/schemas.py` (append before the `model_rebuild()` calls at line 363)

- [ ] **Step 1: Add schemas**

Append to `gym_tracker/schemas.py` just above the `SessionCreate.model_rebuild()` line:

```python
# --------------------
# Standalone Progress Entry Schemas
# --------------------

class ProgressEntryCreate(BaseModel):
    activity_id: int
    entry_date: datetime  # accepts "YYYY-MM-DD"; parsed to midnight
    values: dict = {}
    notes: str | None = None
    user_id: int | None = None  # None = current user; others require admin/trainer role


class ProgressEntryUpdate(BaseModel):
    activity_id: int | None = None
    entry_date: datetime | None = None
    values: dict | None = None
    notes: str | None = None


class ProgressEntryRead(BaseModel):
    id: int
    user_id: int
    activity_id: int
    activity_name: str
    category_id: int
    category_name: str
    entry_date: datetime
    values: dict = {}
    notes: str | None = None
    created_at: datetime
    model_config = {"from_attributes": True}
```

`activity_name`/`category_id`/`category_name` are denormalized for the UI list and
for `ActivitySection.prefill()` (which needs names, not ids). The CRUD layer
serializes entries to dicts carrying these — see Task 3.

- [ ] **Step 2: Verify import**

Run: `.venv/bin/python -c "from gym_tracker import schemas; print(schemas.ProgressEntryCreate(activity_id=1, entry_date='2026-06-10').entry_date)"`
Expected: `2026-06-10 00:00:00`

- [ ] **Step 3: Commit**

```bash
git add gym_tracker/schemas.py
git commit -m "feat: progress entry schemas"
```

---

### Task 3: CRUD + permissions module (TDD)

**Files:**
- Create: `gym_tracker/progress_entries.py`
- Create: `gym_tracker/tests/test_progress_entries.py`

Permission rule (from spec): acting on your own entries is always allowed; targeting
another `user_id` (create/list/update/delete) is allowed only when the actor's
`User.role` is `"admin"` or `"trainer"`, otherwise a `PermissionError` is raised
(the API layer maps it to 403).

Values are validated with the existing `activities.validate_activity_values`
(same rules as session activities: required active fields, type coercion;
raises `ValueError`).

- [ ] **Step 1: Write failing CRUD/permission tests**

Create `gym_tracker/tests/test_progress_entries.py`:

```python
from datetime import datetime

import pytest

from gym_tracker import models, progress_entries, schemas
from gym_tracker.tests.db_test_utils import TestSessionLocal


def _get(db, model, id_):
    return db.get(model, id_)


# ---------------------------------------------------------------
# Unit tests: progress_entries module (use the couples fixture DB:
# owner/partner/outsider users all role=client; one Strength category
# with required integer field "reps"; one activity "Bench Press")
# ---------------------------------------------------------------

def test_create_own_entry(couples):
    db = TestSessionLocal()
    owner = _get(db, models.User, couples._ids["owner"])
    e = progress_entries.create_entry(
        db,
        actor=owner,
        data=schemas.ProgressEntryCreate(
            activity_id=couples._ids["act"],
            entry_date=datetime(2026, 5, 10),
            values={"reps": "12"},
            notes="home gym",
        ),
    )
    assert e["user_id"] == owner.id
    assert e["activity_name"] == "Bench Press"
    assert e["category_name"] == "Strength"
    assert e["values"] == {"reps": 12}  # coerced by validate_activity_values
    row = db.get(models.ProgressEntry, e["id"])
    assert row.created_by_user_id == owner.id
    db.close()


def test_client_cannot_create_for_other_user(couples):
    db = TestSessionLocal()
    owner = _get(db, models.User, couples._ids["owner"])
    with pytest.raises(PermissionError):
        progress_entries.create_entry(
            db,
            actor=owner,
            data=schemas.ProgressEntryCreate(
                activity_id=couples._ids["act"],
                entry_date=datetime(2026, 5, 10),
                values={"reps": "5"},
                user_id=couples._ids["partner"],
            ),
        )
    db.close()


@pytest.mark.parametrize("role", ["admin", "trainer"])
def test_privileged_roles_create_for_other_user(couples, role):
    db = TestSessionLocal()
    actor = _get(db, models.User, couples._ids["outsider"])
    actor.role = role
    db.commit()
    e = progress_entries.create_entry(
        db,
        actor=actor,
        data=schemas.ProgressEntryCreate(
            activity_id=couples._ids["act"],
            entry_date=datetime(2026, 5, 11),
            values={"reps": "8"},
            user_id=couples._ids["owner"],
        ),
    )
    assert e["user_id"] == couples._ids["owner"]
    row = db.get(models.ProgressEntry, e["id"])
    assert row.created_by_user_id == actor.id
    db.close()


def test_create_validates_required_values(couples):
    db = TestSessionLocal()
    owner = _get(db, models.User, couples._ids["owner"])
    with pytest.raises(ValueError):
        progress_entries.create_entry(
            db,
            actor=owner,
            data=schemas.ProgressEntryCreate(
                activity_id=couples._ids["act"],
                entry_date=datetime(2026, 5, 10),
                values={},  # "reps" is required
            ),
        )
    db.close()


def test_create_unknown_activity_raises(couples):
    db = TestSessionLocal()
    owner = _get(db, models.User, couples._ids["owner"])
    with pytest.raises(LookupError):
        progress_entries.create_entry(
            db,
            actor=owner,
            data=schemas.ProgressEntryCreate(
                activity_id=99999,
                entry_date=datetime(2026, 5, 10),
                values={"reps": "5"},
            ),
        )
    db.close()


def _seed_entry(db, *, user_id, activity_id, date, reps, creator_id=None):
    pe = models.ProgressEntry(
        user_id=user_id,
        activity_id=activity_id,
        entry_date=date,
        values={"reps": reps},
        created_by_user_id=creator_id or user_id,
    )
    db.add(pe)
    db.commit()
    db.refresh(pe)
    return pe


def test_list_own_entries_newest_first(couples):
    db = TestSessionLocal()
    owner = _get(db, models.User, couples._ids["owner"])
    act = couples._ids["act"]
    _seed_entry(db, user_id=owner.id, activity_id=act, date=datetime(2026, 5, 1), reps=5)
    _seed_entry(db, user_id=owner.id, activity_id=act, date=datetime(2026, 5, 9), reps=6)
    _seed_entry(db, user_id=couples._ids["partner"], activity_id=act, date=datetime(2026, 5, 5), reps=7)
    out = progress_entries.list_entries(db, actor=owner, user_id=owner.id)
    assert [e["values"]["reps"] for e in out] == [6, 5]  # partner's excluded, newest first
    db.close()


def test_client_cannot_list_other_user(couples):
    db = TestSessionLocal()
    owner = _get(db, models.User, couples._ids["owner"])
    with pytest.raises(PermissionError):
        progress_entries.list_entries(db, actor=owner, user_id=couples._ids["partner"])
    db.close()


def test_update_own_entry(couples):
    db = TestSessionLocal()
    owner = _get(db, models.User, couples._ids["owner"])
    pe = _seed_entry(db, user_id=owner.id, activity_id=couples._ids["act"],
                     date=datetime(2026, 5, 1), reps=5)
    out = progress_entries.update_entry(
        db, actor=owner, entry_id=pe.id,
        data=schemas.ProgressEntryUpdate(values={"reps": "10"}, entry_date=datetime(2026, 5, 2)),
    )
    assert out["values"] == {"reps": 10}
    assert out["entry_date"] == datetime(2026, 5, 2)
    db.close()


def test_client_cannot_update_or_delete_other_users_entry(couples):
    db = TestSessionLocal()
    owner = _get(db, models.User, couples._ids["owner"])
    partner_entry = _seed_entry(db, user_id=couples._ids["partner"],
                                activity_id=couples._ids["act"],
                                date=datetime(2026, 5, 1), reps=5)
    with pytest.raises(PermissionError):
        progress_entries.update_entry(db, actor=owner, entry_id=partner_entry.id,
                                      data=schemas.ProgressEntryUpdate(notes="x"))
    with pytest.raises(PermissionError):
        progress_entries.delete_entry(db, actor=owner, entry_id=partner_entry.id)
    db.close()


def test_delete_own_entry(couples):
    db = TestSessionLocal()
    owner = _get(db, models.User, couples._ids["owner"])
    pe = _seed_entry(db, user_id=owner.id, activity_id=couples._ids["act"],
                     date=datetime(2026, 5, 1), reps=5)
    progress_entries.delete_entry(db, actor=owner, entry_id=pe.id)
    assert db.get(models.ProgressEntry, pe.id) is None
    db.close()


def test_update_missing_entry_raises_lookup(couples):
    db = TestSessionLocal()
    owner = _get(db, models.User, couples._ids["owner"])
    with pytest.raises(LookupError):
        progress_entries.update_entry(db, actor=owner, entry_id=99999,
                                      data=schemas.ProgressEntryUpdate(notes="x"))
    db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest gym_tracker/tests/test_progress_entries.py -q`
Expected: collection error / failures with `ModuleNotFoundError: No module named 'gym_tracker.progress_entries'` (or `AttributeError` once the module exists but functions don't)

- [ ] **Step 3: Implement the module**

Create `gym_tracker/progress_entries.py`:

```python
"""Standalone progress entries: CRUD + permission checks.

Entries record activity values for a date without a session and never touch
package/session accounting. Permission rule: a user always acts on their own
entries; acting on another user's requires role admin or trainer.

Raises: PermissionError (403 at API), LookupError (404), ValueError (400).
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import models, schemas
from .activities import validate_activity_values

PRIVILEGED_ROLES = ("admin", "trainer")


def _check_can_act_for(actor: models.User, target_user_id: int) -> None:
    if actor.id == target_user_id:
        return
    if actor.role in PRIVILEGED_ROLES:
        return
    raise PermissionError("Not allowed to act for another user")


def _serialize(db: Session, pe: models.ProgressEntry) -> dict:
    activity = pe.activity
    category = db.get(models.ActivityCategory, activity.category_id) if activity else None
    return {
        "id": pe.id,
        "user_id": pe.user_id,
        "activity_id": pe.activity_id,
        "activity_name": activity.name if activity else "(unknown)",
        "category_id": category.id if category else 0,
        "category_name": category.name if category else "(unknown)",
        "entry_date": pe.entry_date,
        "values": pe.values or {},
        "notes": pe.notes,
        "created_at": pe.created_at,
    }


def create_entry(db: Session, *, actor: models.User, data: schemas.ProgressEntryCreate) -> dict:
    target_user_id = data.user_id if data.user_id is not None else actor.id
    _check_can_act_for(actor, target_user_id)
    activity = db.get(models.Activity, data.activity_id)
    if not activity:
        raise LookupError("Activity not found")
    cleaned = validate_activity_values(db, activity, data.values)
    pe = models.ProgressEntry(
        user_id=target_user_id,
        activity_id=activity.id,
        entry_date=data.entry_date,
        values=cleaned,
        notes=(data.notes or None),
        created_by_user_id=actor.id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(pe)
    db.commit()
    db.refresh(pe)
    return _serialize(db, pe)


def list_entries(db: Session, *, actor: models.User, user_id: int, limit: int = 100) -> list[dict]:
    _check_can_act_for(actor, user_id)
    q = (
        db.query(models.ProgressEntry)
        .filter(models.ProgressEntry.user_id == user_id)
        .order_by(models.ProgressEntry.entry_date.desc(), models.ProgressEntry.id.desc())
        .limit(limit)
    )
    return [_serialize(db, pe) for pe in q.all()]


def _get_owned(db: Session, *, actor: models.User, entry_id: int) -> models.ProgressEntry:
    pe = db.get(models.ProgressEntry, entry_id)
    if not pe:
        raise LookupError("Entry not found")
    _check_can_act_for(actor, pe.user_id)
    return pe


def update_entry(db: Session, *, actor: models.User, entry_id: int,
                 data: schemas.ProgressEntryUpdate) -> dict:
    pe = _get_owned(db, actor=actor, entry_id=entry_id)
    patch = data.model_dump(exclude_unset=True)
    if "activity_id" in patch:
        activity = db.get(models.Activity, patch["activity_id"])
        if not activity:
            raise LookupError("Activity not found")
        pe.activity_id = activity.id
    if "values" in patch:
        activity = db.get(models.Activity, pe.activity_id)
        pe.values = validate_activity_values(db, activity, patch["values"])
    if "entry_date" in patch:
        pe.entry_date = patch["entry_date"]
    if "notes" in patch:
        pe.notes = patch["notes"] or None
    db.commit()
    db.refresh(pe)
    return _serialize(db, pe)


def delete_entry(db: Session, *, actor: models.User, entry_id: int) -> None:
    pe = _get_owned(db, actor=actor, entry_id=entry_id)
    db.delete(pe)
    db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest gym_tracker/tests/test_progress_entries.py -q`
Expected: 12 passed

Run: `.venv/bin/python -m pytest -q`
Expected: 83 passed (71 baseline + 12 new)

- [ ] **Step 5: Commit**

```bash
git add gym_tracker/progress_entries.py gym_tracker/tests/test_progress_entries.py
git commit -m "feat: standalone progress entry CRUD with role-gated permissions"
```

---

### Task 4: Merge standalone rows into progress aggregation (TDD)

**Files:**
- Modify: `gym_tracker/crud.py:471-513` (`user_activity_rows`)
- Modify: `gym_tracker/tests/test_progress_entries.py` (append tests)

`progress.py` is NOT modified — it already consumes row dicts. `row_id` for
standalone rows is the string `f"p{entry.id}"`; session rows keep integer
`sa.id`, so the namespaces cannot collide.

- [ ] **Step 1: Write failing merge tests**

Append to `gym_tracker/tests/test_progress_entries.py`:

```python
# ---------------------------------------------------------------
# user_activity_rows merge
# ---------------------------------------------------------------

from gym_tracker import crud


def test_user_activity_rows_includes_standalone_entries(couples):
    db = TestSessionLocal()
    owner_id = couples._ids["owner"]
    act = couples._ids["act"]
    pe = _seed_entry(db, user_id=owner_id, activity_id=act,
                     date=datetime(2026, 5, 10), reps=12)
    rows = crud.user_activity_rows(
        db, user_id=owner_id,
        start=datetime(2026, 5, 1), end=datetime(2026, 5, 31),
    )
    standalone = [r for r in rows if r["row_id"] == f"p{pe.id}"]
    assert len(standalone) == 1
    r = standalone[0]
    assert r["session_date"] == datetime(2026, 5, 10)
    assert r["activity_name"] == "Bench Press"
    assert r["category_slug"] == "strength"
    assert r["values"] == {"reps": 12}
    db.close()


def test_user_activity_rows_excludes_other_users_and_out_of_range(couples):
    db = TestSessionLocal()
    owner_id = couples._ids["owner"]
    act = couples._ids["act"]
    _seed_entry(db, user_id=couples._ids["partner"], activity_id=act,
                date=datetime(2026, 5, 10), reps=1)          # other user
    _seed_entry(db, user_id=owner_id, activity_id=act,
                date=datetime(2026, 7, 1), reps=2)            # out of range
    inside = _seed_entry(db, user_id=owner_id, activity_id=act,
                         date=datetime(2026, 5, 15), reps=3)  # in range
    rows = crud.user_activity_rows(
        db, user_id=owner_id,
        start=datetime(2026, 5, 1), end=datetime(2026, 5, 31),
    )
    standalone_ids = [r["row_id"] for r in rows if str(r["row_id"]).startswith("p")]
    assert standalone_ids == [f"p{inside.id}"]
    db.close()


def test_standalone_rows_flow_through_summarize(couples):
    from gym_tracker import progress as progress_mod
    db = TestSessionLocal()
    owner_id = couples._ids["owner"]
    act = couples._ids["act"]
    _seed_entry(db, user_id=owner_id, activity_id=act,
                date=datetime(2026, 5, 10), reps=12)
    rows = crud.user_activity_rows(
        db, user_id=owner_id,
        start=datetime(2026, 5, 1), end=datetime(2026, 5, 31),
    )
    fields = (
        db.query(models.CategoryField)
        .filter(models.CategoryField.category_id == couples._ids["cat"])
        .all()
    )
    out = progress_mod.summarize(rows, {couples._ids["cat"]: fields})
    assert any(s["activity"] == "Bench Press" for s in out["summary"])
    db.close()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest gym_tracker/tests/test_progress_entries.py -q -k "user_activity_rows or summarize"`
Expected: 3 failures (standalone rows absent from output)

- [ ] **Step 3: Implement the union**

In `gym_tracker/crud.py`, inside `user_activity_rows` (line 471), insert before the
final `return rows` (line 513), at function-body indent level:

```python
    # Standalone progress entries (no session) merge into the same row stream.
    # row_id is "p<id>" — string namespace can't collide with integer sa.id.
    entries = (
        db.query(models.ProgressEntry)
        .filter(
            models.ProgressEntry.user_id == user_id,
            models.ProgressEntry.entry_date >= start,
            models.ProgressEntry.entry_date <= end,
        )
        .all()
    )
    for pe in entries:
        activity = pe.activity
        if not activity:
            continue
        cid = activity.category_id
        if cid not in cat_cache:
            cat_cache[cid] = db.get(models.ActivityCategory, cid)
        category = cat_cache[cid]
        rows.append({
            "session_date": pe.entry_date,
            "row_id": f"p{pe.id}",
            "activity_id": activity.id,
            "activity_name": activity.name,
            "category_id": category.id if category else 0,
            "category_slug": category.slug if category else "",
            "category_name": category.name if category else "(unknown)",
            "values": pe.values or {},
        })
```

Also update the docstring's first line to mention standalone entries:

```python
    """Activity rows attributable to user_id within [start, end], including
    standalone progress entries (no session).
    Solo sessions (num_people<=1): all rows (null slot). Couples: rows whose
    person_slot equals the user's slot. Couples null rows are excluded.
    Returns dicts consumed by gym_tracker.progress.summarize."""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: 86 passed (83 + 3 new), zero regressions

- [ ] **Step 5: Commit**

```bash
git add gym_tracker/crud.py gym_tracker/tests/test_progress_entries.py
git commit -m "feat: merge standalone progress entries into user_activity_rows"
```

---

### Task 5: REST API endpoints (TDD)

**Files:**
- Modify: `main.py` (new section after the Activity Tracking endpoints; also import `progress_entries`)
- Modify: `gym_tracker/tests/test_progress_entries.py` (append integration tests)

Auth follows the existing manual pattern (`request.session.get("user_id")` →
401), not `require_admin` (these endpoints are open to any logged-in user;
role only gates cross-user targeting). Integration tests use the
`_login` pattern from `test_session_edit_auth.py`: set `DEV_LOGIN_EMAIL`,
GET `/dev/login` (it logs in existing users by email without changing their role).

`GET /api/users` exists solely so the admin/trainer UI can pick a target user;
it is gated to those roles.

- [ ] **Step 1: Write failing API tests**

Append to `gym_tracker/tests/test_progress_entries.py`:

```python
# ---------------------------------------------------------------
# API integration tests (session-cookie auth via /dev/login)
# ---------------------------------------------------------------

import os


def _login(c, email):
    os.environ["DEV_LOGIN_EMAIL"] = email
    r = c.get("/dev/login", follow_redirects=False)
    assert r.status_code in (302, 307)


def test_api_create_list_update_delete_own(couples):
    c = couples
    _login(c, "owner@x.com")
    r = c.post("/api/progress-entries", json={
        "activity_id": c._ids["act"],
        "entry_date": "2026-05-10",
        "values": {"reps": "12"},
        "notes": "home gym",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["activity_name"] == "Bench Press"
    assert body["values"] == {"reps": 12}
    entry_id = body["id"]

    r = c.get("/api/progress-entries")
    assert r.status_code == 200
    assert [e["id"] for e in r.json()] == [entry_id]

    r = c.put(f"/api/progress-entries/{entry_id}", json={"values": {"reps": "15"}})
    assert r.status_code == 200
    assert r.json()["values"] == {"reps": 15}

    r = c.delete(f"/api/progress-entries/{entry_id}")
    assert r.status_code == 200
    assert c.get("/api/progress-entries").json() == []


def test_api_unauthenticated_401(couples):
    r = couples.post("/api/progress-entries", json={
        "activity_id": couples._ids["act"],
        "entry_date": "2026-05-10",
        "values": {"reps": "5"},
    }, headers={"accept": "application/json"})
    assert r.status_code == 401


def test_api_client_targeting_other_user_403(couples):
    c = couples
    _login(c, "owner@x.com")
    r = c.post("/api/progress-entries", json={
        "activity_id": c._ids["act"],
        "entry_date": "2026-05-10",
        "values": {"reps": "5"},
        "user_id": c._ids["partner"],
    })
    assert r.status_code == 403
    r = c.get(f"/api/progress-entries?user_id={c._ids['partner']}")
    assert r.status_code == 403


def test_api_trainer_can_target_other_user(couples):
    c = couples
    db = TestSessionLocal()
    u = db.get(models.User, c._ids["outsider"])
    u.role = "trainer"
    db.commit(); db.close()

    _login(c, "out@x.com")
    r = c.post("/api/progress-entries", json={
        "activity_id": c._ids["act"],
        "entry_date": "2026-05-10",
        "values": {"reps": "8"},
        "user_id": c._ids["owner"],
    })
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == c._ids["owner"]

    r = c.get(f"/api/progress-entries?user_id={c._ids['owner']}")
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_api_validation_400_and_missing_404(couples):
    c = couples
    _login(c, "owner@x.com")
    r = c.post("/api/progress-entries", json={
        "activity_id": c._ids["act"],
        "entry_date": "2026-05-10",
        "values": {},  # required "reps" missing
    })
    assert r.status_code == 400
    r = c.put("/api/progress-entries/99999", json={"notes": "x"})
    assert r.status_code == 404
    r = c.delete("/api/progress-entries/99999")
    assert r.status_code == 404


def test_api_users_list_role_gated(couples):
    c = couples
    _login(c, "owner@x.com")  # role=client
    assert c.get("/api/users", headers={"accept": "application/json"}).status_code == 403

    db = TestSessionLocal()
    u = db.get(models.User, c._ids["outsider"])
    u.role = "trainer"
    db.commit(); db.close()
    _login(c, "out@x.com")
    r = c.get("/api/users", headers={"accept": "application/json"})
    assert r.status_code == 200
    emails = {u["email"] for u in r.json()}
    assert {"owner@x.com", "partner@x.com", "out@x.com"} <= emails
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest gym_tracker/tests/test_progress_entries.py -q -k api`
Expected: failures with 404 (routes don't exist yet)

- [ ] **Step 3: Implement endpoints**

In `main.py`:

a) Extend the import at line 16:

```python
from gym_tracker import crud, models, progress, progress_entries, schemas
```

b) Add a new section after the Activity Tracking API endpoints (before the
`/admin` page routes around line 584):

```python
# -------------------------------------------------------------
# Standalone Progress Entry API endpoints
# -------------------------------------------------------------

def _require_user(request: Request, db: Session) -> models.User:
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    return user


def _run_entry_op(fn):
    """Map progress_entries exceptions to HTTP errors."""
    try:
        return fn()
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/progress-entries", response_model=schemas.ProgressEntryRead)
def create_progress_entry(
    entry_in: schemas.ProgressEntryCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    return _run_entry_op(lambda: progress_entries.create_entry(db, actor=user, data=entry_in))


@app.get("/api/progress-entries", response_model=List[schemas.ProgressEntryRead])
def list_progress_entries(
    request: Request,
    user_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    target = user_id if user_id is not None else user.id
    return _run_entry_op(lambda: progress_entries.list_entries(db, actor=user, user_id=target))


@app.put("/api/progress-entries/{entry_id}", response_model=schemas.ProgressEntryRead)
def update_progress_entry(
    entry_id: int,
    entry_update: schemas.ProgressEntryUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    return _run_entry_op(lambda: progress_entries.update_entry(
        db, actor=user, entry_id=entry_id, data=entry_update))


@app.delete("/api/progress-entries/{entry_id}")
def delete_progress_entry(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    _run_entry_op(lambda: progress_entries.delete_entry(db, actor=user, entry_id=entry_id))
    return {"ok": True}


@app.get("/api/users")
def list_users(request: Request, db: Session = Depends(get_db)):
    """Minimal user list for the admin/trainer 'log for someone' selector."""
    user = _require_user(request, db)
    if user.role not in progress_entries.PRIVILEGED_ROLES:
        raise HTTPException(status_code=403, detail="Admin or trainer access required")
    users = (
        db.query(models.User)
        .filter(models.User.is_active == True)
        .order_by(models.User.full_name, models.User.email)
        .all()
    )
    return [{"id": u.id, "name": u.full_name or u.email, "email": u.email} for u in users]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: 92 passed (86 + 6 new), zero regressions

- [ ] **Step 5: Commit**

```bash
git add main.py gym_tracker/tests/test_progress_entries.py
git commit -m "feat: REST endpoints for standalone progress entries + role-gated user list"
```

---

### Task 6: Reports → Progress tab UI

**Files:**
- Modify: `templates/reports.html`

No automated tests (template-only); manual verification step below. Notes:

- `_activity_section.html` is include-once-per-page (hardcoded ids, global
  `ActivitySection`); reports.html doesn't include it yet, so this is safe.
- Both files define a global `function escapeHtml` — identical bodies; the later
  declaration silently wins. Harmless, leave as is.
- `ActivitySection.setPeople({numPeople: 1})` keeps the single "+ Add to session"
  button and hides Person B (standalone entries are single-person by spec).
- Delete buttons in the entries list are permanently visible (standing UI rule —
  no hover-only actions).
- `reports_page` already passes `current_user`, so Jinja can role-gate the
  user selector.

- [ ] **Step 1: Add the panel markup**

In `templates/reports.html`, inside the Progress tab pane (`<div class="tab-pane fade" id="tab-progress" role="tabpanel">`, line 85), insert immediately after that opening div (before `<div id="progressEmpty" ...>`):

```html
      <!-- Standalone progress entry (no session) -->
      <div class="border rounded mb-3">
        <button type="button" class="btn btn-link text-decoration-none w-100 text-start fw-bold"
                data-bs-toggle="collapse" data-bs-target="#addProgressBody">
          + Add progress <span class="text-muted fw-normal">(without a session)</span>
        </button>
        <div class="collapse" id="addProgressBody">
          <div class="p-2">
            <div class="row g-2 mb-2">
              <div class="col-auto">
                <label for="peDate" class="form-label mb-0 small">Date</label>
                <input type="date" id="peDate" class="form-control form-control-sm">
              </div>
              {% if current_user and current_user.role in ("admin", "trainer") %}
              <div class="col-auto">
                <label for="peUser" class="form-label mb-0 small">For user</label>
                <select id="peUser" class="form-select form-select-sm"></select>
              </div>
              {% endif %}
            </div>
            {% include "_activity_section.html" %}
            <div class="d-flex align-items-center gap-2 mt-2">
              <button type="button" id="peSaveBtn" class="btn btn-sm btn-success">Save progress</button>
              <span id="peStatus" class="small text-muted"></span>
            </div>
            <div id="peEntries" class="mt-3"></div>
          </div>
        </div>
      </div>
```

- [ ] **Step 2: Add the JS**

In the `<script>` block of `reports.html`, add inside the existing
`DOMContentLoaded` listener (after `RangeControl.init(...)`, line 125):

```javascript
      initProgressEntryPanel();
```

Then add these functions at the end of the script block (top level, alongside
`loadProgress`):

```javascript
    // ---- Standalone progress entries ----
    let peEditingId = null;  // entry id being edited, or null

    function peTargetUserId() {
      const sel = document.getElementById('peUser');
      return sel && sel.value ? parseInt(sel.value) : null;
    }

    async function initProgressEntryPanel() {
      const dateInput = document.getElementById('peDate');
      dateInput.value = new Date().toISOString().slice(0, 10);
      dateInput.max = dateInput.value;  // future dates make no sense for "retroactive"

      await ActivitySection.load();
      ActivitySection.init();
      ActivitySection.setPeople({ numPeople: 1 });

      const userSel = document.getElementById('peUser');
      if (userSel) {
        const res = await fetch('/api/users', { headers: { accept: 'application/json' } });
        if (res.ok) {
          const users = await res.json();
          userSel.innerHTML = users.map(u =>
            `<option value="${u.id}">${escapeHtml(u.name)}</option>`).join('');
        }
        userSel.addEventListener('change', loadEntriesList);
      }

      document.getElementById('peSaveBtn').addEventListener('click', saveProgressEntries);
      loadEntriesList();
    }

    async function saveProgressEntries() {
      const status = document.getElementById('peStatus');
      const date = document.getElementById('peDate').value;
      if (!date) { alert('Pick a date'); return; }
      const rows = ActivitySection.collect();
      if (!rows.length) { alert('Add at least one activity'); return; }

      const userId = peTargetUserId();
      status.textContent = 'Saving…';
      try {
        if (peEditingId !== null) {
          const r = rows[0];
          const res = await fetch(`/api/progress-entries/${peEditingId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json', accept: 'application/json' },
            body: JSON.stringify({ activity_id: r.activity_id, entry_date: date,
                                   values: r.values, notes: r.notes }),
          });
          if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
          peEditingId = null;
        } else {
          for (const r of rows) {
            const body = { activity_id: r.activity_id, entry_date: date,
                           values: r.values, notes: r.notes };
            if (userId !== null) body.user_id = userId;
            const res = await fetch('/api/progress-entries', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', accept: 'application/json' },
              body: JSON.stringify(body),
            });
            if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
          }
        }
        ActivitySection.reset();
        status.textContent = 'Saved.';
        setTimeout(() => { status.textContent = ''; }, 2000);
        loadEntriesList();
        loadProgress(RangeControl.getRange());
      } catch (e) {
        status.textContent = '';
        alert('Save failed: ' + e.message);
      }
    }

    async function loadEntriesList() {
      const wrap = document.getElementById('peEntries');
      if (!wrap) return;
      const userId = peTargetUserId();
      const url = '/api/progress-entries' + (userId !== null ? `?user_id=${userId}` : '');
      const res = await fetch(url, { headers: { accept: 'application/json' } });
      if (!res.ok) { wrap.innerHTML = ''; return; }
      const entries = await res.json();
      if (!entries.length) { wrap.innerHTML = ''; return; }
      const fmap = ActivitySection.fieldMap();
      wrap.innerHTML = '<div class="fw-bold small">Recorded entries</div>' +
        '<ul class="list-group">' + entries.map(e => {
          const parts = Object.entries(e.values).map(([k, v]) => {
            const f = fmap[e.category_id + ':' + k];
            const label = f ? f.label : k;
            const disp = f ? ActivitySection.fmtValue(e.category_name, f, v) : v;
            return `${escapeHtml(label)}: ${escapeHtml(String(disp))}`;
          }).join(' · ');
          const note = e.notes ? ` <span class="text-muted">· ${escapeHtml(e.notes)}</span>` : '';
          return `<li class="list-group-item d-flex justify-content-between align-items-center py-1 small">
            <span><strong>${escapeHtml(e.activity_name)}</strong>
              <span class="text-muted">· ${e.entry_date.slice(0, 10)}</span> ${parts}${note}</span>
            <span class="d-flex gap-2">
              <button type="button" class="btn btn-sm btn-link p-0" onclick="editEntry(${e.id})">Edit</button>
              <button type="button" class="btn btn-sm btn-link text-danger p-0" onclick="deleteEntry(${e.id})">✕</button>
            </span></li>`;
        }).join('') + '</ul>';
      wrap.dataset.entries = JSON.stringify(entries);
    }

    function editEntry(id) {
      const entries = JSON.parse(document.getElementById('peEntries').dataset.entries || '[]');
      const e = entries.find(x => x.id === id);
      if (!e) return;
      peEditingId = id;
      document.getElementById('peDate').value = e.entry_date.slice(0, 10);
      ActivitySection.prefill([{
        id: e.id, category_id: e.category_id, activity_id: e.activity_id,
        activity_name: e.activity_name, category_name: e.category_name,
        values: e.values, notes: e.notes, person_slot: null,
      }]);
      // Open the collapse if closed
      const body = document.getElementById('addProgressBody');
      if (!body.classList.contains('show')) new bootstrap.Collapse(body).show();
    }

    async function deleteEntry(id) {
      if (!confirm('Delete this entry?')) return;
      const res = await fetch(`/api/progress-entries/${id}`, {
        method: 'DELETE', headers: { accept: 'application/json' },
      });
      if (!res.ok) { alert('Delete failed'); return; }
      if (peEditingId === id) { peEditingId = null; ActivitySection.reset(); }
      loadEntriesList();
      loadProgress(RangeControl.getRange());
    }
```

- [ ] **Step 3: Manual verification**

Run the app locally:

```bash
DEV_LOGIN=1 .venv/bin/python -m uvicorn main:app --reload --port 8000
```

Then in a browser: open `http://localhost:8000/dev/login` (logs in as dev admin),
go to `/reports` → Progress tab and verify:

1. "+ Add progress" panel expands; date defaults to today, future dates blocked.
2. Pick category/activity, fill required field, "+ Add to session" stages the row
   (single button — no Person B), "Save progress" persists it; entry appears in
   "Recorded entries" and the Progress table/chart updates without a reload.
3. Edit loads the entry back into the form (date + values); saving applies the PUT.
4. Delete (✕, permanently visible) removes the entry and refreshes the chart.
5. As admin, the "For user" selector appears and lists users; switching it reloads
   the entries list. (Client role: selector absent — verify by setting the dev
   user's role to 'client' in the DB or checking the Jinja conditional.)

- [ ] **Step 4: Commit**

```bash
git add templates/reports.html
git commit -m "feat(ui): add standalone progress entry panel to Reports Progress tab"
```

---

### Task 7: Full verification + docs

**Files:**
- Modify: `README.md` (Changelog section, line 12)

- [ ] **Step 1: Full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 92 passed, zero failures

- [ ] **Step 2: Migration sanity check**

Run: `.venv/bin/python -m alembic upgrade head` against the local dev DB
(check `alembic.ini` / env for the configured URL first; do NOT run against prod).
Expected: `Running upgrade 4093faf32ea1 -> pe01standalone`

Then: `.venv/bin/python -m alembic downgrade -1 && .venv/bin/python -m alembic upgrade head`
Expected: clean round-trip.

- [ ] **Step 3: Changelog entry**

Add at the top of the `## Changelog` section in `README.md`:

```markdown
- **2026-06-12** — Standalone progress entries: record activity progress for any
  past date without a session (Reports → Progress → "+ Add progress"). Admins and
  trainers can log entries on behalf of other users. New `progress_entries` table
  (migration `pe01standalone`); entries merge seamlessly into Progress charts.
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: changelog for standalone progress entries"
```

---

## Out of scope (from spec)

- Full RBAC build-out (role management UI, trainer-user linkage)
- Package/session accounting changes — standalone entries never consume sessions
- Person-slot semantics — standalone entries are single-person
