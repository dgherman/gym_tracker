"""Client Management feature tests: schema/migration, confirm route, admin API, admin page."""
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
    admin = models.User(google_sub="admin-sub", email="admin@x.com", role="admin", status="active")
    cli = models.User(google_sub="cli-sub", email="cli@x.com", role="client", status="active")
    db.add_all([admin, cli])
    db.commit()
    ids = {"admin": admin.id, "client": cli.id}
    db.close()
    main.app.dependency_overrides[main.get_db] = _override_get_db
    c = TestClient(main.app)
    c._ids = ids
    try:
        yield c
    finally:
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def client(app_client):
    """Unauthenticated TestClient with the DB override (for the public confirm route)."""
    return app_client


@pytest.fixture
def admin_client(app_client):
    _login(app_client, "admin@x.com")
    return app_client


@pytest.fixture
def client_client(app_client):
    _login(app_client, "cli@x.com")
    return app_client


# ---------------------------------------------------------------------------
# Task 1 — schema + cutover migration
# ---------------------------------------------------------------------------

def test_user_has_invite_columns(db_session):
    cols = {c.name for c in models.User.__table__.columns}
    assert {"status", "invite_token_hash", "invited_by_id",
            "invited_at", "confirmed_at"} <= cols
    assert models.User.__table__.c.google_sub.nullable is True


def test_pending_invite_row_roundtrips(db_session):
    u = models.User(email="p@example.com", google_sub=None,
                    status="pending", role="client")
    db_session.add(u)
    db_session.commit()
    got = db_session.query(models.User).filter_by(email="p@example.com").one()
    assert got.status == "pending"
    assert got.google_sub is None


def test_cutover_seeds_allowed_emails(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config

    db_path = tmp_path / "cutover.db"
    url = f"sqlite:///{db_path}"
    eng = create_engine(url)
    # Build the *current* schema, then pretend the DB is at the pre-migration head
    # so only the new client_management revision runs against it.
    Base.metadata.create_all(bind=eng)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (google_sub, email, email_verified, role, is_active, "
            "status, created_at, last_login_at) VALUES "
            "('g-existing', 'existing@x.com', 0, 'client', 1, 'pending', "
            "'2020-01-01 00:00:00', '2020-01-01 00:00:00')"
        ))

    monkeypatch.setenv("ALLOWED_EMAILS", "a@x.com, b@x.com, Existing@x.com, a@x.com, ")
    monkeypatch.setenv("SQLALCHEMY_DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", url)
        command.stamp(cfg, "pe01standalone")
        command.upgrade(cfg, "head")

        with eng.connect() as conn:
            rows = list(conn.execute(text(
                "SELECT email, google_sub, status FROM users ORDER BY email")))

        # downgrade is reversible: invite columns are dropped, seeded rows kept,
        # and a re-upgrade is idempotent.
        command.downgrade(cfg, "pe01standalone")
        after_down = sa_inspect(eng)
        down_cols = {c["name"] for c in after_down.get_columns("users")}
        assert "status" not in down_cols and "invite_token_hash" not in down_cols
        command.upgrade(cfg, "head")
    finally:
        get_settings.cache_clear()

    by_email = {r[0]: r for r in rows}
    assert by_email["existing@x.com"][2] == "active"          # pre-existing row flipped
    assert by_email["a@x.com"][1] is None                     # google_sub NULL
    assert by_email["a@x.com"][2] == "active"
    assert by_email["b@x.com"][1] is None
    assert by_email["b@x.com"][2] == "active"
    # case-insensitive dedupe: "Existing@x.com" did not create a second row
    assert sum(1 for r in rows if r[0] == "existing@x.com") == 1
    assert sum(1 for r in rows if r[0] == "a@x.com") == 1


# ---------------------------------------------------------------------------
# Task 5 — public invite confirmation route
# ---------------------------------------------------------------------------

def test_confirm_valid_token_activates(client, db_session):
    from gym_tracker.invites import hash_token
    db_session.add(models.User(email="c@x.com", google_sub=None, status="pending",
                               role="client", invite_token_hash=hash_token("RAW")))
    db_session.commit()
    r = client.get("/invite/confirm?token=RAW")
    assert r.status_code == 200
    u = db_session.query(models.User).filter_by(email="c@x.com").one()
    assert u.status == "active"
    assert u.confirmed_at is not None
    assert u.invite_token_hash is None


def test_confirm_unknown_token_shows_invalid(client):
    r = client.get("/invite/confirm?token=NOPE")
    assert r.status_code in (200, 410)
    assert "invalid" in r.text.lower() or "already" in r.text.lower()


def test_confirm_missing_token_shows_invalid(client):
    r = client.get("/invite/confirm")
    assert r.status_code in (200, 410)
    assert "invalid" in r.text.lower() or "already" in r.text.lower()


def test_confirm_reused_token_shows_invalid(client, db_session):
    db_session.add(models.User(email="c@x.com", google_sub=None, status="active",
                               role="client", invite_token_hash=None))
    db_session.commit()
    r = client.get("/invite/confirm?token=RAW")
    assert r.status_code in (200, 410)
    assert "invalid" in r.text.lower() or "already" in r.text.lower()


def test_confirm_disabled_after_issue_shows_invalid(client, db_session):
    from gym_tracker.invites import hash_token
    db_session.add(models.User(email="c@x.com", google_sub=None, status="disabled",
                               role="client", invite_token_hash=hash_token("RAW")))
    db_session.commit()
    r = client.get("/invite/confirm?token=RAW")
    assert r.status_code in (200, 410)
    u = db_session.query(models.User).filter_by(email="c@x.com").one()
    assert u.status == "disabled"
