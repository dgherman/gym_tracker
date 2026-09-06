"""Item 3 — first-login onboarding.

Covers the ``users.onboarded_at`` column + its backfill migration (Task 4) and
the trigger / dismiss-endpoint / replay-link behavior (Task 5).
"""
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect as sa_inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from gym_tracker.config import get_settings
from gym_tracker.database import Base
from gym_tracker import models

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def _override_get_db():
    d = TestSessionLocal()
    try:
        yield d
    finally:
        d.close()


def _login(c, email):
    os.environ["DEV_LOGIN_EMAIL"] = email
    r = c.get("/dev/login", follow_redirects=False)
    assert r.status_code in (200, 302, 303, 307), r.text


@pytest.fixture
def db_session():
    Base.metadata.create_all(bind=test_engine)
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def app_client():
    Base.metadata.create_all(bind=test_engine)
    db = TestSessionLocal()
    u = models.User(google_sub="ob-sub", email="ob@x.com", role="client", status="active")
    db.add(u)
    db.commit()
    ids = {"user": u.id}
    db.close()
    main.app.dependency_overrides[main.get_db] = _override_get_db
    c = TestClient(main.app)
    c._ids = ids
    try:
        yield c
    finally:
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=test_engine)


# ---------------------------------------------------------------------------
# Task 4 — ORM column
# ---------------------------------------------------------------------------

def test_user_has_onboarded_at_column_nullable(db_session):
    col = models.User.__table__.c.get("onboarded_at")
    assert col is not None
    assert col.nullable is True


def test_fresh_user_onboarded_at_is_none(db_session):
    u = models.User(google_sub="fresh-sub", email="fresh@x.com", role="client")
    db_session.add(u)
    db_session.commit()
    got = db_session.query(models.User).filter_by(email="fresh@x.com").one()
    assert got.onboarded_at is None


# ---------------------------------------------------------------------------
# Task 4 — migration: add column + backfill existing rows
# ---------------------------------------------------------------------------

_LEGACY_USERS_DDL = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    google_sub VARCHAR(255) NOT NULL UNIQUE,
    email VARCHAR(255),
    email_verified BOOLEAN NOT NULL DEFAULT 0,
    full_name VARCHAR(255),
    avatar_url VARCHAR(512),
    role VARCHAR(50) NOT NULL DEFAULT 'client',
    is_active BOOLEAN NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL,
    last_login_at DATETIME NOT NULL
)
"""


def test_migration_backfills_existing_rows_and_defaults_new_rows_null(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config

    url = f"sqlite:///{tmp_path / 'onboard.db'}"
    eng = create_engine(url)
    with eng.begin() as conn:
        conn.execute(text(_LEGACY_USERS_DDL))
        conn.execute(text("CREATE INDEX ix_users_email ON users (email)"))

    monkeypatch.setenv("ALLOWED_EMAILS", "")
    monkeypatch.setenv("SQLALCHEMY_DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", url)
        command.stamp(cfg, "pe01standalone")
        command.upgrade(cfg, "clientmgmt01")

        # A couple of rows exist at clientmgmt01, before onboarded_at ships.
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO users (google_sub, email, email_verified, role, is_active, "
                "status, created_at, last_login_at) VALUES "
                "('g1', 'a@x.com', 0, 'client', 1, 'active', '2020-01-01', '2020-01-01'), "
                "('g2', 'b@x.com', 0, 'client', 1, 'active', '2020-01-01', '2020-01-01')"
            ))

        command.upgrade(cfg, "head")

        insp = sa_inspect(eng)
        cols = {c["name"] for c in insp.get_columns("users")}
        assert "onboarded_at" in cols

        with eng.connect() as conn:
            pre = list(conn.execute(text(
                "SELECT onboarded_at FROM users WHERE google_sub IN ('g1', 'g2')")))
            assert len(pre) == 2
            assert all(r[0] is not None for r in pre)

            conn.execute(text(
                "INSERT INTO users (google_sub, email, email_verified, role, is_active, "
                "status, created_at, last_login_at) VALUES "
                "('g3', 'c@x.com', 0, 'client', 1, 'active', '2020-01-01', '2020-01-01')"
            ))
            conn.commit()
            new_val = conn.execute(text(
                "SELECT onboarded_at FROM users WHERE google_sub = 'g3'")).scalar()
            assert new_val is None

        command.downgrade(cfg, "clientmgmt01")
        down_cols = {c["name"] for c in sa_inspect(eng).get_columns("users")}
        assert "onboarded_at" not in down_cols
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Task 5 — trigger marker, complete endpoint, replay link
# ---------------------------------------------------------------------------

def _set_onboarded(user_id, value):
    db = TestSessionLocal()
    try:
        u = db.get(models.User, user_id)
        u.onboarded_at = value
        db.commit()
    finally:
        db.close()


def _get_onboarded(user_id):
    db = TestSessionLocal()
    try:
        return db.get(models.User, user_id).onboarded_at
    finally:
        db.close()


def test_index_shows_marker_when_never_onboarded(app_client):
    _login(app_client, "ob@x.com")
    body = app_client.get("/").text
    assert 'data-onboarding="1"' in body


def test_index_no_marker_once_onboarded(app_client):
    from datetime import datetime
    _set_onboarded(app_client._ids["user"], datetime(2026, 1, 1, 0, 0, 0))
    _login(app_client, "ob@x.com")
    body = app_client.get("/").text
    assert 'data-onboarding="1"' not in body


def test_index_tour_query_forces_marker_for_onboarded_user(app_client):
    from datetime import datetime
    _set_onboarded(app_client._ids["user"], datetime(2026, 1, 1, 0, 0, 0))
    _login(app_client, "ob@x.com")
    body = app_client.get("/?tour=1").text
    assert 'data-onboarding="1"' in body


def test_complete_endpoint_sets_onboarded_at_once_idempotent(app_client):
    _login(app_client, "ob@x.com")
    assert _get_onboarded(app_client._ids["user"]) is None

    r1 = app_client.post("/api/onboarding/complete")
    assert r1.status_code == 204
    first = _get_onboarded(app_client._ids["user"])
    assert first is not None

    r2 = app_client.post("/api/onboarding/complete")
    assert r2.status_code == 204
    assert _get_onboarded(app_client._ids["user"]) == first


def test_complete_endpoint_requires_auth(app_client):
    r = app_client.post("/api/onboarding/complete", headers={"accept": "application/json"})
    assert r.status_code == 401


def test_dev_login_does_not_set_onboarded_at(app_client):
    _login(app_client, "ob@x.com")
    assert _get_onboarded(app_client._ids["user"]) is None


def test_nav_has_show_tips_again_link_for_authenticated_user(app_client):
    _login(app_client, "ob@x.com")
    body = app_client.get("/").text
    assert 'href="/?tour=1"' in body
    assert "Show tips again" in body

