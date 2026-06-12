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
