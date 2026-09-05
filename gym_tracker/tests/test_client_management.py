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


# ---------------------------------------------------------------------------
# Task 6 — admin client API
# ---------------------------------------------------------------------------

@pytest.fixture
def no_email(monkeypatch):
    """Record send_invite_email calls instead of sending."""
    sent = []
    monkeypatch.setattr("main.send_invite_email",
                        lambda to, url, **kw: sent.append((to, url, kw)))
    return sent


def _one(db_session, email):
    return db_session.query(models.User).filter_by(email=email).one()


def test_create_client_makes_pending_and_sends(admin_client, db_session, no_email):
    r = admin_client.post("/api/admin/clients", json={"email": "New@X.com", "name": "N"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending"
    u = _one(db_session, "new@x.com")
    assert u.role == "client" and u.status == "pending"
    assert u.google_sub is None
    assert u.invite_token_hash and u.invited_by_id == admin_client._ids["admin"]
    assert u.invited_at is not None
    assert no_email and no_email[0][0] == "new@x.com"
    assert "/invite/confirm?token=" in no_email[0][1]


def test_create_duplicate_409(admin_client, db_session, no_email):
    assert admin_client.post("/api/admin/clients", json={"email": "dup@x.com"}).status_code == 201
    r = admin_client.post("/api/admin/clients", json={"email": "DUP@x.com"})
    assert r.status_code == 409


def test_create_bad_email_400(admin_client, no_email):
    assert admin_client.post("/api/admin/clients", json={"email": "  "}).status_code == 400
    assert admin_client.post("/api/admin/clients", json={"email": "nope"}).status_code == 400


def test_non_admin_forbidden(client_client, db_session, no_email):
    db_session.add(models.User(email="p@x.com", role="client", status="pending",
                               google_sub=None, invite_token_hash="h"))
    db_session.commit()
    pid = _one(db_session, "p@x.com").id
    assert client_client.post("/api/admin/clients", json={"email": "z@x.com"}).status_code == 403
    for suffix in ("resend", "disable", "reinvite"):
        r = client_client.post(f"/api/admin/clients/{pid}/{suffix}")
        assert r.status_code == 403, suffix


def test_resend_only_pending(admin_client, db_session, no_email):
    admin_client.post("/api/admin/clients", json={"email": "r@x.com"})
    u = _one(db_session, "r@x.com")
    old_hash = u.invite_token_hash
    r = admin_client.post(f"/api/admin/clients/{u.id}/resend")
    assert r.status_code == 200
    db_session.refresh(u)
    assert u.invite_token_hash and u.invite_token_hash != old_hash
    assert len(no_email) == 2  # create + resend

    u.status = "active"
    db_session.commit()
    assert admin_client.post(f"/api/admin/clients/{u.id}/resend").status_code == 409
    assert admin_client.post("/api/admin/clients/9999/resend").status_code == 404


def test_disable_sets_status_and_is_idempotent(admin_client, db_session, no_email):
    admin_client.post("/api/admin/clients", json={"email": "d@x.com"})
    u = _one(db_session, "d@x.com")
    assert admin_client.post(f"/api/admin/clients/{u.id}/disable").status_code == 200
    db_session.refresh(u)
    assert u.status == "disabled"
    # second call is a no-op success
    assert admin_client.post(f"/api/admin/clients/{u.id}/disable").status_code == 200
    assert admin_client.post("/api/admin/clients/9999/disable").status_code == 404


def test_reinvite_only_disabled(admin_client, db_session, no_email):
    admin_client.post("/api/admin/clients", json={"email": "ri@x.com"})
    u = _one(db_session, "ri@x.com")
    admin_client.post(f"/api/admin/clients/{u.id}/disable")
    db_session.refresh(u)
    u.confirmed_at = datetime_now()
    db_session.commit()
    r = admin_client.post(f"/api/admin/clients/{u.id}/reinvite")
    assert r.status_code == 200
    db_session.refresh(u)
    assert u.status == "pending"
    assert u.confirmed_at is None
    assert u.invite_token_hash
    assert no_email  # invite attempted again

    u.status = "active"
    db_session.commit()
    assert admin_client.post(f"/api/admin/clients/{u.id}/reinvite").status_code == 409


def test_create_email_failure_still_creates_with_warning(admin_client, db_session, monkeypatch):
    from gym_tracker.email import EmailSendError

    def boom(*a, **k):
        raise EmailSendError("smtp down")

    monkeypatch.setattr("main.send_invite_email", boom)
    r = admin_client.post("/api/admin/clients", json={"email": "f@x.com"})
    assert r.status_code == 201
    assert r.json().get("warning")
    assert _one(db_session, "f@x.com").status == "pending"


def datetime_now():
    from datetime import datetime
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# Task 7 — admin client management page
# ---------------------------------------------------------------------------

def test_admin_clients_page_lists_only_clients(admin_client, db_session):
    db_session.add_all([
        models.User(email="c1@x.com", role="client", status="active", google_sub="s1"),
        models.User(email="t1@x.com", role="trainer", status="active", google_sub="s2"),
        models.User(email="c2@x.com", role="client", status="pending", google_sub=None),
    ])
    db_session.commit()
    r = admin_client.get("/admin/clients")
    assert r.status_code == 200, r.text
    assert "c1@x.com" in r.text and "c2@x.com" in r.text
    assert "t1@x.com" not in r.text


def test_admin_clients_page_requires_admin(client_client):
    assert client_client.get("/admin/clients").status_code in (302, 303, 403)


def test_admin_index_links_to_clients(admin_client):
    r = admin_client.get("/admin")
    assert r.status_code == 200
    assert "/admin/clients" in r.text
    assert "Coming Soon" not in r.text.split("Client Management")[1].split("</div>")[0]
