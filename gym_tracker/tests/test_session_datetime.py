"""Item 2 — retroactive session date/time.

POST /sessions/ accepts an optional client-supplied ``session_date`` (UTC ISO).
A past value is stored as the exact naive-UTC instant; omitting it stores ~now;
a value more than 5 minutes in the future is rejected 422 with no row written.
"""
import os
from datetime import datetime, timedelta, timezone

from gym_tracker import models
from gym_tracker.tests.db_test_utils import TestSessionLocal


def _login(c, email="owner@x.com"):
    os.environ["DEV_LOGIN_EMAIL"] = email
    r = c.get("/dev/login", follow_redirects=False)
    assert r.status_code in (200, 302, 303, 307), r.text


def _solo(client_factory):
    c = client_factory(num_people=1, with_partner=False)
    _login(c)
    return c


def _post(c, **extra):
    payload = {"duration_minutes": 30, "trainer": "Alex", "num_people": 1}
    payload.update(extra)
    return c.post("/sessions/", json=payload, headers={"accept": "application/json"})


def _session_count():
    db = TestSessionLocal()
    try:
        return db.query(models.Session).count()
    finally:
        db.close()


def _latest_session_date():
    db = TestSessionLocal()
    try:
        row = db.query(models.Session).order_by(models.Session.id.desc()).first()
        return row.session_date
    finally:
        db.close()


def test_past_session_date_stored_as_exact_naive_utc(client_factory):
    c = _solo(client_factory)
    r = _post(c, session_date="2026-01-15T09:30:00Z")
    assert r.status_code == 200, r.text
    stored = _latest_session_date()
    assert stored == datetime(2026, 1, 15, 9, 30, 0)
    assert stored.tzinfo is None


def test_omitted_session_date_defaults_to_now(client_factory):
    c = _solo(client_factory)
    r = _post(c)
    assert r.status_code == 200, r.text
    stored = _latest_session_date()
    assert stored.tzinfo is None
    delta = abs(datetime.utcnow() - stored)
    assert delta < timedelta(seconds=30), delta


def test_future_session_date_beyond_skew_rejected_422_no_row(client_factory):
    c = _solo(client_factory)
    before = _session_count()
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    r = _post(c, session_date=future.isoformat().replace("+00:00", "Z"))
    assert r.status_code == 422, r.text
    assert _session_count() == before


def test_future_session_date_within_skew_accepted(client_factory):
    c = _solo(client_factory)
    near = datetime.now(timezone.utc) + timedelta(minutes=2)
    r = _post(c, session_date=near.isoformat().replace("+00:00", "Z"))
    assert r.status_code == 200, r.text


def test_naive_and_aware_inputs_store_identical_value(client_factory):
    c = _solo(client_factory)
    r1 = _post(c, session_date="2026-01-15T09:30:00")
    assert r1.status_code == 200, r1.text
    naive_stored = _latest_session_date()
    r2 = _post(c, session_date="2026-01-15T09:30:00Z")
    assert r2.status_code == 200, r2.text
    aware_stored = _latest_session_date()
    assert naive_stored == aware_stored == datetime(2026, 1, 15, 9, 30, 0)
