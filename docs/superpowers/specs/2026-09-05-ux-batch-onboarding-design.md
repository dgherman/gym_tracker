# UX Batch + First-Login Onboarding — Design Spec

Date: 2026-09-05
Status: Approved for planning
Branch: `feat/ux-batch-onboarding` (base: `main` @ 304808d, PR #5 merged)
Delivery: single PR, all four items.

## Overview

Four scoped changes to the gym_tracker app:

0. Consolidate `BASE_URL` / `APP_BASE_URL` into one env var (`BASE_URL`).
1. History + Reports pages: move the bottom "Back" button to the top, rename
   it "Home"; drop the page-local "Gym Tracker" link on History.
2. Session logging: add a date/time picker (default = now, no future dates)
   for retroactive session logging.
3. First-login onboarding: an interactive guided tour on the dashboard, shown
   once per user, tracked by a new `users.onboarded_at` column.

Tech context: Python 3.11 / FastAPI / SQLAlchemy / Alembic; server-rendered
Jinja2 templates (no base template — each page `{% include "_nav.html" %}`);
Bootstrap 5.3 via CDN; vanilla inline JS; pytest + in-memory SQLite. No
frontend build pipeline.

---

## Item 0 — Consolidate `BASE_URL` / `APP_BASE_URL`

### Current state
- `gym_tracker/config.py:50` `OAUTH_REDIRECT_URI` — independent, unchanged.
- `gym_tracker/config.py:51` `BASE_URL`, default `http://localhost:8000`.
  Consumers: `gym_tracker/auth.py:137` (post-OAuth redirect),
  `gym_tracker/auth.py:146` (logout redirect).
- `gym_tracker/config.py:62` `APP_BASE_URL`, default `""`. Sole consumer:
  `build_confirm_url()` at `main.py:903-911`, used at `main.py:1001` and
  `main.py:1042`.

### Change
- Delete `APP_BASE_URL` from `gym_tracker/config.py`.
- `build_confirm_url()` uses `settings.BASE_URL`, keeping the
  `str(request.base_url)` fallback only when `BASE_URL` is falsy:
  `base = (settings.BASE_URL or str(request.base_url)).rstrip("/")`
  then `return f"{base}/invite/confirm?token={raw_token}"`.
- `gym_tracker/tests/test_email.py:10-20` and
  `gym_tracker/tests/test_client_management.py:618-632`: update the
  monkeypatched env var name from `APP_BASE_URL` to `BASE_URL`. The
  "trailing slash" and "fallback" tests now set `BASE_URL` explicitly; the
  fallback test must clear `BASE_URL` (set to `""`) to reach
  `request.base_url`.
- `ARCHITECTURE.md:252,260`: replace `APP_BASE_URL` references with `BASE_URL`.
- Historical docs (`docs/superpowers/specs/2026-09-05-client-management-design.md`,
  `docs/superpowers/plans/2026-09-05-client-management.md`): a one-line note
  "APP_BASE_URL superseded by BASE_URL — see
  2026-09-05-ux-batch-onboarding-design.md" is acceptable instead of
  rewriting every mention.

### Behavior note (acceptable, intended)
Previously an unset `APP_BASE_URL` made invite links use the incoming request
host. Now `BASE_URL` is authoritative (always set in production:
`https://gym.x-mas.ro`). The request fallback remains only for the
never-configured case.

### Post-merge ops (not code)
Remove the `APP_BASE_URL=` line from `~/apps/gym-tracker/.env` on the Oracle
host and restart `gym-tracker.service`.

---

## Item 1 — History / Reports navigation

### Current state
- `templates/history.html:13` `{% include "_nav.html" %}`
- `templates/history.html:14-16` page-local `<nav class="navbar navbar-light mb-4">`
  containing `<a class="navbar-brand" href="/">Gym Tracker</a>`.
- `templates/history.html:23` `<a href="/" class="btn btn-secondary mt-4">Back</a>`
- `templates/reports.html:22` `{% include "_nav.html" %}`
- `templates/reports.html:148` `<a href="/" class="btn btn-secondary mt-4">Back</a>`
- `_nav.html` provides the sticky header; its brand is a non-link `<div>`.

### Change
Both pages: remove the bottom `Back` anchor. Immediately after the
`{% include "_nav.html" %}` line and before the first content element, insert:
`<a href="/" class="btn btn-secondary mb-3">Home</a>`
History only: delete the `templates/history.html:14-16` `<nav>` block entirely.

No change to `_nav.html`, the Reports header, or routing. `/` is
`main.py:194-197`.

### Tests
Template-render assertions (existing test client + a logged-in session):
`GET /history` and `GET /reports` responses contain `>Home</a>` with
`href="/"` before the page `<h1>`, and do NOT contain the old `>Back</a>`.
`GET /history` no longer contains `class="navbar-brand"`.

---

## Item 2 — Session date/time (retroactive logging)

### Current state
- Log Session modal: `templates/index.html:35-70`; package `<select>` labelled
  exactly "Select Package" at `templates/index.html:46`, the first control in
  `.modal-body`.
- Submit JS: `templates/index.html:297-320` builds
  `{ duration_minutes, trainer, num_people, [partner_email], activities }`
  and POSTs to `/sessions/`.
- Route: `main.py:264-281` `POST /sessions/`. Schema `SessionCreate`:
  `gym_tracker/schemas.py:13-18` (no date field).
- `gym_tracker/crud.py:293-353`; line ~336 sets
  `session_date=datetime.now(timezone.utc)` server-side.
- `Session.session_date = Column(DateTime, index=True)` — naive column
  (`gym_tracker/models.py:111-115`).
- History renders wire timestamps as UTC: `new Date(s + 'Z')`
  (`templates/history.html:90-92,133`).

### Change

**Template (`templates/index.html`):** insert, immediately above the
"Select Package" control (before `index.html:46`), inside `.modal-body`:
a `<div class="mb-3">` with `<label for="session-datetime">Session date &amp; time</label>`
and `<input type="datetime-local" class="form-control" id="session-datetime" required>`.

On modal show / init, set the input to current local time and set `max` to
now (local):
- `const now = new Date(); now.setSeconds(0,0);`
- value = `new Date(now.getTime() - now.getTimezoneOffset()*60000).toISOString().slice(0,16)`
- `max` = same expression evaluated at submit-open time.

On submit, block if empty; else convert local value to UTC ISO and attach:
`payload.session_date = new Date(dtLocal).toISOString();`
(`new Date("YYYY-MM-DDTHH:mm")` parses as local; `.toISOString()` yields UTC,
matching History's `+ 'Z'` read convention.)

**Schema (`gym_tracker/schemas.py`):** add `session_date: datetime | None = None`
to `SessionCreate`. Pydantic parses the ISO string (with `Z`) to an aware
datetime.

**Route / CRUD:** `POST /sessions/` passes `payload.session_date` to CRUD create.
In `gym_tracker/crud.py`:
- If provided: normalize to naive UTC before storing — when aware,
  `dt.astimezone(timezone.utc).replace(tzinfo=None)`; when naive, assume UTC.
- If not provided: current behavior (`datetime.now(timezone.utc)`), stored as
  naive UTC (`.replace(tzinfo=None)`) for column consistency.
- Reject future: if resolved UTC datetime is more than 5 minutes ahead of
  `datetime.utcnow()`, raise `HTTPException(422, "Session date cannot be in
  the future.")` (5-min skew tolerance).

Server-side validation is authoritative; the client `max` is convenience only.

### Timezone scope
Only the create path is touched. The pre-existing History *edit*
inconsistency (`main.py:393` stores datetime-local text as naive local while
the renderer assumes UTC) is OUT OF SCOPE — note it in the PR description as a
known follow-up; do not fix it here.

### Tests
- POST `/sessions/` with `session_date` = past UTC ISO → stored value equals
  that instant (naive UTC), not "now".
- POST with no `session_date` → behaves as today (≈ now).
- POST with `session_date` 1 hour future → `422`.
- POST with `session_date` 2 minutes future → accepted (skew tolerance).
- Aware (`...Z`) and naive inputs normalize to the same stored value.

---

## Item 3 — First-login onboarding tour

### Detection / persistence
New column on `users` (`gym_tracker/models.py`), via a new Alembic revision
(`down_revision` = `clientmgmt01`):

| Column         | Type     | Null | Default | Notes |
|----------------|----------|------|---------|-------|
| `onboarded_at` | DATETIME | YES  | NULL    | set when the user finishes or skips the tour |

Data step in the same revision:
`UPDATE users SET onboarded_at = :now WHERE onboarded_at IS NULL` — so the two
existing production users (and any already-active client) are treated as
already-onboarded; only genuinely new accounts see the tour.

ORM: add `onboarded_at = Column(DateTime, nullable=True)` to `User`.

### Trigger
`GET /` (`main.py:194-197`) passes `show_onboarding = (current_user.onboarded_at
is None)` to `templates/index.html`. The template renders the tour bootstrap
only when true.

`/dev/login` and the OAuth callback are unchanged; they do not set
`onboarded_at`.

### Dismiss endpoint
`POST /api/onboarding/complete` — authenticated (any logged-in user), no body.
Sets `current_user.onboarded_at = datetime.utcnow()` if currently NULL,
commits, returns `204`. Idempotent.

### Tour implementation
- Library: driver.js (MIT), CDN in `templates/index.html` only, matching the
  existing CDN pattern; pin a specific stable version (implementer picks the
  current `driver.js@1.3.x` and pins it): `driver.css` + `driver.js.iife.js`.
- Steps (interactive — opens modals and highlights real controls):
  1. Welcome popover (centered, no anchor): one line on what the app does.
  2. Highlight "Purchase Package" button (`index.html:74-107` trigger):
     buy at least one block of sessions first. Advancing opens the Purchase
     Package modal.
  3. Purchase modal open: highlight its package `<select>` + submit button —
     "pick a package and confirm". "Next" closes this modal.
  4. Highlight "Log Session" button. Advancing opens the Log Session modal.
  5. Step through the Log Session modal fields, one driver.js step each,
     anchored to real inputs:
     a. `#session-datetime` — "defaults to now; change it to log a past session".
     b. "Select Package" `<select>` — "which purchased block this draws from".
     c. trainer field — "who trained you".
     d. num people / partner email — "solo or shared session".
     e. activities section — "optionally log exercises, sets, reps".
     "Next" from the last field closes the modal.
  6. Highlight the Reports nav link (`_nav.html`) — "charts of your progress".
  7. Highlight the History nav link — "every past session; edit or review".
  8. Final popover with a Done button: "You're set. Start with Purchase
     Package."
- Dismissal: driver.js `onDestroyed` / Done / close / `Esc` calls
  `fetch('/api/onboarding/complete', {method:'POST'})`. Tour does not reappear
  on next `/` load.
- Replay: add a menu item in `_nav.html` (existing dropdown, authenticated
  users only): `<a href="/?tour=1">Show tips again</a>`. On `/`, if
  `request.query_params.get("tour") == "1"`, render the tour bootstrap
  regardless of `onboarded_at`. Replaying does not clear `onboarded_at`;
  finishing still POSTs `complete` (idempotent, harmless).
- Robustness: steps targeting a modal control wait for `shown.bs.modal`
  before advancing (driver.js async `onNextClick` hooks or Bootstrap modal
  events). Missing element → skip the step, do not break the tour.

### Tests
- Migration: after upgrade, `users.onboarded_at` exists; pre-existing rows
  non-NULL (backfilled); a freshly inserted row NULL.
- `GET /` as user with `onboarded_at IS NULL` → response contains the tour
  bootstrap (e.g. a `data-onboarding="1"` marker / the CDN `<script>`); as a
  user with `onboarded_at` set → does not.
- `GET /?tour=1` as an onboarded user → bootstrap present.
- `POST /api/onboarding/complete` → `204`, column set; second call → `204`,
  value unchanged; unauthenticated → existing auth behavior (redirect/401).
- `_nav.html` renders "Show tips again" for an authenticated user.

The tour's browser interaction is not unit-tested (no JS harness); keep the
tour script isolated and defensive.

---

## Files touched (estimate)
- `gym_tracker/config.py` — drop `APP_BASE_URL` (0).
- `main.py` — `build_confirm_url` (0); `POST /sessions/` future-date + passthrough
  (2); `GET /` onboarding flag + `tour` query (3);
  `POST /api/onboarding/complete` (3).
- `gym_tracker/schemas.py` — `SessionCreate.session_date` (2).
- `gym_tracker/crud.py` — session_date normalize / default (2).
- `gym_tracker/models.py` — `User.onboarded_at` (3).
- `alembic/versions/<rev>_onboarded_at.py` — new column + backfill (3).
- `templates/history.html`, `templates/reports.html` — nav (1).
- `templates/index.html` — datetime input + JS (2); driver.js CDN + tour
  script + conditional bootstrap (3).
- `templates/_nav.html` — "Show tips again" menu link (3).
- `ARCHITECTURE.md` — `BASE_URL` (0); onboarding + retro-dating notes (2, 3).
- Historical spec/plan docs — `APP_BASE_URL` note (0).
- Tests: `test_email.py`, `test_client_management.py` (0);
  new `test_session_datetime.py` (2); new `test_onboarding.py` (3);
  nav render test (1).

## Delivery
Single branch `feat/ux-batch-onboarding`, single PR. Implementer: `claude_code`.
Cross-review: `codex` against this spec. polly does not merge; the human merges.
Full gate: `python -m pytest --maxfail=1 --disable-warnings -q` green with the
new tests present.
