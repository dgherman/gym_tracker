import pytest
from gym_tracker import models
# client_factory and couples fixtures are provided by conftest.py
# TestSessionLocal is imported here for use in test bodies
from gym_tracker.tests.db_test_utils import TestSessionLocal


def test_session_activity_has_person_slot_column():
    assert hasattr(models.SessionActivity, "person_slot")
    sa = models.SessionActivity(session_id=1, activity_id=1, values={}, person_slot=2)
    assert sa.person_slot == 2


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


def test_reconcile_no_purchase_coerces_slot_to_none(client_factory):
    """Session whose purchase_id resolves to nothing is treated as solo;
    any explicit slot must be silently coerced to None (not raise)."""
    c = client_factory(num_people=2, with_partner=True)
    db = TestSessionLocal()
    ids = c._ids
    # Build an orphan session that references a non-existent purchase
    orphan = models.Session(
        purchase_id=999999,
        duration_minutes=30,
        trainer="Alex",
        session_date=__import__("datetime").datetime(2026, 5, 2, 10, 0),
        created_by_user_id=ids["owner"],
    )
    db.add(orphan)
    db.commit()
    activities_mod.reconcile_session_activities(db, orphan, [_input(ids, 2)])
    db.commit()
    stored_slot = db.get(models.Session, orphan.id).activities[0].person_slot
    assert stored_slot is None
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


from gym_tracker import crud


# ---------------------------------------------------------------
# Requester-relative slot translation.
# Wire semantics: person_slot 1 = the authenticated requester, 2 = the
# other person. Stored semantics: 1 = purchase owner, 2 = partner.
# ---------------------------------------------------------------

def _sorted_rows(db, session_id):
    sess = db.get(models.Session, session_id)
    return sorted(sess.activities, key=lambda sa: sa.sort_order)


def test_reconcile_owner_requester_stores_slots_as_is(couples):
    db = TestSessionLocal()
    ids = couples._ids
    sess = db.get(models.Session, ids["session"])
    activities_mod.reconcile_session_activities(
        db, sess, [_input(ids, 1), _input(ids, 2)],
        created_by_user_id=ids["owner"])
    db.commit()
    rows = _sorted_rows(db, ids["session"])
    assert [sa.person_slot for sa in rows] == [1, 2]
    db.close()


def test_reconcile_partner_requester_swaps_slots(couples):
    """Partner says slot 1 ('me') -> stored as 2; slot 2 ('other') -> stored 1."""
    db = TestSessionLocal()
    ids = couples._ids
    sess = db.get(models.Session, ids["session"])
    activities_mod.reconcile_session_activities(
        db, sess, [_input(ids, 1), _input(ids, 2)],
        created_by_user_id=ids["partner"])
    db.commit()
    rows = _sorted_rows(db, ids["session"])
    assert [sa.person_slot for sa in rows] == [2, 1]
    db.close()


def test_reconcile_partner_requester_keeps_shared_none(couples):
    db = TestSessionLocal()
    ids = couples._ids
    sess = db.get(models.Session, ids["session"])
    activities_mod.reconcile_session_activities(
        db, sess, [_input(ids, None)],
        created_by_user_id=ids["partner"])
    db.commit()
    assert _sorted_rows(db, ids["session"])[0].person_slot is None
    db.close()


def test_annotate_emits_viewer_relative_slots_for_partner(couples):
    """Stored slot 1 (owner) viewed by the partner -> wire slot 2 with the
    owner's name; stored slot 2 -> wire slot 1 with the partner's name."""
    db = TestSessionLocal()
    ids = couples._ids
    sess = db.get(models.Session, ids["session"])
    activities_mod.reconcile_session_activities(
        db, sess, [_input(ids, 1), _input(ids, 2)],
        created_by_user_id=ids["owner"])
    db.commit()
    sess = db.get(models.Session, ids["session"])
    crud._annotate_session_activities(db, sess, for_user_id=ids["partner"])
    rows = sorted(sess.activities, key=lambda sa: sa.sort_order)
    read = [schemas.SessionActivityRead.model_validate(sa) for sa in rows]
    assert (read[0].person_slot, read[0].person_name) == (2, "owner@x.com")
    assert (read[1].person_slot, read[1].person_name) == (1, "partner@x.com")
    # Stored values untouched (absolute)
    assert [sa.person_slot for sa in _sorted_rows(db, ids["session"])] == [1, 2]
    db.close()


def test_history_sessions_roundtrip_for_partner(couples):
    """End-to-end: partner edits the session tagging slot 1 ('me') with
    reps=1; GET /history/sessions/ as partner must show that row as slot 1
    named partner@x.com."""
    import os
    c = couples
    ids = c._ids
    os.environ["DEV_LOGIN_EMAIL"] = "partner@x.com"
    r = c.get("/dev/login", follow_redirects=False)
    assert r.status_code in (200, 302, 307), r.text
    payload = {
        "session_date": "2026-05-01T10:00:00",
        "duration_minutes": 30,
        "trainer": "Alex",
        "activities": [
            {"activity_id": ids["act"], "values": {"reps": 1}, "person_slot": 1},
            {"activity_id": ids["act"], "values": {"reps": 2}, "person_slot": 2},
        ],
    }
    r = c.post(f"/history/api/edit/session/{ids['session']}",
               json=payload, headers={"accept": "application/json"})
    assert r.status_code == 200, r.text
    r = c.get("/history/sessions/", headers={"accept": "application/json"})
    assert r.status_code == 200, r.text
    sess = next(s for s in r.json() if s["id"] == ids["session"])
    by_reps = {a["values"]["reps"]: a for a in sess["activities"]}
    assert by_reps[1]["person_slot"] == 1
    assert by_reps[1]["person_name"] == "partner@x.com"
    assert by_reps[2]["person_slot"] == 2
    assert by_reps[2]["person_name"] == "owner@x.com"


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
