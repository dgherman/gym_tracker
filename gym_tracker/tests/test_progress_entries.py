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


def test_update_activity_revalidates_existing_values(couples):
    db = TestSessionLocal()
    owner = db.get(models.User, couples._ids["owner"])
    # Second category with a different required field
    cat2 = models.ActivityCategory(name="Cardio", slug="cardio", sort_order=2)
    db.add(cat2); db.flush()
    db.add(models.CategoryField(category_id=cat2.id, key="distance", label="Distance",
                                field_type="decimal", is_required=True, sort_order=1))
    act2 = models.Activity(category_id=cat2.id, name="Bike")
    db.add(act2); db.commit()

    pe = _seed_entry(db, user_id=owner.id, activity_id=couples._ids["act"],
                     date=datetime(2026, 5, 1), reps=5)
    # Switching activity without new values: old {"reps": 5} lacks required "distance"
    with pytest.raises(ValueError):
        progress_entries.update_entry(db, actor=owner, entry_id=pe.id,
                                      data=schemas.ProgressEntryUpdate(activity_id=act2.id))
    # Switching with matching values succeeds
    out = progress_entries.update_entry(
        db, actor=owner, entry_id=pe.id,
        data=schemas.ProgressEntryUpdate(activity_id=act2.id, values={"distance": "12.5"}),
    )
    assert out["activity_name"] == "Bike"
    assert out["values"] == {"distance": 12.5}
    db.close()


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
