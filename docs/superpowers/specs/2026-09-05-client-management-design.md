# Client Management — Design Spec

Date: 2026-09-05
Status: Approved for planning
Author: polly (orchestrator) with human partner

## 1. Problem

Login authorization is currently a manually-maintained comma-separated
environment variable, `ALLOWED_EMAILS`, checked once inside the Google OAuth
callback (`gym_tracker/auth.py:69-72`). Adding or removing a client means
editing deployment config and redeploying.

We want an in-app **Client Management** section in the existing Admin Console
where an admin can add and remove clients. Adding a client sends a
confirmation email to their address. After the client confirms, they can sign
in with their Google account.

## 2. Goals

- Admin-only UI at `/admin/clients` to add, re-invite, and disable clients.
- New client receives a confirmation email containing a tokenized link.
- Clicking the link marks the account confirmed; only then can the client sign
  in with Google.
- Removing a client is a soft-disable: login is revoked, all historical data
  (purchases, sessions, progress entries) is retained.
- Retire `ALLOWED_EMAILS`. Existing allowlisted addresses are migrated to
  database rows by a one-off data migration.
- Outbound email is sent from `admin@gym.x-mas.ro` via a transactional
  provider (Resend), behind a thin transport abstraction so the provider can
  be swapped in one file.

## 3. Non-goals

- No inbound email handling / webhook parsing. The flow is send-only.
- No self-service signup. Clients exist only because an admin invited them.
- No token expiry, no rate limiting on invite sends (very low volume).
- No change to how the `admin` or `trainer` roles are assigned (still
  DB-managed, unchanged from today).
- No bulk import UI. One email at a time.
- No email to the client on disable / re-enable (only the invite email).

## 4. Current-state summary (from investigation)

- Python 3.11, FastAPI, Uvicorn, Pydantic. SQLAlchemy ORM, Alembic
  migrations, MySQL in production, in-memory SQLite for tests.
- Server-rendered Jinja2 templates, Bootstrap 5, vanilla `fetch` JS. No
  frontend build pipeline.
- Google OAuth via Authlib. `/login` starts the redirect; `/auth/callback`
  exchanges the code, reads OIDC claims, upserts `users` keyed by
  `google_sub`, stores `user_id` in a signed session cookie.
- `ALLOWED_EMAILS` env var, normalized in `gym_tracker/config.py:52-56`,
  enforced only in `gym_tracker/auth.py:69-72`. Empty value disables the
  restriction.
- `users.role` is real: `"client"` (default for OAuth-created accounts),
  `"admin"` (checked by `require_admin` in `main.py:95-102`), `"trainer"`
  (unrelated to the admin console). No production UI promotes users.
- Admin Console exists and is role-gated: `/admin`, `/admin/trainers`,
  `/admin/packages`, `/admin/activities`. All page and mutating routes use
  `Depends(require_admin)`. The nav link shows only for admins
  (`templates/_nav.html:78-80`). The `/admin` dashboard has a "Client
  management — Coming Soon" tile in `templates/admin/index.html`.
- `users` table stores `google_sub` (unique, currently `nullable=False`),
  `email` (indexed, nullable), profile fields, `role`, an active flag, and
  audit timestamps. Ten ORM models total; `gym_tracker/models.py` is the
  schema source; nine tracked Alembic revisions.
- No outbound email code, dependency, template, or config anywhere today.
- Deployed as a Docker image (`python:3.11-slim`); container runs
  `alembic upgrade head` then Uvicorn. CI (`.github/workflows/ci.yml`) runs
  `pytest --maxfail=1 --disable-warnings -q` and pushes images to GHCR on
  `main`.
- Tests: pytest, `gym_tracker/tests/test_*.py`, in-memory SQLite with
  `StaticPool`. Authenticated integration tests use a `DEV_LOGIN` bypass.
  There are no existing tests for the real OAuth callback or the
  `ALLOWED_EMAILS` check.

## 5. Design

### 5.1 Data model — extend `users` (Model A)

One new Alembic revision. All changes on the `users` table.

Column changes:

- `google_sub`: change to **nullable**. Keep the unique constraint (MySQL
  permits multiple `NULL`s in a unique index; SQLite does too). A pending
  invite is a `users` row with `google_sub = NULL`.

New columns:

| Column                    | Type          | Null | Default     | Notes |
|---------------------------|---------------|------|-------------|-------|
| `status`                  | VARCHAR(20)   | NO   | `'active'`  | one of `pending`, `active`, `disabled` |
| `invite_token_hash`       | VARCHAR(64)   | YES  | NULL        | SHA-256 hex of the raw invite token; unique index; cleared on confirm |
| `invited_by_id`           | INT           | YES  | NULL        | FK -> `users.id`, `ON DELETE SET NULL` |
| `invited_at`              | DATETIME      | YES  | NULL        | set when the invite row is created |
| `confirmed_at`            | DATETIME      | YES  | NULL        | set when the client confirms |

`status` is the single source of truth for login eligibility. The existing
active flag on `users` is folded into `status` (see 5.2); if application code
still reads the old flag, keep it in sync on writes, or migrate those reads to
`status` as part of this work — implementer's discretion, but there must be
exactly one source of truth for "can this user log in".

Raw invite token: `secrets.token_urlsafe(32)`. The raw value appears only in
the emailed URL. Only its SHA-256 hex digest is stored, in
`invite_token_hash`. Lookups hash the incoming token and match on the digest.

ORM: add the columns to `models.User`, plus a `status` string and the
timestamp/FK fields. No new model class.

### 5.2 Cutover migration (retire `ALLOWED_EMAILS`)

In the **same** Alembic revision, after the schema change, a data step:

1. Set `status = 'active'` for every existing `users` row (they are all
   already-logged-in real users), and set `confirmed_at` to `created_at` (or
   `now()` if `created_at` is absent) where `confirmed_at IS NULL`.
2. Read `os.getenv("ALLOWED_EMAILS")`. For each normalized entry
   (`strip().lower()`, split on `,`, drop blanks) that does **not** already
   match a `users.email` (case-insensitive):
   - insert a `users` row with `email` set, `google_sub = NULL`,
     `status = 'active'`, `role = 'client'`, `invited_at = now()`,
     `confirmed_at = now()`, `invite_token_hash = NULL`.
   These are pre-authorized addresses that had never logged in; they can sign
   in with Google immediately (first login backfills `google_sub`), matching
   today's behavior.

After this revision is deployed:

- `ALLOWED_EMAILS` is removed from deployment configuration.
- `Settings.allowed_emails_set` and `ALLOWED_EMAILS` handling in
  `gym_tracker/config.py` are deleted.
- The check in `gym_tracker/auth.py:69-72` is replaced per 5.3.

Known caveat, unchanged from today: an allowlisted address that belonged to a
future admin and had never logged in is created with `role = 'client'`. Admin
role assignment remains a manual DB operation, exactly as it is now.

Migration must be safe to run against the production MySQL database and
against a fresh empty database (no `users`, no env var → no-op beyond adding
columns).

### 5.3 Auth callback

Replace the `ALLOWED_EMAILS` check in `gym_tracker/auth.py` (currently lines
69-72) with a database lookup. After the OIDC claims are read and `email` /
`google_sub` (`sub`) are known:

1. Look up a `users` row by `lower(email)`.
2. Decision table:

| Condition                                              | Result |
|-------------------------------------------------------|--------|
| no row                                                | `403` — "This email has not been invited." |
| `status = 'pending'`                                   | `403` — "Please confirm your invitation from the email we sent you before signing in." |
| `status = 'disabled'`                                  | `403` — "Access for this account has been revoked." |
| `status = 'active'` and `google_sub IS NULL`           | first login: set `google_sub` and profile fields from claims, proceed |
| `status = 'active'` and `google_sub` set and `!= sub`  | `403` — "This email is linked to a different Google account." |
| `status = 'active'` and `google_sub == sub`            | proceed |

3. "Proceed" = the existing behavior below the old check: upsert profile,
   set `request.session["user_id"]`, redirect.

The existing lookup by `google_sub` in the callback still runs for returning
users; the new lookup is by `email` and governs eligibility. Implementer
reconciles the two lookups so an active invited user whose `google_sub` is
`NULL` is matched by email on first login and by `google_sub` thereafter,
without creating a duplicate row.

Partner-matching / linking code that also reads `email`
(`gym_tracker/auth.py:101-110`, `gym_tracker/crud.py`) is unaffected and must
keep working.

### 5.4 Admin Client Management UI

New page: `GET /admin/clients`, `Depends(require_admin)`, rendered from a new
`templates/admin/clients.html` that mirrors the structure and JS style of
`templates/admin/trainers.html`.

Page contents:

- **Add client** form: `email` (required), `name` (optional). Submits via
  `fetch` to the create API, then reloads the table or updates it in place.
- **Client table**, one row per `users` row with `role = 'client'` (admins
  and trainers are not "clients" and are managed elsewhere). Columns: email,
  name, status badge, invited at, confirmed at, actions.
- **Row actions** (buttons, `fetch` POST, `Depends(require_admin)`):
  - `status = 'pending'`  -> **Resend invite**
  - `status = 'active'`   -> **Disable**
  - `status = 'disabled'` -> **Re-invite**

Nav: replace the "Client management — Coming Soon" tile in
`templates/admin/index.html` with a working link to `/admin/clients`. No
change to `templates/_nav.html` beyond what already gates admin links, unless
the console has a secondary nav that should list the new page (match the
pattern used for trainers/packages/activities).

### 5.5 Admin API

All routes `Depends(require_admin)`. JSON in, JSON out. Follow the existing
admin API conventions in `main.py` (`main.py:511-536`, `703-728`,
`757-823`).

| Method & path                              | Body            | Behavior |
|--------------------------------------------|-----------------|----------|
| `POST /api/admin/clients`                  | `{email, name?}`| Normalize email (`strip().lower()`). `409` if a `users` row with that email exists. Else create row: `status='pending'`, `role='client'`, `google_sub=NULL`, `name` if given, `invited_by_id` = current admin, `invited_at=now()`. Generate raw token, store hash, send invite email (5.6). On send failure: keep the row, respond `201` with a `warning` field so the UI can prompt "Resend". |
| `POST /api/admin/clients/{id}/resend`      | none            | `404` if no row. `409` unless `status='pending'`. Generate new token, replace hash, resend email. |
| `POST /api/admin/clients/{id}/disable`     | none            | `404` if no row. Set `status='disabled'`. Idempotent if already disabled. Existing session cookies are not force-invalidated; the next request that hits an auth-guarded route continues to work until the cookie is re-checked — acceptable, but note: the client can no longer *re-login* once their current session ends. (If stronger revocation is wanted later, that is a separate change.) |
| `POST /api/admin/clients/{id}/reinvite`    | none            | `404` if no row. `409` unless `status='disabled'`. Set `status='pending'`, clear `confirmed_at`, generate new token, store hash, send invite email. |

Errors use the same shape as existing admin endpoints.

### 5.6 Confirmation flow

- `GET /invite/confirm?token=<raw>` — **public**, no auth dependency.
  1. Hash the `token` query param (SHA-256 hex).
  2. Find a `users` row with matching `invite_token_hash` **and**
     `status = 'pending'`.
  3. Found: set `status='active'`, `confirmed_at=now()`, clear
     `invite_token_hash`. Render `templates/invite_confirmed.html`: short
     success message + a "Sign in with Google" button linking to `/login`.
  4. Not found (unknown, already used, or row no longer pending): render
     `templates/invite_invalid.html` with a friendly explanation and a link to
     `/login`. Return HTTP `200` (page, not an API) or `410` — implementer
     picks one and is consistent; the body is the same friendly page.

- Confirm is **idempotent from the user's view**: a second click on a
  consumed link lands on the "invalid or already used" page, not an error.

- Two new minimal Jinja2 templates, styled like the rest of the app
  (Bootstrap, existing base layout if there is one for unauthenticated pages;
  otherwise a minimal standalone layout).

### 5.7 Email module

New file `gym_tracker/email.py` (or `gym_tracker/emailer.py` if `email`
shadows the stdlib package uncomfortably in this codebase — implementer's
call; prefer a name that does not collide with `import email`).

Public function:

```
def send_invite_email(to_email: str, confirm_url: str, *, to_name: str | None = None) -> None
```

- Builds the message: subject, plain-text body, minimal HTML body. Copy is
  drafted by the implementer; requirements: states the gym/admin is inviting
  them, one clear call-to-action link (`confirm_url`), plain and non-spammy,
  no tracking pixels. Keep it short.
- `From`: value of `EMAIL_FROM` (`"Gym Tracker <admin@gym.x-mas.ro>"`).
- `Reply-To`: value of `EMAIL_REPLY_TO` (`dumitru@x-mas.ro`).
- Transport is selected by `EMAIL_PROVIDER` (only `"resend"` implemented
  now). Resend transport: `POST https://api.resend.com/emails` with
  `Authorization: Bearer <RESEND_API_KEY>`, JSON `{from, to, reply_to,
  subject, text, html}`, using `httpx` (add to `requirements.txt` if not
  already transitively pinned; pin it explicitly).
- If `EMAIL_ENABLED` is false (**default**): do not make an HTTP call. Log
  the `confirm_url` at `INFO` so local/dev/test flows can complete manually.
- On HTTP failure (non-2xx or transport error): raise a well-typed exception
  (`EmailSendError`) that the create/resend/reinvite API paths catch and turn
  into the `warning` response field. The module never swallows failures
  silently when enabled.

Thin abstraction: a `Transport` protocol / function with one Resend
implementation. Swapping to Zoho SMTP or Brevo later is a new function in this
file plus an `EMAIL_PROVIDER` branch — no changes elsewhere.

### 5.8 Configuration

New settings in `gym_tracker/config.py`, read via `os.getenv` like the
existing ones:

| Env var           | Default                                   | Purpose |
|-------------------|-------------------------------------------|---------|
| `EMAIL_ENABLED`   | `false`                                   | Gate real sending. `false` -> log only. |
| `EMAIL_PROVIDER`  | `resend`                                  | Transport selector. |
| `RESEND_API_KEY`  | `""`                                      | Resend API key (sending scope). |
| `EMAIL_FROM`      | `Gym Tracker <admin@gym.x-mas.ro>`        | `From` header. |
| `EMAIL_REPLY_TO`  | `dumitru@x-mas.ro`                        | `Reply-To` header. |
| `APP_BASE_URL`    | derive from request if unset, else `""`   | Base URL used to build `confirm_url` (`{APP_BASE_URL}/invite/confirm?token=...`). Prefer an explicit env value in production; fall back to `request.base_url` if empty. |

`ALLOWED_EMAILS` is deleted from config after the cutover migration ships.

Document all new env vars in `ARCHITECTURE.md` (and `.env.example` if one
exists) and remove the `ALLOWED_EMAILS` references there
(`ARCHITECTURE.md:159,246`).

### 5.9 DNS / provider (human, outside this codebase — already done)

Recorded here for completeness; no code depends on it and it is already set
up:

- Resend account, domain `gym.x-mas.ro` verified.
- DNS on the subdomain only; root `x-mas.ro` MX stays on Zoho:
  - `MX send.gym.x-mas.ro -> feedback-smtp.<region>.amazonses.com`
  - `TXT send.gym.x-mas.ro -> v=spf1 include:amazonses.com ~all`
  - `TXT resend._domainkey.gym.x-mas.ro -> <DKIM key>`
  - `TXT _dmarc.gym.x-mas.ro -> v=DMARC1; p=none; rua=mailto:dumitru@x-mas.ro`
- The inbound `MX gym.x-mas.ro -> inbound-smtp...amazonaws.com` and Resend
  "receiving" feature are being removed; not used.
- Test send verified: `DKIM=pass`, `SPF=pass`, `DMARC=pass`.
- Production env will set `EMAIL_ENABLED=true` and `RESEND_API_KEY`.

## 6. Testing

Framework: pytest, in-memory SQLite, existing `db_test_utils` fixtures. Email
is exercised with `EMAIL_ENABLED=false` (asserts logging, no HTTP) and with a
mocked `httpx` client (asserts request URL, auth header, and JSON payload
shape).

Required cases:

- **Migration**: after `alembic upgrade head` on a DB seeded with a couple of
  `users` rows and `ALLOWED_EMAILS="a@x.com, b@x.com"` in env — new columns
  exist; existing rows are `status='active'`; `a@x.com` / `b@x.com` rows
  created with `status='active'`, `google_sub IS NULL`; an entry already
  matching an existing email does not duplicate. Empty env + empty DB -> only
  columns added.
- **Auth callback matrix** (5.3), one test per row: no invite -> 403;
  pending -> 403; disabled -> 403; active + null `google_sub` -> success +
  `google_sub` backfilled; active + mismatched `google_sub` -> 403; active +
  matching `google_sub` -> success. OIDC token exchange is stubbed.
- **Confirm endpoint**: valid pending token -> `status='active'`,
  `confirmed_at` set, hash cleared, success page; reused/unknown token ->
  invalid page, no state change; token whose row was disabled after issue ->
  invalid page.
- **Admin API**:
  - `POST /api/admin/clients` happy path -> row `pending`, hash set,
    `invited_by_id` = admin, invite email attempted once with a
    `.../invite/confirm?token=` URL.
  - duplicate email -> `409`.
  - non-admin caller -> `403` for every `/api/admin/clients*` route.
  - `resend` on pending -> new hash differs from old, email re-attempted;
    `resend` on active -> `409`.
  - `disable` -> `status='disabled'`; idempotent.
  - `reinvite` on disabled -> `status='pending'`, `confirmed_at` cleared, new
    hash, email attempted; `reinvite` on active -> `409`.
  - email transport failure -> API still returns success with a `warning`
    field and the row persists.
- **Email module**: `EMAIL_ENABLED=false` -> no HTTP, `confirm_url` logged;
  `EMAIL_ENABLED=true` with mocked `httpx` -> correct endpoint, bearer
  header, `from` / `reply_to` / `to` / `subject` / `text` / `html` in
  payload; non-2xx -> `EmailSendError`.

Full suite must pass: `pytest --maxfail=1 --disable-warnings -q`.

## 7. Rollout

### 7.0 Local verification (Docker) before production

Production is the Oracle host. Before deploying there, verify the full flow
locally against a Docker build:

1. Build the image locally and run it with a local MySQL (mirror the
   production entrypoint: `alembic upgrade head` then Uvicorn).
2. First pass with `EMAIL_ENABLED=false`: add a client in `/admin/clients`,
   copy the `confirm_url` from the container logs, hit it, then sign in with a
   matching Google account. Confirms schema, migration, callback matrix, and
   UI without sending mail.
3. Second pass with `EMAIL_ENABLED=true` + the real `RESEND_API_KEY` and
   `APP_BASE_URL` set to the locally reachable URL: add a client with a real
   address, confirm the email actually arrives from `admin@gym.x-mas.ro`,
   click the real link, sign in.
4. Run `alembic downgrade` one step and re-`upgrade` against the local MySQL
   to confirm the migration is reversible and idempotent.

Only after this passes locally, proceed to production.

### 7.1 Production (Oracle host)

1. Merge the PR. CI builds the image.
2. Deploy to the Oracle host. Container runs `alembic upgrade head`: columns
   added, existing users set active, `ALLOWED_EMAILS` entries migrated to
   rows.
3. Set `EMAIL_ENABLED=true`, `RESEND_API_KEY=...`, `APP_BASE_URL=https://<prod
   host>` in the deployment environment. Redeploy / restart.
4. Verify: admin opens `/admin/clients`, adds a throwaway address, receives
   the email, clicks confirm, signs in with the matching Google account.
5. Remove `ALLOWED_EMAILS` from the deployment environment.
6. Remove the `gym.x-mas.ro` inbound MX record and disable Resend receiving.

Rollback: the previous image still reads `ALLOWED_EMAILS`. If rolled back
before step 5, login authorization falls back to the env var; rows added via
the new UI simply are not consulted. The added columns are additive and do
not break the old image. A client invited but not confirmed during the window
is a pending row with no login access — re-inviting after roll-forward is
harmless.

## 8. Files touched (estimate)

- `gym_tracker/models.py` — new columns on `User`.
- `alembic/versions/<new>_client_management.py` — schema + data migration.
- `gym_tracker/auth.py` — replace allowlist check with DB decision table;
  first-login `google_sub` backfill.
- `gym_tracker/config.py` — add email settings; remove `ALLOWED_EMAILS`.
- `gym_tracker/email.py` (new) — transport + `send_invite_email`.
- `main.py` — `/admin/clients` page route; `/api/admin/clients*` routes;
  `/invite/confirm` route.
- `templates/admin/clients.html` (new), `templates/admin/index.html` (tile ->
  link), `templates/invite_confirmed.html` (new),
  `templates/invite_invalid.html` (new).
- `requirements.txt` — pin `httpx` if not already.
- `gym_tracker/tests/test_client_management.py` (new) and/or additions to
  existing auth test modules.
- `ARCHITECTURE.md`, `.env.example` (if present) — document new env vars,
  drop `ALLOWED_EMAILS`.

## 9. Delivery

- One implementer sub-agent (`claude_code`) in its own git worktree; opens
  one PR.
- Cross-review by a different vendor (`codex`) against this spec + the PR
  diff.
- Blocking review findings become fix-tasks on the same branch.
- polly does not merge. The human merges the PR after cross-review passes.

## 10. Open questions

None blocking. Deferred: force-invalidating an active session on disable
(currently only prevents future logins).
