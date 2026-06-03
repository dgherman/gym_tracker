import os
import datetime

from gym_tracker import crud, models
from gym_tracker.tests.db_test_utils import TestSessionLocal


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


# ---------------------------------------------------------------------------
# Integration tests — edit/delete authorization via /dev/login session cookies
# ---------------------------------------------------------------------------

# /dev/login (GET) is env-gated by DEV_LOGIN and logs in as the user whose
# email == DEV_LOGIN_EMAIL (it finds our seeded users by email and sets
# request.session["user_id"]). It IGNORES any user_id param. So we switch
# acting-user by setting DEV_LOGIN_EMAIL, then GET /dev/login; the signed
# session cookie lands in the TestClient cookie jar for later requests.
# conftest.py sets os.environ["DEV_LOGIN"] = "1" BEFORE importing main.
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
