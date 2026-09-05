"""Client Management feature tests: schema/migration, confirm route, admin API, admin page."""
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect as sa_inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from gym_tracker.config import get_settings
from gym_tracker.database import Base
from gym_tracker import crud, models

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


# users table exactly as it stood *before* clientmgmt01 (no status / invite_*
# columns; google_sub NOT NULL). Lets the migration tests exercise the real
# "add 5 columns to a pre-migration table" path instead of stamping over the
# current ORM schema.
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


def _make_legacy_db(url):
    eng = create_engine(url)
    with eng.begin() as conn:
        conn.execute(text(_LEGACY_USERS_DDL))
        conn.execute(text("CREATE INDEX ix_users_email ON users (email)"))
    return eng


def _run_clientmgmt01(url):
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.stamp(cfg, "pe01standalone")
    command.upgrade(cfg, "head")


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


def test_cutover_adds_columns_and_seeds_allowed_emails(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config

    url = f"sqlite:///{tmp_path / 'cutover.db'}"
    eng = _make_legacy_db(url)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (google_sub, email, email_verified, role, is_active, "
            "created_at, last_login_at) VALUES "
            "('g-existing', 'existing@x.com', 0, 'client', 1, "
            "'2020-01-01 00:00:00', '2020-01-01 00:00:00')"
        ))
    # Pre-migration table really lacks the invite columns.
    assert "status" not in {c["name"] for c in sa_inspect(eng).get_columns("users")}

    monkeypatch.setenv("ALLOWED_EMAILS", "a@x.com, b@x.com, Existing@x.com, a@x.com, ")
    monkeypatch.setenv("SQLALCHEMY_DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", url)
        command.stamp(cfg, "pe01standalone")
        command.upgrade(cfg, "head")

        insp = sa_inspect(eng)
        cols = {c["name"] for c in insp.get_columns("users")}
        assert {"status", "invite_token_hash", "invited_by_id",
                "invited_at", "confirmed_at"} <= cols
        # N3: the self-referential FK exists on SQLite too (schema parity with MySQL)
        assert any(fk["name"] == "fk_users_invited_by" for fk in insp.get_foreign_keys("users"))

        with eng.connect() as conn:
            rows = list(conn.execute(text(
                "SELECT email, google_sub, status, confirmed_at FROM users ORDER BY email")))

        # downgrade drops the invite columns; seeded rows are kept; re-upgrade works.
        command.downgrade(cfg, "pe01standalone")
        down_cols = {c["name"] for c in sa_inspect(eng).get_columns("users")}
        assert "status" not in down_cols and "invite_token_hash" not in down_cols
        command.upgrade(cfg, "head")
    finally:
        get_settings.cache_clear()

    by_email = {r[0]: r for r in rows}
    assert by_email["existing@x.com"][2] == "active"          # pre-existing row flipped
    assert by_email["existing@x.com"][3] is not None          # confirmed_at backfilled
    assert by_email["a@x.com"][1] is None                     # google_sub NULL
    assert by_email["a@x.com"][2] == "active"
    assert by_email["b@x.com"][1] is None
    assert by_email["b@x.com"][2] == "active"
    # case-insensitive dedupe: "Existing@x.com" did not create a second row
    assert sum(1 for r in rows if r[0] == "existing@x.com") == 1
    assert sum(1 for r in rows if r[0] == "a@x.com") == 1


def test_migration_aborts_on_ci_duplicate_emails(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config

    url = f"sqlite:///{tmp_path / 'dups.db'}"
    eng = _make_legacy_db(url)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (google_sub, email, email_verified, role, is_active, "
            "created_at, last_login_at) VALUES "
            "('g1', 'Dup@X.com', 0, 'client', 1, '2020-01-01', '2020-01-01'), "
            "('g2', 'dup@x.com', 0, 'client', 1, '2020-01-01', '2020-01-01')"
        ))
    monkeypatch.setenv("ALLOWED_EMAILS", "")
    monkeypatch.setenv("SQLALCHEMY_DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", url)
        command.stamp(cfg, "pe01standalone")
        with pytest.raises(RuntimeError) as e:
            command.upgrade(cfg, "head")
        assert "dup@x.com" in str(e.value)
        # rows untouched — migration must not delete or merge
        with eng.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM users")).scalar() == 2
    finally:
        get_settings.cache_clear()


def test_migration_aborts_on_empty_string_duplicate_emails(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config

    url = f"sqlite:///{tmp_path / 'blankdups.db'}"
    eng = _make_legacy_db(url)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (google_sub, email, email_verified, role, is_active, "
            "created_at, last_login_at) VALUES "
            "('g1', '', 0, 'client', 1, '2020-01-01', '2020-01-01'), "
            "('g2', '', 0, 'client', 1, '2020-01-01', '2020-01-01')"
        ))
    monkeypatch.setenv("ALLOWED_EMAILS", "")
    monkeypatch.setenv("SQLALCHEMY_DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", url)
        command.stamp(cfg, "pe01standalone")
        with pytest.raises(RuntimeError) as e:
            command.upgrade(cfg, "head")
        assert "''" in str(e.value)  # the empty string is named as the offender
        # aborted BEFORE any DDL — no invite columns were added, rows intact
        cols = {c["name"] for c in sa_inspect(eng).get_columns("users")}
        assert "status" not in cols and "invite_token_hash" not in cols
        with eng.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM users")).scalar() == 2
    finally:
        get_settings.cache_clear()


def test_migration_allows_repeated_null_emails(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'nulls.db'}"
    eng = _make_legacy_db(url)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (google_sub, email, email_verified, role, is_active, "
            "created_at, last_login_at) VALUES "
            "('g1', NULL, 0, 'client', 1, '2020-01-01', '2020-01-01'), "
            "('g2', NULL, 0, 'client', 1, '2020-01-01', '2020-01-01')"
        ))
    monkeypatch.setenv("ALLOWED_EMAILS", "")
    monkeypatch.setenv("SQLALCHEMY_DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        _run_clientmgmt01(url)  # must NOT raise
        cols = {c["name"] for c in sa_inspect(eng).get_columns("users")}
        assert "status" in cols
    finally:
        get_settings.cache_clear()


def test_migration_enforces_ci_email_uniqueness(tmp_path, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    url = f"sqlite:///{tmp_path / 'ci.db'}"
    eng = _make_legacy_db(url)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (google_sub, email, email_verified, role, is_active, "
            "created_at, last_login_at) VALUES "
            "('g1', 'a@x.com', 0, 'client', 1, '2020-01-01', '2020-01-01')"
        ))
    monkeypatch.setenv("ALLOWED_EMAILS", "")
    monkeypatch.setenv("SQLALCHEMY_DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        _run_clientmgmt01(url)
        with pytest.raises(IntegrityError):
            with eng.begin() as conn:
                conn.execute(text(
                    "INSERT INTO users (google_sub, email, email_verified, role, is_active, "
                    "status, created_at, last_login_at) VALUES "
                    "('g2', 'A@X.com', 0, 'client', 1, 'pending', '2020-01-01', '2020-01-01')"
                ))
        # multiple NULL emails are still fine
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO users (google_sub, email, email_verified, role, is_active, "
                "status, created_at, last_login_at) VALUES "
                "('g3', NULL, 0, 'client', 1, 'pending', '2020-01-01', '2020-01-01'), "
                "('g4', NULL, 0, 'client', 1, 'pending', '2020-01-01', '2020-01-01')"
            ))
    finally:
        get_settings.cache_clear()


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
    row = _one(db_session, "f@x.com")
    assert row.status == "pending"
    # store-hash-then-send: the token is committed even though the email failed
    assert row.invite_token_hash is not None


def test_create_ci_duplicate_returns_409_not_500(admin_client, db_session, no_email):
    db_session.add_all([
        models.User(email="Same@Example.com", role="client", status="pending", google_sub=None),
        models.User(email="same@example.com", role="client", status="pending", google_sub=None),
    ])
    db_session.commit()
    r = admin_client.post("/api/admin/clients", json={"email": "SAME@example.com"})
    assert r.status_code == 409


def test_invite_row_is_committed_before_email_is_sent(admin_client, db_session, monkeypatch):
    # B4: store-hash-then-send. At send time the row+token must already be
    # visible in an independent session (i.e. committed).
    seen = {}

    def capture(to, url, **kw):
        probe = TestSessionLocal()
        try:
            row = probe.query(models.User).filter_by(email=to).one_or_none()
            seen["persisted"] = row is not None
            seen["hash"] = bool(row and row.invite_token_hash)
        finally:
            probe.close()

    monkeypatch.setattr("main.send_invite_email", capture)
    r = admin_client.post("/api/admin/clients", json={"email": "commit1st@x.com"})
    assert r.status_code == 201
    assert seen == {"persisted": True, "hash": True}


def test_resend_row_committed_before_email(admin_client, db_session, monkeypatch):
    monkeypatch.setattr("main.send_invite_email", lambda *a, **k: None)
    admin_client.post("/api/admin/clients", json={"email": "rs@x.com"})
    u = _one(db_session, "rs@x.com")
    old_hash = u.invite_token_hash

    seen = {}

    def capture(to, url, **kw):
        probe = TestSessionLocal()
        try:
            row = probe.query(models.User).filter_by(email=to).one()
            seen["hash_rotated"] = row.invite_token_hash not in (None, old_hash)
        finally:
            probe.close()

    monkeypatch.setattr("main.send_invite_email", capture)
    r = admin_client.post(f"/api/admin/clients/{u.id}/resend")
    assert r.status_code == 200
    assert seen.get("hash_rotated") is True


def test_reinvite_email_failure_keeps_committed_pending_row(admin_client, db_session, monkeypatch):
    from gym_tracker.email import EmailSendError

    monkeypatch.setattr("main.send_invite_email", lambda *a, **k: None)
    admin_client.post("/api/admin/clients", json={"email": "rv@x.com"})
    u = _one(db_session, "rv@x.com")
    admin_client.post(f"/api/admin/clients/{u.id}/disable")

    def boom(*a, **k):
        raise EmailSendError("smtp down")

    monkeypatch.setattr("main.send_invite_email", boom)
    r = admin_client.post(f"/api/admin/clients/{u.id}/reinvite")
    assert r.status_code == 200
    assert r.json().get("warning")
    db_session.expire_all()
    row = _one(db_session, "rv@x.com")
    assert row.status == "pending"
    assert row.confirmed_at is None
    assert row.invite_token_hash is not None


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


# ---------------------------------------------------------------------------
# Review B1 — /admin/clients ordering must be MySQL-portable (no NULLS LAST)
# ---------------------------------------------------------------------------

def test_clients_ordering_is_mysql_portable():
    import main
    from sqlalchemy.dialects import mysql

    db = TestSessionLocal()
    try:
        stmt = str(main._clients_ordered(db).statement.compile(dialect=mysql.dialect()))
    finally:
        db.close()
    assert "NULLS LAST" not in stmt.upper()
    assert "NULLS FIRST" not in stmt.upper()


# ---------------------------------------------------------------------------
# Review N4 — build_confirm_url strips a trailing slash from either base
# ---------------------------------------------------------------------------

def test_build_confirm_url_configured_base_no_double_slash(monkeypatch):
    import main
    monkeypatch.setattr(main.settings, "APP_BASE_URL", "https://h/")
    req = SimpleNamespace(base_url="http://ignored.example/")
    url = main.build_confirm_url(req, "TOK")
    assert url == "https://h/invite/confirm?token=TOK"


def test_build_confirm_url_request_fallback_no_double_slash(monkeypatch):
    import main
    monkeypatch.setattr(main.settings, "APP_BASE_URL", "")
    req = SimpleNamespace(base_url="http://testserver/")
    url = main.build_confirm_url(req, "TOK")
    assert url == "http://testserver/invite/confirm?token=TOK"


# ---------------------------------------------------------------------------
# Review R2-2 — partner linking must be case-insensitive and fail closed
# ---------------------------------------------------------------------------

def test_resolve_partner_ci_match_links(db_session):
    db_session.add(models.User(email="Mixed@X.com", role="client", status="active",
                               google_sub="pm1"))
    db_session.commit()
    uid = db_session.query(models.User).filter_by(email="Mixed@X.com").one().id
    assert crud._resolve_partner(db_session, "mixed@x.com") == uid


def test_resolve_partner_no_match_returns_none(db_session):
    assert crud._resolve_partner(db_session, "nobody@x.com") is None
    assert crud._resolve_partner(db_session, "") is None
    assert crud._resolve_partner(db_session, None) is None


def test_resolve_partner_ambiguous_does_not_link(db_session):
    db_session.add_all([
        models.User(email="Dirty@X.com", role="client", status="active", google_sub="pa1"),
        models.User(email="dirty@x.com", role="client", status="active", google_sub="pa2"),
    ])
    db_session.commit()
    # two case variants -> fail closed, do not pick an arbitrary row
    assert crud._resolve_partner(db_session, "DIRTY@x.com") is None
