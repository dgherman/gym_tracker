# Per-Person Activity Tracking + Couples Edit-Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let couples sessions record activities per person (Person A = owner, Person B = partner), and let either participant edit/delete a couples session.

**Architecture:** Add a nullable `person_slot` column to `session_activities` (NULL=shared/legacy, 1=owner, 2=partner). Validation + name resolution live in the existing `gym_tracker/activities.py` reconcile path and `gym_tracker/crud.py` annotate path. Edit/delete authorization moves from logger-only to any session participant, with pack reallocation scoped to the purchase owner. Frontend groups activity rows into two labeled add-sections.

**Tech Stack:** FastAPI 0.135, Starlette 1.0, SQLAlchemy 1.4, Alembic, Jinja2, vanilla JS (Bootstrap 5), pytest + in-memory SQLite (StaticPool).

**Conventions (read before starting):**
- venv only: run tests with `.venv/bin/python -m pytest` from repo root.
- Tests bypass login with header `accept: application/json`.
- Tests use in-memory SQLite via `Base.metadata.create_all` — the model column is enough for tests; the Alembic migration is for prod MySQL.
- Current Alembic head: `ab12activity01`.
- Per repo CLAUDE.md: add a changelog entry to `README.md` before any push to main.

---

## File Structure

- `gym_tracker/models.py` — add `person_slot` column to `SessionActivity`.
- `alembic/versions/<new>_add_person_slot.py` — prod migration.
- `gym_tracker/schemas.py` — `person_slot` on `SessionActivityInput`; `person_slot`+`person_name` on `SessionActivityRead`.
- `gym_tracker/activities.py` — persist + validate `person_slot` in `reconcile_session_activities`.
- `gym_tracker/crud.py` — resolve `person_slot`/`person_name` in `_annotate_session_activities`; add `session_participant_ids` + `user_can_edit_session`.
- `main.py` — edit/delete auth + owner-scoped pack reallocation.
- `templates/_activity_section.html` — person-slot-aware rendering + two add destinations.
- `templates/index.html`, `templates/history.html` — pass couples context (num_people, person labels) to the section.
- `gym_tracker/tests/test_person_slot.py` — new backend tests.
- `gym_tracker/tests/test_session_edit_auth.py` — new backend tests.
- `README.md` — changelog entry.

---

## Task 1: Add `person_slot` column to the model

**Files:**
- Modify: `gym_tracker/models.py:195-207` (SessionActivity)
- Test: `gym_tracker/tests/test_person_slot.py`

- [ ] **Step 1: Write the failing test**

Create `gym_tracker/tests/test_person_slot.py`. Reuse the fixture style from `test_activity_api.py` but build a **couples** purchase (num_people=2, owner + partner user). This fixture is used by later tasks too.

```python
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from gym_tracker.database import Base
from gym_tracker import models

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture
def couples(client_factory):
    return client_factory(num_people=2, with_partner=True)


@pytest.fixture
def client_factory():
    created = {}

    def _make(num_people=2, with_partner=True):
        Base.metadata.create_all(bind=test_engine)
        db = TestSessionLocal()
        cat = models.ActivityCategory(name="Strength", slug="strength", sort_order=1)
        db.add(cat); db.flush()
        db.add(models.CategoryField(category_id=cat.id, key="reps", label="Reps",
                                    field_type="integer", is_required=True, sort_order=1))
        act = models.Activity(category_id=cat.id, name="Bench Press"); db.add(act)
        owner = models.User(google_sub="owner-sub", email="owner@x.com", role="client")
        partner = models.User(google_sub="partner-sub", email="partner@x.com", role="client")
        outsider = models.User(google_sub="out-sub", email="out@x.com", role="client")
        db.add_all([owner, partner, outsider]); db.flush()
        pur = models.Purchase(duration_minutes=30, total_sessions=10, sessions_remaining=10,
                              num_people=num_people, logged_by_user_id=owner.id,
                              partner_user_id=partner.id if with_partner else None)
        db.add(pur); db.flush()
        sess = models.Session(purchase_id=pur.id, duration_minutes=30, trainer="Alex",
                              session_date=__import__("datetime").datetime(2026, 5, 1, 10, 0),
                              created_by_user_id=owner.id)
        db.add(sess); db.commit()
        ids = {"cat": cat.id, "act": act.id, "owner": owner.id, "partner": partner.id,
               "outsider": outsider.id, "purchase": pur.id, "session": sess.id}
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
        created["c"] = c
        return c

    yield _make
    main.app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=test_engine)


def test_session_activity_has_person_slot_column():
    assert hasattr(models.SessionActivity, "person_slot")
    sa = models.SessionActivity(session_id=1, activity_id=1, values={}, person_slot=2)
    assert sa.person_slot == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest gym_tracker/tests/test_person_slot.py::test_session_activity_has_person_slot_column -v`
Expected: FAIL — `AttributeError: type object 'SessionActivity' has no attribute 'person_slot'` (or TypeError on the kwarg).

- [ ] **Step 3: Add the column**

In `gym_tracker/models.py`, inside `class SessionActivity`, after the `sort_order` line (currently line 203):

```python
    sort_order = Column(Integer, nullable=False, default=0)
    # NULL = shared / whole-session (legacy + single-person); 1 = owner (Person A); 2 = partner (Person B)
    person_slot = Column(Integer, nullable=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest gym_tracker/tests/test_person_slot.py::test_session_activity_has_person_slot_column -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gym_tracker/models.py gym_tracker/tests/test_person_slot.py
git commit -m "feat(activities): add person_slot column to session_activities"
```

---

## Task 2: Alembic migration for `person_slot` (prod MySQL)

**Files:**
- Create: `alembic/versions/<rev>_add_person_slot.py`

- [ ] **Step 1: Generate a stub revision**

Run: `.venv/bin/alembic revision -m "add person_slot to session_activities"`
This creates a file under `alembic/versions/` with a generated `revision` id and `down_revision = 'ab12activity01'` (verify the down_revision points at the current head; fix it if not).

- [ ] **Step 2: Fill in upgrade/downgrade**

Edit the generated file's `upgrade()` and `downgrade()`:

```python
from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column("session_activities", sa.Column("person_slot", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("session_activities", "person_slot")
```

- [ ] **Step 3: Verify the migration is consistent**

Run: `.venv/bin/alembic heads`
Expected: exactly one head — the new revision.
Run: `.venv/bin/alembic check 2>/dev/null || true` (informational; the model matches the migration).

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/
git commit -m "feat(db): migration adds person_slot to session_activities"
```

---

## Task 3: Schema fields for `person_slot` / `person_name`

**Files:**
- Modify: `gym_tracker/schemas.py` (`SessionActivityInput`, `SessionActivityRead`)
- Test: `gym_tracker/tests/test_person_slot.py`

- [ ] **Step 1: Write the failing test**

Append to `gym_tracker/tests/test_person_slot.py`:

```python
from gym_tracker import schemas


def test_input_schema_accepts_person_slot():
    s = schemas.SessionActivityInput(activity_id=1, values={}, person_slot=2)
    assert s.person_slot == 2


def test_input_schema_person_slot_defaults_none():
    s = schemas.SessionActivityInput(activity_id=1, values={})
    assert s.person_slot is None


def test_read_schema_has_person_fields():
    fields = schemas.SessionActivityRead.model_fields
    assert "person_slot" in fields
    assert "person_name" in fields
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest gym_tracker/tests/test_person_slot.py -k "schema" -v`
Expected: FAIL — `person_slot` unknown / not in `model_fields`.

- [ ] **Step 3: Add the fields**

In `gym_tracker/schemas.py`, in `SessionActivityInput` (after `notes`):

```python
class SessionActivityInput(BaseModel):
    # id present => update existing row; absent => insert
    id: int | None = None
    activity_id: int
    values: dict = {}
    notes: str | None = None
    person_slot: int | None = None  # None=shared, 1=owner, 2=partner
```

In `SessionActivityRead` (after `notes`):

```python
class SessionActivityRead(BaseModel):
    id: int
    activity_id: int
    activity_name: str
    category_id: int
    category_name: str
    values: dict = {}
    notes: str | None = None
    person_slot: int | None = None
    person_name: str | None = None
    sort_order: int
    model_config = {"from_attributes": True}
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest gym_tracker/tests/test_person_slot.py -k "schema" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gym_tracker/schemas.py gym_tracker/tests/test_person_slot.py
git commit -m "feat(activities): person_slot/person_name on activity schemas"
```

---

## Task 4: Persist + validate `person_slot` in reconcile

**Files:**
- Modify: `gym_tracker/activities.py` (`reconcile_session_activities`, ~last function)
- Test: `gym_tracker/tests/test_person_slot.py`

Rules: `person_slot ∈ {None,1,2}`; if purchase `num_people <= 1` force `None`; `person_slot == 2` requires a resolvable partner (`session.partner_user_id` or `purchase.partner_user_id` or `purchase.partner_email`), else `ValueError`.

- [ ] **Step 1: Write the failing tests**

Append to `gym_tracker/tests/test_person_slot.py`:

```python
from gym_tracker import activities as activities_mod


def _input(ids, slot):
    return schemas.SessionActivityInput(activity_id=ids["act"], values={"reps": 5}, person_slot=slot)


def test_reconcile_persists_person_slot(couples):
    db = TestSessionLocal()
    ids = couples._ids
    sess = db.get(models.Session, ids["session"])
    activities_mod.reconcile_session_activities(db, sess, [_input(ids, 1), _input(ids, 2)])
    db.commit()
    slots = sorted(sa.person_slot for sa in db.get(models.Session, ids["session"]).activities)
    assert slots == [1, 2]
    db.close()


def test_reconcile_slot2_without_partner_rejected(client_factory):
    c = client_factory(num_people=2, with_partner=False)  # no partner_user_id, no partner_email
    db = TestSessionLocal()
    ids = c._ids
    sess = db.get(models.Session, ids["session"])
    with pytest.raises(ValueError):
        activities_mod.reconcile_session_activities(db, sess, [_input(ids, 2)])
    db.close()


def test_reconcile_single_person_forces_none(client_factory):
    c = client_factory(num_people=1, with_partner=False)
    db = TestSessionLocal()
    ids = c._ids
    sess = db.get(models.Session, ids["session"])
    activities_mod.reconcile_session_activities(db, sess, [_input(ids, 1)])
    db.commit()
    assert db.get(models.Session, ids["session"]).activities[0].person_slot is None
    db.close()


def test_reconcile_rejects_bad_slot(couples):
    db = TestSessionLocal()
    ids = couples._ids
    sess = db.get(models.Session, ids["session"])
    with pytest.raises(ValueError):
        activities_mod.reconcile_session_activities(db, sess, [_input(ids, 3)])
    db.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest gym_tracker/tests/test_person_slot.py -k reconcile -v`
Expected: FAIL — slots not persisted / no validation raised.

- [ ] **Step 3: Implement**

In `gym_tracker/activities.py`, replace the body of `reconcile_session_activities`. Add a helper above it and resolve `person_slot` per row:

```python
def _resolve_person_slot(session, purchase, raw_slot):
    """Normalize/validate a desired person_slot for one activity row.
    Returns the slot to store (None/1/2). Raises ValueError on bad input."""
    if raw_slot is None:
        return None
    if raw_slot not in (1, 2):
        raise ValueError("person_slot must be null, 1, or 2")
    num_people = purchase.num_people if purchase else 1
    if num_people <= 1:
        return None  # single-person sessions are never per-person
    if raw_slot == 2:
        has_partner = bool(
            session.partner_user_id
            or (purchase and purchase.partner_user_id)
            or (purchase and purchase.partner_email)
        )
        if not has_partner:
            raise ValueError("Cannot assign activity to Person B: session has no partner")
    return raw_slot


def reconcile_session_activities(db: Session, session: models.Session, items, *, created_by_user_id=None):
    """Reconcile a session's activities to the desired set `items`
    (list of schemas.SessionActivityInput). Upsert by id, delete omitted.
    Validates each row's values and person_slot; raises ValueError on any
    problem. Does NOT commit — caller commits so the edit is atomic."""
    purchase = db.get(models.Purchase, session.purchase_id)
    existing = {sa.id: sa for sa in session.activities}
    seen_ids = set()

    for idx, item in enumerate(items):
        activity = db.get(models.Activity, item.activity_id)
        if not activity or not activity.is_active:
            raise ValueError("Selected activity is not available (inactive or unknown)")
        cleaned = validate_activity_values(db, activity, item.values)
        slot = _resolve_person_slot(session, purchase, getattr(item, "person_slot", None))

        if item.id is not None:
            sa = existing.get(item.id)
            if sa is None:
                raise ValueError("Activity row is not on this session")
            sa.activity_id = activity.id
            sa.values = cleaned
            sa.notes = item.notes
            sa.sort_order = idx
            sa.person_slot = slot
            seen_ids.add(item.id)
        else:
            sa = models.SessionActivity(
                session_id=session.id,
                activity_id=activity.id,
                values=cleaned,
                notes=item.notes,
                sort_order=idx,
                person_slot=slot,
                created_at=datetime.now(timezone.utc),
            )
            db.add(sa)

    for sid, sa in existing.items():
        if sid not in seen_ids:
            db.delete(sa)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest gym_tracker/tests/test_person_slot.py -k reconcile -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gym_tracker/activities.py gym_tracker/tests/test_person_slot.py
git commit -m "feat(activities): validate+persist person_slot in reconcile"
```

---

## Task 5: Resolve `person_slot` → `person_name` on read

**Files:**
- Modify: `gym_tracker/crud.py:99-108` (`_annotate_session_activities`)
- Test: `gym_tracker/tests/test_person_slot.py`

Absolute labels (not viewer-relative): slot 1 → owner (`purchase.logged_by_user`), slot 2 → partner (session override, else purchase partner user, else `partner_email`), NULL → `"Both / Shared"`.

- [ ] **Step 1: Write the failing test**

Append to `gym_tracker/tests/test_person_slot.py`:

```python
from gym_tracker import crud


def test_annotate_resolves_person_names(couples):
    db = TestSessionLocal()
    ids = couples._ids
    sess = db.get(models.Session, ids["session"])
    activities_mod.reconcile_session_activities(
        db, sess,
        [_input(ids, 1), _input(ids, 2),
         schemas.SessionActivityInput(activity_id=ids["act"], values={"reps": 1}, person_slot=None)],
    )
    db.commit()
    sess = db.get(models.Session, ids["session"])
    crud._annotate_session_activities(db, sess)
    by_slot = {sa.person_slot: sa.person_name for sa in sess.activities}
    assert by_slot[1] == "owner@x.com"        # owner has no full_name -> email
    assert by_slot[2] == "partner@x.com"
    assert by_slot[None] == "Both / Shared"
    db.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest gym_tracker/tests/test_person_slot.py -k annotate -v`
Expected: FAIL — `person_name` not set (AttributeError or None).

- [ ] **Step 3: Implement**

In `gym_tracker/crud.py`, replace `_annotate_session_activities`:

```python
def _person_name_for_slot(db, purchase, sess, slot):
    """Absolute person label for an activity row's slot.
    1=owner, 2=partner, None=shared. Returns a display string."""
    if slot is None:
        return "Both / Shared"
    if slot == 1:
        owner = purchase.logged_by_user if purchase else None
        if owner:
            return owner.full_name or owner.email
        return "Person A"
    # slot == 2 (partner): session override -> purchase partner user -> partner_email
    if sess.partner_user_id and getattr(sess, "partner_user", None):
        return sess.partner_user.full_name or sess.partner_user.email
    if purchase and purchase.partner_user_id and getattr(purchase, "partner_user", None):
        return purchase.partner_user.full_name or purchase.partner_user.email
    if purchase and purchase.partner_email:
        return purchase.partner_email
    return "Person B"


def _annotate_session_activities(db, sess):
    """Attach activity_name, category_id, category_name, person_slot and
    person_name onto each SessionActivity for SessionActivityRead."""
    purchase = db.get(models.Purchase, sess.purchase_id)
    for sa in sess.activities:
        activity = sa.activity or db.get(models.Activity, sa.activity_id)
        sa.activity_name = activity.name if activity else "(unknown)"
        category = db.get(models.ActivityCategory, activity.category_id) if activity else None
        sa.category_id = category.id if category else 0
        sa.category_name = category.name if category else "(unknown)"
        sa.person_name = _person_name_for_slot(db, purchase, sess, sa.person_slot)
```

(`person_slot` is a real column so it serializes without help; only `person_name` needs attaching.)

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest gym_tracker/tests/test_person_slot.py -k annotate -v`
Expected: PASS

- [ ] **Step 5: Full backend regression**

Run: `.venv/bin/python -m pytest gym_tracker/tests/ -v`
Expected: PASS (no regressions in existing activity/crud tests).

- [ ] **Step 6: Commit**

```bash
git add gym_tracker/crud.py gym_tracker/tests/test_person_slot.py
git commit -m "feat(activities): resolve person_name for activity rows on read"
```

---

## Task 6: Auth helper — any participant can edit

**Files:**
- Modify: `gym_tracker/crud.py` (add helpers near `_user_session_ids`, ~line 19)
- Test: `gym_tracker/tests/test_session_edit_auth.py`

- [ ] **Step 1: Write the failing test**

Create `gym_tracker/tests/test_session_edit_auth.py`:

```python
from gym_tracker import crud, models


class _S:  # lightweight stand-ins (helpers are pure)
    def __init__(self, created_by, partner=None):
        self.created_by_user_id = created_by
        self.partner_user_id = partner


class _P:
    def __init__(self, owner, partner=None):
        self.logged_by_user_id = owner
        self.partner_user_id = partner


def test_participant_ids_includes_owner_and_partner():
    ids = crud.session_participant_ids(_S(created_by=1), _P(owner=1, partner=2))
    assert ids == {1, 2}


def test_owner_can_edit():
    assert crud.user_can_edit_session(_S(1), _P(1, 2), 1) is True


def test_partner_can_edit():
    assert crud.user_can_edit_session(_S(1), _P(1, 2), 2) is True


def test_outsider_cannot_edit():
    assert crud.user_can_edit_session(_S(1), _P(1, 2), 99) is False


def test_session_partner_override_counts():
    assert crud.user_can_edit_session(_S(1, partner=5), _P(1, None), 5) is True
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest gym_tracker/tests/test_session_edit_auth.py -v`
Expected: FAIL — `AttributeError: module 'gym_tracker.crud' has no attribute 'session_participant_ids'`.

- [ ] **Step 3: Implement**

In `gym_tracker/crud.py`, add near the other private helpers (after `_user_session_ids`, before `_resolve_partner`):

```python
def session_participant_ids(session, purchase) -> set:
    """User ids allowed to edit/delete a session: creator, purchase owner,
    and the session/purchase partner. partner_email-only partners have no
    account and cannot log in, so they need no entry."""
    ids = {
        getattr(session, "created_by_user_id", None),
        getattr(session, "partner_user_id", None),
        getattr(purchase, "logged_by_user_id", None) if purchase else None,
        getattr(purchase, "partner_user_id", None) if purchase else None,
    }
    ids.discard(None)
    return ids


def user_can_edit_session(session, purchase, user_id) -> bool:
    return user_id in session_participant_ids(session, purchase)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest gym_tracker/tests/test_session_edit_auth.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gym_tracker/crud.py gym_tracker/tests/test_session_edit_auth.py
git commit -m "feat(sessions): participant-based edit authorization helper"
```

---

## Task 7: Wire participant auth + owner-scoped packs into edit/delete endpoints

**Files:**
- Modify: `main.py` `api_edit_session` (~lines 278-345) and `api_delete_session` (~lines 370-398)
- Test: `gym_tracker/tests/test_session_edit_auth.py`

Logic changes in `api_edit_session`:
- Load purchase; replace `if s.created_by_user_id != user_id` gate with `user_can_edit_session`.
- In the duration-change branch, scope refund + new-pack lookup + ownership guard to `owner_id = s_purchase.logged_by_user_id` instead of `user_id`.
In `api_delete_session`:
- Replace gate with `user_can_edit_session`.
- Refund to the funding purchase unconditionally (drop `== user_id`).

- [ ] **Step 1: Write the failing integration tests**

Append to `gym_tracker/tests/test_session_edit_auth.py`. Reuse the `client_factory`/`couples` fixtures — import them via a shared conftest. Add `gym_tracker/tests/conftest.py` housing `client_factory` + `couples` (move them out of `test_person_slot.py` into conftest so both test modules share them; update `test_person_slot.py` to drop its local copies).

```python
import os
import datetime


# /dev/login (GET) is env-gated by DEV_LOGIN and logs in as the user whose
# email == DEV_LOGIN_EMAIL (it finds our seeded users by email and sets
# request.session["user_id"]). It IGNORES any user_id param. So we switch
# acting-user by setting DEV_LOGIN_EMAIL, then GET /dev/login; the signed
# session cookie lands in the TestClient cookie jar for later requests.
# conftest.py must set os.environ["DEV_LOGIN"] = "1" BEFORE importing main.
def _login(c, email):
    os.environ["DEV_LOGIN_EMAIL"] = email
    r = c.get("/dev/login", follow_redirects=False)
    assert r.status_code in (200, 302, 307), r.text


def _edit_payload(session_date="2026-05-01T10:00:00", duration=30, trainer="Alex", activities=None):
    return {"session_date": session_date, "duration_minutes": duration,
            "trainer": trainer, "activities": activities or []}


def test_partner_can_edit_session(couples):
    c = couples
    _login(c, "partner@x.com")
    r = c.post(f"/history/api/edit/session/{c._ids['session']}",
               json=_edit_payload(), headers={"accept": "application/json"})
    assert r.status_code == 200, r.text


def test_outsider_cannot_edit_session(couples):
    c = couples
    _login(c, "out@x.com")
    r = c.post(f"/history/api/edit/session/{c._ids['session']}",
               json=_edit_payload(), headers={"accept": "application/json"})
    assert r.status_code == 403


def test_partner_can_delete_session(couples):
    c = couples
    _login(c, "partner@x.com")
    r = c.post(f"/history/api/delete/session/{c._ids['session']}",
               headers={"accept": "application/json"})
    assert r.status_code == 200, r.text


def test_partner_duration_change_reallocates_owner_packs(couples_with_60min_owner_pack):
    # Fixture seeds a 60-min pack owned by OWNER so reallocation can succeed.
    c, db_factory = couples_with_60min_owner_pack
    _login(c, "partner@x.com")
    r = c.post(f"/history/api/edit/session/{c._ids['session']}",
               json=_edit_payload(duration=60), headers={"accept": "application/json"})
    assert r.status_code == 200, r.text
    db = db_factory()
    owner_60 = (db.query(models.Purchase)
                .filter(models.Purchase.logged_by_user_id == c._ids["owner"],
                        models.Purchase.duration_minutes == 60).first())
    assert owner_60.sessions_remaining == owner_60.total_sessions - 1  # one consumed off OWNER's pack
    db.close()
```

NOTE for the implementer — `/dev/login` mechanics (verified against `main.py`):
- Route is `GET /dev/login`, gated by env `DEV_LOGIN` ∈ {1,true,yes}. It finds the user whose `email == DEV_LOGIN_EMAIL` and sets `request.session["user_id"]`. It does NOT accept a `user_id` param. Our fixture seeds users with emails `owner@x.com`, `partner@x.com`, `out@x.com`, so `_login(c, "<email>")` selects them.
- `conftest.py` MUST set `os.environ["DEV_LOGIN"] = "1"` BEFORE `import main` (the guard reads env at request time, but set it early to be safe). The signed session cookie requires the real `SessionMiddleware`, which is active in `main.app` — `TestClient` persists the cookie across requests automatically.
- Caution: if `DEV_LOGIN_EMAIL` points at an email with no seeded user, `dev_login` will CREATE a new admin user — always set it to one of the seeded emails.
- For `couples_with_60min_owner_pack`: extend `client_factory` (in conftest) with an option to also seed a 60-min `Purchase` owned by `owner` (e.g. `total_sessions=5, sessions_remaining=5`). Have the fixture return `(client, TestSessionLocal)` so the assertion can re-open a session. Assert the move landed on the OWNER's pack, not the partner's.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest gym_tracker/tests/test_session_edit_auth.py -k "partner or outsider" -v`
Expected: FAIL — partner gets 403 (current code), or dev-login wiring missing.

- [ ] **Step 3: Implement endpoint changes**

In `main.py` `api_edit_session`, replace the ownership block:

```python
    s = db.query(models.Session).filter(models.Session.id == session_id).first()
    if not s:
        raise HTTPException(404, "Session not found")

    s_purchase = db.query(models.Purchase).filter(models.Purchase.id == s.purchase_id).first()
    if not crud.user_can_edit_session(s, s_purchase, user_id):
        raise HTTPException(403, "Not allowed")

    owner_id = s_purchase.logged_by_user_id if s_purchase else user_id
```

Then in the duration-change branch, swap every `user_id` used for pack scoping to `owner_id`:

```python
    if new_duration != old_duration:
        original_purchase = db.query(models.Purchase).filter(models.Purchase.id == s.purchase_id).first()
        if original_purchase:
            if original_purchase.logged_by_user_id != owner_id:
                raise HTTPException(403, "Not allowed to modify packs you don't own")
            original_purchase.sessions_remaining += 1
            db.add(original_purchase)

        new_pack = (
            db.query(models.Purchase)
            .filter(
                models.Purchase.duration_minutes == new_duration,
                models.Purchase.sessions_remaining > 0,
                models.Purchase.logged_by_user_id == owner_id,
            )
            .order_by(models.Purchase.purchase_date)
            .first()
        )
        if not new_pack:
            raise HTTPException(400, f"No {new_duration}-min package available to reallocate")
        new_pack.sessions_remaining -= 1
        db.add(new_pack)
        s.purchase_id = new_pack.id
        s.duration_minutes = new_duration
```

In `api_delete_session`, replace the ownership + refund block:

```python
    s = db.query(models.Session).filter(models.Session.id == session_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")

    purchase = db.query(models.Purchase).filter(models.Purchase.id == s.purchase_id).first()
    if not crud.user_can_edit_session(s, purchase, user_id):
        raise HTTPException(403, "Not allowed")

    # Refund the session to the purchase that funded it (regardless of who deletes).
    if purchase:
        purchase.sessions_remaining += 1
        db.add(purchase)

    db.delete(s)
    db.commit()
    return {"success": True}
```

Confirm `crud` is imported in `main.py` (it is — used elsewhere).

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest gym_tracker/tests/test_session_edit_auth.py -v`
Expected: PASS (all auth + reallocation tests).

- [ ] **Step 5: Full regression**

Run: `.venv/bin/python -m pytest gym_tracker/tests/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add main.py gym_tracker/tests/
git commit -m "feat(sessions): any participant can edit/delete couples session; packs scoped to owner"
```

---

## Task 8: Frontend — person-slot-aware activity section

**Files:**
- Modify: `templates/_activity_section.html`

This module is a singleton (hardcoded element IDs). Make it couples-aware via a public `setPeople({numPeople, personA, personB})` call. When `numPeople > 1`, render rows grouped under two headers and show two destination buttons ("Add to <A>" / "Add to <B>"); the clicked button sets the new/edited row's `person_slot`. Solo keeps current single-list, single-button behavior and sends `person_slot: null`.

- [ ] **Step 1: Add people state + setter**

Near the top of the IIFE (after `let editingIndex = null;`):

```javascript
  let people = { numPeople: 1, personA: 'Person A', personB: 'Person B' };
  let addSlot = null;  // slot the next add() will assign (set by the destination buttons)
  function setPeople(p) {
    people = Object.assign({ numPeople: 1, personA: 'Person A', personB: 'Person B' }, p || {});
    renderAddButtons();
    renderRows();
  }
```

- [ ] **Step 2: Replace the single add button with destination buttons**

In the HTML, replace the `actAddBtn` button line:

```html
          <div class="col-12" id="actAddBtns">
            <button type="button" id="actAddBtn" class="btn btn-sm btn-outline-success">+ Add to session</button>
            <button type="button" id="actAddBtnB" class="btn btn-sm btn-outline-primary d-none">+ Add to Person B</button>
          </div>
```

Add `renderAddButtons()`:

```javascript
  function renderAddButtons() {
    const a = document.getElementById('actAddBtn');
    const b = document.getElementById('actAddBtnB');
    if (!a) return;
    if (people.numPeople > 1) {
      a.textContent = (editingIndex !== null ? 'Save for ' : '+ Add to ') + people.personA;
      b.textContent = (editingIndex !== null ? 'Save for ' : '+ Add to ') + people.personB;
      b.classList.remove('d-none');
    } else {
      a.textContent = (editingIndex !== null ? 'Save changes' : '+ Add to session');
      b.classList.add('d-none');
    }
  }
```

- [ ] **Step 3: Make `add(slot)` carry person_slot; wire both buttons**

Change `add()` signature to `add(slot)` and set the row's `person_slot`:

```javascript
  async function add(slot) {
    // ...existing validation up to building `row`...
    const row = {
      id: (editingIndex !== null && rows[editingIndex]) ? rows[editingIndex].id : undefined,
      category_id: cat.id,
      activity_id: activity.id,
      activity_name: activity.name,
      category_name: cat.name,
      values,
      notes: document.getElementById('actNote').value.trim() || null,
      person_slot: people.numPeople > 1 ? (slot || 1) : null,
    };
    // ...existing push/replace + clearForm + renderRows...
  }
```

In `init()`, wire both buttons (replace the single listener):

```javascript
    document.getElementById('actAddBtn').addEventListener('click', () => add(1));
    const bBtn = document.getElementById('actAddBtnB');
    if (bBtn) bBtn.addEventListener('click', () => add(2));
```

- [ ] **Step 4: Group `renderRows()` by person for couples**

Replace `renderRows()` so couples render two labeled groups (slot 1 / slot 2), and any `person_slot == null` rows render under a "Both / Shared" group. Solo renders the flat list as today. Keep the existing per-row Edit/✕ buttons and `editingIndex` highlight.

```javascript
  function rowHtml(r, i) {
    const parts = Object.entries(r.values).map(([k, v]) => `${escapeHtml(k)}: ${escapeHtml(v)}`).join(' · ');
    const note = r.notes ? ` <span class="text-muted">· ${escapeHtml(r.notes)}</span>` : '';
    const editing = (editingIndex === i) ? ' bg-warning-subtle' : '';
    return `<li class="list-group-item d-flex justify-content-between align-items-center py-1 small${editing}">
      <span><strong>${escapeHtml(r.activity_name)}</strong> <span class="text-muted">· ${escapeHtml(r.category_name)}</span> ${parts}${note}</span>
      <span class="d-flex gap-2">
        <button type="button" class="btn btn-sm btn-link p-0" onclick="ActivitySection.edit(${i})">Edit</button>
        <button type="button" class="btn btn-sm btn-link text-danger p-0" onclick="ActivitySection.remove(${i})">✕</button>
      </span></li>`;
  }

  function group(label, indices) {
    if (!indices.length) return '';
    return `<div class="fw-bold small mt-2">${escapeHtml(label)}</div>` +
      `<ul class="list-group mb-1">${indices.map(i => rowHtml(rows[i], i)).join('')}</ul>`;
  }

  function renderRows() {
    const ul = document.getElementById('actList');
    if (people.numPeople <= 1) {
      ul.innerHTML = rows.length ? `<ul class="list-group">${rows.map((r, i) => rowHtml(r, i)).join('')}</ul>` : '';
      return;
    }
    const idx = (s) => rows.map((r, i) => [r, i]).filter(([r]) => (r.person_slot || null) === s).map(([, i]) => i);
    ul.innerHTML =
      group(people.personA, idx(1)) +
      group(people.personB, idx(2)) +
      group('Both / Shared', idx(null));
  }
```

(Note: `actList` is a `<ul>` today; with grouping it now holds child `<ul>`s. Change the outer `#actList` element from `<ul>` to `<div>` in the HTML to keep markup valid.)

- [ ] **Step 5: `prefill` + `collect` carry person_slot**

In `prefill`, map `person_slot: r.person_slot ?? null`. In `collect`, add `person_slot: r.person_slot ?? null`. Export `setPeople` in the returned object. Call `renderAddButtons()` from `clearForm()` so button labels reset out of edit mode.

- [ ] **Step 6: Manual verification (no JS test harness in repo)**

Run the app with dev login and exercise a couples session end-to-end (covered in Task 9 verification). Commit after Task 9 wiring so the section is actually fed `setPeople`.

- [ ] **Step 7: Commit**

```bash
git add templates/_activity_section.html
git commit -m "feat(ui): person-slot grouping + per-person add in activity section"
```

---

## Task 9: Wire couples context in create + history pages

**Files:**
- Modify: `templates/index.html` (log-session modal — call `ActivitySection.setPeople` from the existing num_people/partner logic)
- Modify: `templates/history.html` (edit-session modal — call `setPeople` using the session's `num_people`, owner = current user, `partner_name`)

- [ ] **Step 1: Create modal**

In `index.html`, find where the package/num_people + partner_email inputs drive the log-session modal. When the chosen package has `num_people > 1`, call:

```javascript
ActivitySection.setPeople({
  numPeople: 2,
  personA: (window.CURRENT_USER_NAME || 'You'),
  personB: (partnerEmailInput.value.trim() || 'Partner'),
});
```

Call `setPeople({numPeople:1})` when solo. Update the Person B label on partner_email `input` events. Confirm `window.CURRENT_USER_NAME` is available; if not, render it from the template context (`current_user`) into a `<script>` var, mirroring how other user context reaches the page.

- [ ] **Step 2: History edit modal**

In `history.html`, the edit modal already has each session's `num_people` and `partner_name` (from `SessionActivityRead`/`Session` schema). When opening edit:

```javascript
ActivitySection.setPeople({
  numPeople: sess.num_people,
  personA: (window.CURRENT_USER_NAME || 'You'),
  personB: (sess.partner_name || 'Partner'),
});
ActivitySection.prefill(sess.activities);
```

Ensure this runs before/with the existing `prefill` call.

- [ ] **Step 3: Manual end-to-end verification**

Start app against local Docker MySQL (per memory: `gym_tracker_local`), run the migration, log in via dev-login:

```bash
docker start gym_tracker_local 2>/dev/null || true
.venv/bin/alembic upgrade head
.venv/bin/python -m uvicorn main:app --reload --port 8001
```

Verify in browser:
1. Create a couples session → two add destinations appear with owner/partner labels.
2. Add one activity to Person A, one to Person B → they render under the right headers.
3. Save, reload history → grouping persists with correct names.
4. Edit the session as the partner account (dev-login as partner) → allowed; change duration → owner's pack count adjusts.
5. Open an old (pre-migration) couples session → its rows show under "Both / Shared", still editable.
6. Solo session → single list, no person UI.

- [ ] **Step 4: Commit**

```bash
git add templates/index.html templates/history.html
git commit -m "feat(ui): feed couples context (people labels) to activity section"
```

---

## Task 10: Changelog + docs

**Files:**
- Modify: `README.md` (changelog entry — required by repo CLAUDE.md before push)
- Modify: `ARCHITECTURE.md` (note person_slot + participant edit-auth) — optional but recommended

Per repo CLAUDE.md, documentation is delegated to the worker model. After code is merged-ready:

- [ ] **Step 1: Generate doc changes via worker**

```bash
extract-chat "$(ls -t ~/.claude/projects/*/$(basename $PWD)*/*.jsonl 2>/dev/null | head -1)" -o /tmp/chat.txt 2>/dev/null || true
ask-kimi --paths /tmp/chat.txt README.md ARCHITECTURE.md \
  --question "Read the chat. Give exact changelog entry for README.md (per-person activity tracking via person_slot; couples sessions editable by either participant; packs scoped to owner) and exact ARCHITECTURE.md additions for these two changes."
```

- [ ] **Step 2: Apply the worker's suggested edits via Edit tool** (review first; keep style consistent with existing entries).

- [ ] **Step 3: Final full test run**

Run: `.venv/bin/python -m pytest gym_tracker/tests/ -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add README.md ARCHITECTURE.md
git commit -m "docs: per-person activity tracking + couples edit-auth"
```

---

## Self-Review Notes

- **Spec coverage:** person_slot column (T1/T2), schemas (T3), validation rules incl. num_people=1 + slot-2-without-partner (T4), name resolution + legacy NULL=Both/Shared (T5), participant auth (T6), endpoint auth + owner-scoped packs + unconditional delete refund (T7), two-block UI + no Shared add block + legacy shared display (T8/T9), tests (T1,T3-T7), docs/changelog (T10). All spec sections mapped.
- **Type consistency:** `person_slot` (int|None), `person_name` (str|None), `setPeople({numPeople, personA, personB})`, `add(slot)`, `session_participant_ids`/`user_can_edit_session` used consistently across tasks.
- **Known soft spot:** Task 7 dev-login/session wiring depends on the exact env flag in `main.py` (`fe12187`); implementer must verify it and document in `conftest.py`. Flagged inline.
