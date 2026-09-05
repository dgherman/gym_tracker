# Client Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `ALLOWED_EMAILS` env-var login allowlist with an admin-managed Client Management section that invites clients by email, confirms them via a tokenized link, and gates Google sign-in on confirmed status.

**Architecture:** Extend the existing `users` table with invite/status columns (Model A). A pending invite is a `users` row with `google_sub = NULL` and `status = 'pending'`. The Google OAuth callback swaps its env-var check for a DB decision table. A new `gym_tracker/email.py` sends the invite via Resend behind a swappable transport. A new `/admin/clients` Jinja2 page plus `/api/admin/clients*` endpoints drive add/resend/disable/reinvite. A public `/invite/confirm` route flips `pending -> active`.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, Alembic, Jinja2, Bootstrap 5, `httpx`, pytest (in-memory SQLite).

**Spec:** `docs/superpowers/specs/2026-09-05-client-management-design.md` — read it in full before starting. The plan argues from the spec; both travel together.

## Global Constraints

- Python 3.11; FastAPI; SQLAlchemy ORM; Alembic for all schema changes. No raw DDL outside a migration.
- Tests: `pytest --maxfail=1 --disable-warnings -q` must pass. In-memory SQLite via `gym_tracker/tests/db_test_utils.py` (`StaticPool`).
- `status` values are exactly the strings `pending`, `active`, `disabled`. No enum type; `VARCHAR(20)`.
- `invite_token_hash` stores the **SHA-256 hex digest** of the raw token. The raw token appears only in the emailed URL and is never persisted or logged when `EMAIL_ENABLED=true`.
- Raw token generation: `secrets.token_urlsafe(32)`.
- Invite tokens have **no expiry**.
- New env vars and defaults (read via `os.getenv` like existing settings in `gym_tracker/config.py`):
  - `EMAIL_ENABLED` default `false`
  - `EMAIL_PROVIDER` default `resend`
  - `RESEND_API_KEY` default `""`
  - `EMAIL_FROM` default `Gym Tracker <admin@gym.x-mas.ro>`
  - `EMAIL_REPLY_TO` default `dumitru@x-mas.ro`
  - `APP_BASE_URL` default `""` (fall back to `request.base_url` when empty)
- `EMAIL_ENABLED=false` MUST NOT make any outbound HTTP call; it logs the confirm URL at `INFO` instead.
- Resend endpoint: `POST https://api.resend.com/emails`, header `Authorization: Bearer <RESEND_API_KEY>`, JSON body keys `from`, `to`, `reply_to`, `subject`, `text`, `html`.
- All `/admin/clients` page and `/api/admin/clients*` routes use `Depends(require_admin)` (`main.py:95-102`).
- `/invite/confirm` is public (no auth dependency).
- Only `users` rows with `role = 'client'` appear in the Client Management table.
- Soft-disable only. Never delete a `users` row or any dependent purchases/sessions/progress rows.
- `ALLOWED_EMAILS` and `Settings.allowed_emails_set` are removed only in Task 4 (after the callback no longer needs them), not before.
- Follow existing admin API + template patterns: `main.py:511-536, 703-728, 757-823`, `templates/admin/trainers.html`.
- Frequent commits: every task ends committed and green. Conventional Commit messages.

---

## File Structure

| File | Responsibility | Tasks |
|------|----------------|-------|
| `gym_tracker/models.py` | Add `status`, `invite_token_hash`, `invited_by_id`, `invited_at`, `confirmed_at` to `User`; make `google_sub` nullable | 1 |
| `alembic/versions/<rev>_client_management.py` | Schema change + data cutover (existing users -> active, `ALLOWED_EMAILS` -> rows) | 1 |
| `gym_tracker/config.py` | Add email settings (Task 2); remove `ALLOWED_EMAILS` / `allowed_emails_set` (Task 4) | 2, 4 |
| `gym_tracker/email.py` (new) | `send_invite_email(...)`, `EmailSendError`, Resend transport, `EMAIL_ENABLED` gate | 3 |
| `gym_tracker/invites.py` (new) | Token helpers: `generate_token()`, `hash_token(raw)`; shared by API + confirm route | 3 |
| `gym_tracker/auth.py` | Replace `ALLOWED_EMAILS` check with DB decision table; first-login `google_sub` backfill | 4 |
| `main.py` | `/invite/confirm` route (Task 5); `/admin/clients` page + `/api/admin/clients*` API (Tasks 6-7) | 5, 6, 7 |
| `templates/invite_confirmed.html`, `templates/invite_invalid.html` (new) | Public confirm result pages | 5 |
| `templates/admin/clients.html` (new) | Client Management table + add form + row actions | 7 |
| `templates/admin/index.html` | Replace "Client management — Coming Soon" tile with a link to `/admin/clients` | 7 |
| `requirements.txt` | Pin `httpx` explicitly if not already pinned | 3 |
| `ARCHITECTURE.md`, `.env.example` (if present) | Document new env vars; drop `ALLOWED_EMAILS` refs (`ARCHITECTURE.md:159,246`) | 8 |
| `gym_tracker/tests/test_client_management.py` (new) | Migration, confirm, admin API, UI gating tests | 1, 5, 6, 7 |
| `gym_tracker/tests/test_auth_callback.py` (new) | Callback decision-table matrix | 4 |
| `gym_tracker/tests/test_email.py` (new) | Email module: logging path + mocked httpx | 3 |

---

## Task 1: Schema + cutover migration

**Files:**
- Modify: `gym_tracker/models.py` (class `User`, ~lines 23-38)
- Create: `alembic/versions/<rev>_client_management.py`
- Test: `gym_tracker/tests/test_client_management.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `User.status: str` (`"pending" | "active" | "disabled"`, default `"active"`)
  - `User.invite_token_hash: str | None`
  - `User.invited_by_id: int | None`
  - `User.invited_at: datetime | None`
  - `User.confirmed_at: datetime | None`
  - `User.google_sub: str | None` (now nullable, still unique)
  - Alembic revision id for `client_management`, down_revision = current head.

- [ ] **Step 1: Write the failing migration test**

In `gym_tracker/tests/test_client_management.py`, using the existing SQLite test engine/fixtures from `db_test_utils`:

```python
def test_user_has_invite_columns(db_session):
    from gym_tracker import models
    cols = {c.name for c in models.User.__table__.columns}
    assert {"status", "invite_token_hash", "invited_by_id",
            "invited_at", "confirmed_at"} <= cols
    assert models.User.__table__.c.google_sub.nullable is True

def test_pending_invite_row_roundtrips(db_session):
    from gym_tracker import models
    u = models.User(email="p@example.com", google_sub=None,
                    status="pending", role="client")
    db_session.add(u); db_session.commit()
    got = db_session.query(models.User).filter_by(email="p@example.com").one()
    assert got.status == "pending"
    assert got.google_sub is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest gym_tracker/tests/test_client_management.py -v`
Expected: FAIL — columns missing / `google_sub` NOT NULL.

- [ ] **Step 3: Add the ORM columns**

In `gym_tracker/models.py` class `User`: make `google_sub` `nullable=True` (keep `unique=True, index=True`). Add:
- `status = Column(String(20), nullable=False, server_default="active")`
- `invite_token_hash = Column(String(64), nullable=True, unique=True)`
- `invited_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)`
- `invited_at = Column(DateTime, nullable=True)`
- `confirmed_at = Column(DateTime, nullable=True)`

Match the import style and column conventions already in the file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest gym_tracker/tests/test_client_management.py -v`
Expected: PASS.

- [ ] **Step 5: Generate the Alembic revision**

Run: `alembic revision -m "client_management"` (do NOT use `--autogenerate`; write it by hand for control over the data step). Set `down_revision` to the current head.

- [ ] **Step 6: Write `upgrade()`**

Order:
1. `op.add_column("users", ...)` for the five new columns (`status` with `server_default="active"`, others nullable).
2. `op.alter_column("users", "google_sub", existing_type=sa.String(255), nullable=True)`.
3. `op.create_unique_constraint("uq_users_invite_token_hash", "users", ["invite_token_hash"])` (skip if the column-level `unique=True` already emits it on autogen-free path; create explicitly here).
4. `op.create_foreign_key("fk_users_invited_by", "users", "users", ["invited_by_id"], ["id"], ondelete="SET NULL")`.
5. Data step, via `op.get_bind()` and SQLAlchemy Core:
   - `UPDATE users SET status='active' WHERE status IS NULL OR status=''`
   - `UPDATE users SET confirmed_at = COALESCE(created_at, :now) WHERE confirmed_at IS NULL` (use `now = datetime.utcnow()`; if `users` has no `created_at`, use `:now` only).
   - Read `os.getenv("ALLOWED_EMAILS", "")`; for each `e.strip().lower()` split on `,` that is non-empty AND not already present (case-insensitive `SELECT 1 FROM users WHERE lower(email)=:e`): `INSERT INTO users (email, google_sub, status, role, invited_at, confirmed_at) VALUES (:e, NULL, 'active', 'client', :now, :now)`.

- [ ] **Step 7: Write `downgrade()`**

Drop FK `fk_users_invited_by`, drop unique constraint `uq_users_invite_token_hash`, `alter_column` `google_sub` back to `nullable=False` (guard: only safe if no NULL rows — acceptable for dev rollback), `op.drop_column` the five columns. Rows inserted from `ALLOWED_EMAILS` are intentionally left (data-preserving downgrade); document this in a comment.

- [ ] **Step 8: Write the migration data-step test**

```python
def test_cutover_seeds_allowed_emails(tmp_path, monkeypatch):
    # Build a fresh SQLite DB, stamp base, set ALLOWED_EMAILS, upgrade head,
    # assert: existing pre-seeded row -> status 'active';
    #         "a@x.com","b@x.com" rows created with status='active', google_sub IS NULL;
    #         an entry equal to an existing email does NOT duplicate.
```

Use Alembic's `command.upgrade` against a temp SQLite URL with `monkeypatch.setenv("ALLOWED_EMAILS", "a@x.com, b@x.com, existing@x.com")`. Pre-insert `existing@x.com` before upgrade.

- [ ] **Step 9: Run the full migration test + suite**

Run: `python -m pytest gym_tracker/tests/test_client_management.py -v && python -m pytest --maxfail=1 --disable-warnings -q`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add gym_tracker/models.py alembic/versions gym_tracker/tests/test_client_management.py
git commit -m "feat(db): add client invite columns and cutover migration"
```

---

## Task 2: Email configuration settings

**Files:**
- Modify: `gym_tracker/config.py` (add to the `Settings` loader, ~lines 5-56)
- Test: `gym_tracker/tests/test_email.py`

**Interfaces:**
- Consumes: nothing.
- Produces on `Settings`:
  - `EMAIL_ENABLED: bool`
  - `EMAIL_PROVIDER: str`
  - `RESEND_API_KEY: str`
  - `EMAIL_FROM: str`
  - `EMAIL_REPLY_TO: str`
  - `APP_BASE_URL: str`

- [ ] **Step 1: Write the failing test**

```python
def test_email_settings_defaults(monkeypatch):
    for k in ["EMAIL_ENABLED","EMAIL_PROVIDER","RESEND_API_KEY",
              "EMAIL_FROM","EMAIL_REPLY_TO","APP_BASE_URL"]:
        monkeypatch.delenv(k, raising=False)
    from gym_tracker.config import Settings
    s = Settings()
    assert s.EMAIL_ENABLED is False
    assert s.EMAIL_PROVIDER == "resend"
    assert s.EMAIL_FROM == "Gym Tracker <admin@gym.x-mas.ro>"
    assert s.EMAIL_REPLY_TO == "dumitru@x-mas.ro"
    assert s.APP_BASE_URL == ""

def test_email_enabled_truthy(monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    from gym_tracker.config import Settings
    assert Settings().EMAIL_ENABLED is True
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest gym_tracker/tests/test_email.py -v`
Expected: FAIL — attributes missing.

- [ ] **Step 3: Add the settings**

In `gym_tracker/config.py`, alongside the existing `os.getenv` reads. Parse `EMAIL_ENABLED` with a helper: `os.getenv("EMAIL_ENABLED","false").strip().lower() in {"1","true","yes","on"}`. Others are plain string reads with the defaults from Global Constraints.

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest gym_tracker/tests/test_email.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gym_tracker/config.py gym_tracker/tests/test_email.py
git commit -m "feat(config): add email provider settings"
```

---

## Task 3: Email module + token helpers

**Files:**
- Create: `gym_tracker/invites.py`
- Create: `gym_tracker/email.py`
- Modify: `requirements.txt` (pin `httpx` if not already pinned)
- Test: `gym_tracker/tests/test_email.py`

**Interfaces:**
- Consumes: `Settings` email fields (Task 2).
- Produces:
  - `gym_tracker/invites.py`:
    - `generate_token() -> str` — `secrets.token_urlsafe(32)`
    - `hash_token(raw: str) -> str` — `hashlib.sha256(raw.encode()).hexdigest()`
  - `gym_tracker/email.py`:
    - `class EmailSendError(Exception)`
    - `send_invite_email(to_email: str, confirm_url: str, *, to_name: str | None = None) -> None`

- [ ] **Step 1: Write failing tests for token helpers**

```python
def test_hash_token_is_sha256_hex():
    from gym_tracker.invites import hash_token
    assert hash_token("abc") == \
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"

def test_generate_token_unique_and_urlsafe():
    from gym_tracker.invites import generate_token
    a, b = generate_token(), generate_token()
    assert a != b and len(a) >= 32
```

- [ ] **Step 2: Run — fail**

Run: `python -m pytest gym_tracker/tests/test_email.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `gym_tracker/invites.py`**

Two small functions per the Interfaces block. No other logic.

- [ ] **Step 4: Run — pass**

Run: `python -m pytest gym_tracker/tests/test_email.py -v`
Expected: PASS for the two token tests.

- [ ] **Step 5: Write failing tests for `send_invite_email`**

```python
def test_disabled_logs_url_no_http(monkeypatch, caplog):
    monkeypatch.setenv("EMAIL_ENABLED", "false")
    import httpx
    def boom(*a, **k): raise AssertionError("no HTTP when disabled")
    monkeypatch.setattr(httpx, "post", boom, raising=False)
    from gym_tracker.email import send_invite_email
    with caplog.at_level("INFO"):
        send_invite_email("c@example.com", "https://h/invite/confirm?token=RAW")
    assert "https://h/invite/confirm?token=RAW" in caplog.text

def test_enabled_posts_to_resend(monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    calls = {}
    class FakeResp:
        status_code = 200
        def json(self): return {"id": "e1"}
    def fake_post(url, headers=None, json=None, timeout=None):
        calls.update(url=url, headers=headers, json=json); return FakeResp()
    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)
    from gym_tracker.email import send_invite_email
    send_invite_email("c@example.com", "https://h/x?token=RAW", to_name="Cee")
    assert calls["url"] == "https://api.resend.com/emails"
    assert calls["headers"]["Authorization"] == "Bearer re_test"
    body = calls["json"]
    assert body["from"] == "Gym Tracker <admin@gym.x-mas.ro>"
    assert body["reply_to"] == "dumitru@x-mas.ro"
    assert body["to"] == ["c@example.com"]
    assert "https://h/x?token=RAW" in body["text"]
    assert "https://h/x?token=RAW" in body["html"]

def test_enabled_non_2xx_raises(monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    class FakeResp:
        status_code = 422
        text = "bad"
        def json(self): return {"message": "bad"}
    import httpx
    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp())
    from gym_tracker.email import send_invite_email, EmailSendError
    import pytest
    with pytest.raises(EmailSendError):
        send_invite_email("c@example.com", "https://h/x?token=RAW")
```

- [ ] **Step 6: Run — fail**

Run: `python -m pytest gym_tracker/tests/test_email.py -v`
Expected: FAIL — `send_invite_email` missing.

- [ ] **Step 7: Implement `gym_tracker/email.py`**

- Build `subject`, `text`, `html`. Copy requirements (spec 5.7): short, states an admin invited them to the gym tracker, one CTA link = `confirm_url`, no tracking pixels, plain. Greet with `to_name` when provided.
- Fresh `Settings()` read at call time.
- If not `EMAIL_ENABLED`: `logger.info("invite email (disabled) for %s: %s", to_email, confirm_url)` and return.
- Else dispatch by `EMAIL_PROVIDER`. `"resend"` -> `httpx.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"}, json={"from": EMAIL_FROM, "to": [to_email], "reply_to": EMAIL_REPLY_TO, "subject": subject, "text": text, "html": html}, timeout=10)`.
- Non-2xx or `httpx.HTTPError` -> raise `EmailSendError` with a short message (do not include the raw token in the message).
- Unknown provider -> `EmailSendError`.

- [ ] **Step 8: Pin `httpx`**

If `requirements.txt` lacks an explicit `httpx==` line, add one matching the version already resolved in the environment (`python -c "import httpx; print(httpx.__version__)"`).

- [ ] **Step 9: Run — pass + full suite**

Run: `python -m pytest gym_tracker/tests/test_email.py -v && python -m pytest --maxfail=1 --disable-warnings -q`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add gym_tracker/invites.py gym_tracker/email.py requirements.txt gym_tracker/tests/test_email.py
git commit -m "feat(email): invite email via Resend with disabled log-only mode"
```

---

## Task 4: Auth callback decision table

**Files:**
- Modify: `gym_tracker/auth.py` (replace the `ALLOWED_EMAILS` check at ~lines 69-72; adjust the user lookup/upsert at ~lines 74-116)
- Modify: `gym_tracker/config.py` (remove `ALLOWED_EMAILS` read + `allowed_emails_set`)
- Test: `gym_tracker/tests/test_auth_callback.py`

**Interfaces:**
- Consumes: `User.status`, `User.google_sub` (Task 1).
- Produces: callback behavior per spec 5.3 table. No new public symbols.

- [ ] **Step 1: Write the failing matrix tests**

In `gym_tracker/tests/test_auth_callback.py`, stub the OIDC token exchange. Factor a helper `call_callback(claims, db)` that patches `oauth.google.authorize_access_token` (and any `parse_id_token`/userinfo call) to return `claims` with `email` and `sub`, then invokes the `/auth/callback` handler with a test `Request` + `db` session.

```python
import pytest

def _mk(db, **kw):
    from gym_tracker import models
    u = models.User(**{"role": "client", **kw})
    db.add(u); db.commit(); return u

def test_no_invite_rejected(db_session):
    with pytest.raises(HTTPException) as e:
        call_callback({"email": "x@x.com", "sub": "g1"}, db_session)
    assert e.value.status_code == 403

def test_pending_rejected(db_session):
    _mk(db_session, email="p@x.com", google_sub=None, status="pending")
    with pytest.raises(HTTPException) as e:
        call_callback({"email": "p@x.com", "sub": "g1"}, db_session)
    assert e.value.status_code == 403

def test_disabled_rejected(db_session):
    _mk(db_session, email="d@x.com", google_sub="g9", status="disabled")
    with pytest.raises(HTTPException) as e:
        call_callback({"email": "d@x.com", "sub": "g9"}, db_session)
    assert e.value.status_code == 403

def test_active_null_sub_backfills(db_session):
    u = _mk(db_session, email="a@x.com", google_sub=None, status="active")
    call_callback({"email": "a@x.com", "sub": "g-new"}, db_session)
    db_session.refresh(u)
    assert u.google_sub == "g-new"

def test_active_sub_mismatch_rejected(db_session):
    _mk(db_session, email="a@x.com", google_sub="g-old", status="active")
    with pytest.raises(HTTPException) as e:
        call_callback({"email": "a@x.com", "sub": "g-different"}, db_session)
    assert e.value.status_code == 403

def test_active_sub_match_ok(db_session):
    _mk(db_session, email="a@x.com", google_sub="g-ok", status="active")
    call_callback({"email": "a@x.com", "sub": "g-ok"}, db_session)  # no raise
```

- [ ] **Step 2: Run — fail**

Run: `python -m pytest gym_tracker/tests/test_auth_callback.py -v`
Expected: FAIL (current code checks `ALLOWED_EMAILS`, returns wrong codes / creates rows).

- [ ] **Step 3: Rewrite the check**

In `gym_tracker/auth.py`, after `email` and `google_sub` (`sub`) are known and before the session is set:
1. `user = db.query(models.User).filter(func.lower(models.User.email) == email.lower()).one_or_none()`
2. Apply the spec 5.3 table:
   - `user is None` -> `raise HTTPException(403, "This email has not been invited.")`
   - `user.status == "pending"` -> `raise HTTPException(403, "Please confirm your invitation from the email we sent you before signing in.")`
   - `user.status == "disabled"` -> `raise HTTPException(403, "Access for this account has been revoked.")`
   - `user.status == "active"` and `user.google_sub is None` -> set `user.google_sub = google_sub`, fill profile fields from claims, `db.commit()`
   - `user.status == "active"` and `user.google_sub` and `user.google_sub != google_sub` -> `raise HTTPException(403, "This email is linked to a different Google account.")`
   - else -> proceed
3. Remove the old `google_sub`-first branch that *creates* a new user for an unknown `google_sub`; the email lookup now owns eligibility. Returning users still match by email; keep updating profile fields + `request.session["user_id"] = user.id`.
4. Preserve partner-matching/linking calls (`gym_tracker/auth.py:101-110`).

- [ ] **Step 4: Remove the allowlist config**

Delete the `ALLOWED_EMAILS` `os.getenv` line and the `allowed_emails_set` property from `gym_tracker/config.py`. Grep for other references: `grep -rn "ALLOWED_EMAILS\|allowed_emails_set" gym_tracker main.py` — remove/adjust all hits (there should be only the one enforcement site, now replaced).

- [ ] **Step 5: Run — pass**

Run: `python -m pytest gym_tracker/tests/test_auth_callback.py -v`
Expected: PASS.

- [ ] **Step 6: Full suite**

Run: `python -m pytest --maxfail=1 --disable-warnings -q`
Expected: PASS. Fix any test that referenced `ALLOWED_EMAILS` (update it to seed a `users` row instead).

- [ ] **Step 7: Commit**

```bash
git add gym_tracker/auth.py gym_tracker/config.py gym_tracker/tests
git commit -m "feat(auth): gate login on client invite status instead of ALLOWED_EMAILS"
```

---

## Task 5: Invite confirmation route + pages

**Files:**
- Modify: `main.py` (add `GET /invite/confirm`)
- Create: `templates/invite_confirmed.html`, `templates/invite_invalid.html`
- Test: `gym_tracker/tests/test_client_management.py`

**Interfaces:**
- Consumes: `hash_token` (Task 3), `User.status/invite_token_hash/confirmed_at` (Task 1).
- Produces: route `GET /invite/confirm?token=<raw>`.

- [ ] **Step 1: Write failing tests**

```python
def test_confirm_valid_token_activates(client, db_session):
    from gym_tracker import models
    from gym_tracker.invites import hash_token
    db_session.add(models.User(email="c@x.com", google_sub=None,
        status="pending", role="client", invite_token_hash=hash_token("RAW")))
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

def test_confirm_reused_token_shows_invalid(client, db_session):
    from gym_tracker import models
    from gym_tracker.invites import hash_token
    db_session.add(models.User(email="c@x.com", google_sub=None,
        status="active", role="client", invite_token_hash=None))
    db_session.commit()
    r = client.get("/invite/confirm?token=RAW")
    assert r.status_code in (200, 410)
```

- [ ] **Step 2: Run — fail**

Run: `python -m pytest gym_tracker/tests/test_client_management.py -k confirm -v`
Expected: FAIL — 404 (route missing).

- [ ] **Step 3: Implement the route**

In `main.py`, mirroring how other public/template routes are declared:
- `token = request.query_params.get("token", "")`
- `row = db.query(models.User).filter(models.User.invite_token_hash == hash_token(token), models.User.status == "pending").one_or_none()` (guard empty `token` -> treat as not found without hashing).
- Found: `row.status = "active"; row.confirmed_at = datetime.utcnow(); row.invite_token_hash = None; db.commit()`; render `invite_confirmed.html`.
- Not found: render `invite_invalid.html` (pick `200` or `410` consistently; document the choice in a comment).

- [ ] **Step 4: Write the templates**

`templates/invite_confirmed.html`: extends the app's base layout if one exists for unauthenticated pages, else a minimal standalone HTML with the Bootstrap CDN `<link>` already used elsewhere. Content: short "Your account is confirmed." + a `btn btn-primary` link to `/login` labelled "Sign in with Google".
`templates/invite_invalid.html`: "This confirmation link is invalid or has already been used." + link to `/login`.

- [ ] **Step 5: Run — pass + suite**

Run: `python -m pytest gym_tracker/tests/test_client_management.py -k confirm -v && python -m pytest --maxfail=1 --disable-warnings -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add main.py templates/invite_confirmed.html templates/invite_invalid.html gym_tracker/tests/test_client_management.py
git commit -m "feat(invite): public confirmation route flips pending to active"
```

---

## Task 6: Admin client API

**Files:**
- Modify: `main.py` (add `/api/admin/clients` + 3 action routes)
- Test: `gym_tracker/tests/test_client_management.py`

**Interfaces:**
- Consumes: `require_admin`, `send_invite_email` + `EmailSendError` (Task 3), `generate_token`/`hash_token` (Task 3), `APP_BASE_URL` setting (Task 2), `User` invite columns (Task 1).
- Produces:
  - `POST /api/admin/clients` `{email, name?}` -> `201 {id, status, warning?}`
  - `POST /api/admin/clients/{id}/resend` -> `200`
  - `POST /api/admin/clients/{id}/disable` -> `200`
  - `POST /api/admin/clients/{id}/reinvite` -> `200`
  - helper `build_confirm_url(request, raw_token) -> str` = `f"{APP_BASE_URL or str(request.base_url).rstrip('/')}/invite/confirm?token={raw_token}"`

- [ ] **Step 1: Write failing tests**

Use the existing admin-auth test helpers (`test_activity_api.py:73-123` shows the `require_admin` pattern and how tests log in as admin vs client). Patch `main.send_invite_email` with a recording double.

```python
def test_create_client_makes_pending_and_sends(admin_client, db_session, monkeypatch):
    sent = []
    monkeypatch.setattr("main.send_invite_email",
        lambda to, url, **k: sent.append((to, url)))
    r = admin_client.post("/api/admin/clients", json={"email": "New@X.com", "name": "N"})
    assert r.status_code == 201
    from gym_tracker import models
    u = db_session.query(models.User).filter_by(email="new@x.com").one()
    assert u.status == "pending" and u.role == "client"
    assert u.invite_token_hash and u.invited_by_id is not None
    assert sent and sent[0][0] == "new@x.com"
    assert "/invite/confirm?token=" in sent[0][1]

def test_create_duplicate_409(admin_client, db_session):
    db_session.add_all_helper_or_post(...)  # create new@x.com first
    r = admin_client.post("/api/admin/clients", json={"email": "new@x.com"})
    assert r.status_code == 409

def test_non_admin_forbidden(client_client):  # logged in as role=client
    for path in ["/api/admin/clients"]:
        assert client_client.post(path, json={"email": "z@x.com"}).status_code == 403

def test_resend_only_pending(admin_client, db_session, monkeypatch):
    monkeypatch.setattr("main.send_invite_email", lambda *a, **k: None)
    # pending row -> 200 and hash changes; active row -> 409

def test_disable_sets_status(admin_client, db_session):
    # active row -> POST /disable -> 200, status 'disabled', idempotent second call

def test_reinvite_only_disabled(admin_client, db_session, monkeypatch):
    monkeypatch.setattr("main.send_invite_email", lambda *a, **k: None)
    # disabled row -> 200, status 'pending', confirmed_at cleared, new hash;
    # active row -> 409

def test_create_email_failure_still_creates_with_warning(admin_client, db_session, monkeypatch):
    from gym_tracker.email import EmailSendError
    def boom(*a, **k): raise EmailSendError("smtp down")
    monkeypatch.setattr("main.send_invite_email", boom)
    r = admin_client.post("/api/admin/clients", json={"email": "f@x.com"})
    assert r.status_code == 201 and r.json().get("warning")
    from gym_tracker import models
    assert db_session.query(models.User).filter_by(email="f@x.com").one().status == "pending"
```

If fixtures `admin_client` / `client_client` do not already exist, add them to the test module (or `conftest.py`) using the `DEV_LOGIN` bypass already used by other authenticated tests, setting the session user's role appropriately.

- [ ] **Step 2: Run — fail**

Run: `python -m pytest gym_tracker/tests/test_client_management.py -k "client" -v`
Expected: FAIL — routes missing.

- [ ] **Step 3: Implement the routes**

In `main.py`, next to the other admin API routes, all with `Depends(require_admin)` and the shared DB dependency. Pull the current admin's id from the request session for `invited_by_id`.

- `POST /api/admin/clients`: parse `{email, name?}`; `email = email.strip().lower()`; `400` if empty/malformed; `409` if a `users` row with that email exists. Create row `status="pending", role="client", google_sub=None, name=name, invited_by_id=<admin id>, invited_at=utcnow()`. `raw = generate_token(); row.invite_token_hash = hash_token(raw)`. `db.commit()`. `try: send_invite_email(email, build_confirm_url(request, raw), to_name=name) except EmailSendError as ex: return JSONResponse(status_code=201, content={"id": row.id, "status": row.status, "warning": str(ex)})`. Else `201 {"id","status"}`.
- `POST /api/admin/clients/{id}/resend`: `404` if missing; `409` unless `status=="pending"`; new `raw`, replace hash, `db.commit()`, `send_invite_email(...)` (same `EmailSendError` -> `warning` handling), `200`.
- `POST /api/admin/clients/{id}/disable`: `404` if missing; set `status="disabled"`, `db.commit()`, `200`. Idempotent.
- `POST /api/admin/clients/{id}/reinvite`: `404` if missing; `409` unless `status=="disabled"`; set `status="pending"`, `confirmed_at=None`, new `raw`, replace hash, `db.commit()`, `send_invite_email(...)`, `200`.

Error body shape: match existing admin endpoints (`HTTPException(status_code, detail=...)`).

- [ ] **Step 4: Run — pass + suite**

Run: `python -m pytest gym_tracker/tests/test_client_management.py -v && python -m pytest --maxfail=1 --disable-warnings -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py gym_tracker/tests/test_client_management.py
git commit -m "feat(admin): client management API (add/resend/disable/reinvite)"
```

---

## Task 7: Admin client management page

**Files:**
- Modify: `main.py` (add `GET /admin/clients`)
- Create: `templates/admin/clients.html`
- Modify: `templates/admin/index.html` (replace the "Client management — Coming Soon" tile)
- Test: `gym_tracker/tests/test_client_management.py`

**Interfaces:**
- Consumes: `require_admin`, `User` rows with `role="client"`.
- Produces: route `GET /admin/clients` rendering `templates/admin/clients.html`.

- [ ] **Step 1: Write failing tests**

```python
def test_admin_clients_page_lists_only_clients(admin_client, db_session):
    from gym_tracker import models
    db_session.add_all([
        models.User(email="c1@x.com", role="client", status="active", google_sub="s1"),
        models.User(email="t1@x.com", role="trainer", status="active", google_sub="s2"),
        models.User(email="c2@x.com", role="client", status="pending", google_sub=None),
    ]); db_session.commit()
    r = admin_client.get("/admin/clients")
    assert r.status_code == 200
    assert "c1@x.com" in r.text and "c2@x.com" in r.text
    assert "t1@x.com" not in r.text

def test_admin_clients_page_requires_admin(client_client):
    assert client_client.get("/admin/clients").status_code in (302, 303, 403)
```

- [ ] **Step 2: Run — fail**

Run: `python -m pytest gym_tracker/tests/test_client_management.py -k "page" -v`
Expected: FAIL — 404.

- [ ] **Step 3: Implement the route**

In `main.py`, mirroring `/admin/trainers` (`main.py:679-691`): `Depends(require_admin)`, query `db.query(models.User).filter(models.User.role == "client").order_by(models.User.invited_at.desc().nullslast(), models.User.id.desc()).all()`, render `templates/admin/clients.html` with `{"request": request, "clients": rows, "user": current_user}`.

- [ ] **Step 4: Write `templates/admin/clients.html`**

Copy the structure of `templates/admin/trainers.html`: same base template `{% extends %}`, same nav/secondary-nav includes, same card/table classes, same inline `<script>` `fetch` style.
- Add-client form: `<input type="email" required>` + optional `<input type="text">` for name + submit -> `fetch('/api/admin/clients', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({email, name})})`; on `ok` reload; on `409` show "already exists"; if JSON has `warning` show it as a non-fatal notice.
- Table rows over `clients`: email, name, `<span class="badge">{{ c.status }}</span>`, `invited_at`, `confirmed_at`, actions cell:
  - `status == 'pending'` -> "Resend invite" button -> `POST /api/admin/clients/{{c.id}}/resend`
  - `status == 'active'` -> "Disable" button -> `POST /api/admin/clients/{{c.id}}/disable`
  - `status == 'disabled'` -> "Re-invite" button -> `POST /api/admin/clients/{{c.id}}/reinvite`
  - each button: `fetch` then reload; show returned `detail` on non-ok.

- [ ] **Step 5: Update the dashboard tile**

In `templates/admin/index.html`, find the "Client management" / "Coming Soon" block and replace it with an active card linking to `/admin/clients`, matching the markup of the other active admin cards (trainers/packages/activities).

- [ ] **Step 6: Run — pass + suite**

Run: `python -m pytest gym_tracker/tests/test_client_management.py -v && python -m pytest --maxfail=1 --disable-warnings -q`
Expected: PASS.

- [ ] **Step 7: Manual smoke (optional, local)**

`uvicorn main:app --port 8000`, log in as an admin (DEV_LOGIN), open `/admin/clients`, add an address, watch the confirm URL in logs (`EMAIL_ENABLED` unset), hit it, verify status flips.

- [ ] **Step 8: Commit**

```bash
git add main.py templates/admin/clients.html templates/admin/index.html gym_tracker/tests/test_client_management.py
git commit -m "feat(admin): client management page and dashboard tile"
```

---

## Task 8: Documentation

**Files:**
- Modify: `ARCHITECTURE.md` (lines ~159, ~246 mention `ALLOWED_EMAILS`)
- Modify: `.env.example` (only if it exists in the repo)
- Test: none (docs only) — run the full suite once at the end.

- [ ] **Step 1: Update `ARCHITECTURE.md`**

Replace the `ALLOWED_EMAILS` description with the Client Management model: admin invites clients at `/admin/clients`; a pending `users` row (`google_sub` NULL) is created; a confirmation email (Resend, `admin@gym.x-mas.ro`) carries a tokenized `/invite/confirm` link; confirming sets `status='active'`; the OAuth callback gates on `status`. Document the new env vars in a table with defaults (copy from Global Constraints). Note the one-off cutover migration seeds rows from the old `ALLOWED_EMAILS`.

- [ ] **Step 2: Update `.env.example`** (if present)

Remove `ALLOWED_EMAILS`. Add `EMAIL_ENABLED=false`, `EMAIL_PROVIDER=resend`, `RESEND_API_KEY=`, `EMAIL_FROM=Gym Tracker <admin@gym.x-mas.ro>`, `EMAIL_REPLY_TO=dumitru@x-mas.ro`, `APP_BASE_URL=` with a one-line comment each.

- [ ] **Step 3: Grep for stragglers**

Run: `grep -rn "ALLOWED_EMAILS" . --exclude-dir=.git`
Expected: only historical mentions in `docs/superpowers/specs` and this plan; no code or live config references.

- [ ] **Step 4: Full suite**

Run: `python -m pytest --maxfail=1 --disable-warnings -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ARCHITECTURE.md .env.example
git commit -m "docs: document client management and email env vars, drop ALLOWED_EMAILS"
```

---

## Task 9: PR

- [ ] **Step 1: Push the branch and open a PR**

```bash
git push -u origin <branch>
gh pr create --fill --title "Client management (admin console) + email invites" \
  --body "Implements docs/superpowers/specs/2026-09-05-client-management-design.md. Replaces ALLOWED_EMAILS with an admin-managed invite/confirm flow. Email via Resend, EMAIL_ENABLED=false by default. Includes cutover Alembic migration. See plan docs/superpowers/plans/2026-09-05-client-management.md."
```

- [ ] **Step 2: Paste the final diffstat + full `pytest` output into the PR description.**

Do not merge. Cross-review follows.

---

## Self-Review (against the spec)

- **5.1 schema** -> Task 1. **5.2 cutover** -> Task 1 Steps 6/8. **5.3 callback table** -> Task 4 (one test per row). **5.4 UI** -> Task 7. **5.5 API** -> Task 6 (all four routes + 409 + non-admin + email-failure warning). **5.6 confirm** -> Task 5. **5.7 email module** -> Task 3. **5.8 config** -> Task 2 (+ removal in Task 4). **5.9 DNS** -> out of code scope, noted. **§6 testing** -> Tasks 1,3,4,5,6,7 test steps. **§7 rollout** -> Task 9 + spec.
- **Type consistency:** `generate_token`/`hash_token` (Task 3) used verbatim in Tasks 5/6. `send_invite_email(to_email, confirm_url, *, to_name=None)` + `EmailSendError` (Task 3) used verbatim in Task 6. `User.status` string values `pending`/`active`/`disabled` consistent across Tasks 1,4,5,6,7. `build_confirm_url` defined in Task 6 Interfaces, used in Task 6 Step 3.
- **Placeholder scan:** test bodies are concrete; the two `# ...` comments in Task 6 Step 1 (`add_all_helper_or_post`, inline transition comments) are illustrative shorthand for repeated CRUD setup already shown in sibling tests in the same step — acceptable, not a spec gap.
