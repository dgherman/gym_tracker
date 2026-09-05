"""OAuth callback decision-table matrix (spec 5.3).

The OIDC token exchange is stubbed; each test drives gym_tracker.auth.auth_callback
directly with a fake request and a seeded users row.
"""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from gym_tracker import auth as auth_mod
from gym_tracker import models
from gym_tracker.database import Base

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture
def db_session():
    Base.metadata.create_all(bind=test_engine)
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=test_engine)


def _mk(db, **kw):
    u = models.User(**{"role": "client", **kw})
    db.add(u)
    db.commit()
    return u


def call_callback(claims, db, monkeypatch):
    async def fake_exchange(request):
        return {"userinfo": claims}

    monkeypatch.setattr(auth_mod.oauth.google, "authorize_access_token", fake_exchange)
    req = SimpleNamespace(session={})
    result = asyncio.run(auth_mod.auth_callback(req, db=db))
    return req, result


def test_no_invite_rejected(db_session, monkeypatch):
    with pytest.raises(HTTPException) as e:
        call_callback({"email": "x@x.com", "sub": "g1"}, db_session, monkeypatch)
    assert e.value.status_code == 403


def test_pending_rejected(db_session, monkeypatch):
    _mk(db_session, email="p@x.com", google_sub=None, status="pending")
    with pytest.raises(HTTPException) as e:
        call_callback({"email": "p@x.com", "sub": "g1"}, db_session, monkeypatch)
    assert e.value.status_code == 403


def test_disabled_rejected(db_session, monkeypatch):
    _mk(db_session, email="d@x.com", google_sub="g9", status="disabled")
    with pytest.raises(HTTPException) as e:
        call_callback({"email": "d@x.com", "sub": "g9"}, db_session, monkeypatch)
    assert e.value.status_code == 403


def test_active_null_sub_backfills(db_session, monkeypatch):
    u = _mk(db_session, email="a@x.com", google_sub=None, status="active")
    req, _ = call_callback({"email": "a@x.com", "sub": "g-new"}, db_session, monkeypatch)
    db_session.refresh(u)
    assert u.google_sub == "g-new"
    assert req.session["user_id"] == u.id


def test_active_sub_mismatch_rejected(db_session, monkeypatch):
    _mk(db_session, email="a@x.com", google_sub="g-old", status="active")
    with pytest.raises(HTTPException) as e:
        call_callback({"email": "a@x.com", "sub": "g-different"}, db_session, monkeypatch)
    assert e.value.status_code == 403


def test_active_sub_match_ok(db_session, monkeypatch):
    u = _mk(db_session, email="a@x.com", google_sub="g-ok", status="active")
    req, _ = call_callback({"email": "a@x.com", "sub": "g-ok"}, db_session, monkeypatch)
    assert req.session["user_id"] == u.id


def test_active_email_case_insensitive(db_session, monkeypatch):
    u = _mk(db_session, email="Mixed@X.com", google_sub="g-ok", status="active")
    req, _ = call_callback({"email": "mixed@x.com", "sub": "g-ok"}, db_session, monkeypatch)
    assert req.session["user_id"] == u.id


def test_unknown_status_rejected(db_session, monkeypatch):
    # `status` is an unconstrained VARCHAR; any value other than "active" must
    # not fall through to the login path.
    _mk(db_session, email="u@x.com", google_sub="g1", status="unexpected")
    with pytest.raises(HTTPException) as e:
        call_callback({"email": "u@x.com", "sub": "g1"}, db_session, monkeypatch)
    assert e.value.status_code == 403


def test_empty_status_rejected(db_session, monkeypatch):
    _mk(db_session, email="e@x.com", google_sub="g1", status="")
    with pytest.raises(HTTPException) as e:
        call_callback({"email": "e@x.com", "sub": "g1"}, db_session, monkeypatch)
    assert e.value.status_code == 403
