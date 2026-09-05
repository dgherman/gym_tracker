# UX Batch + First-Login Onboarding — Implementation Plan

> **For agentic workers:** implement task-by-task, TDD where a test is
> specified (write failing test, run it red, minimal implementation, run
> green), commit at the end of every task with the given message. Checkbox
> steps track progress.

**Goal:** Ship four scoped UX changes in one PR: BASE_URL consolidation,
History/Reports nav tidy, retroactive session date/time, and an interactive
first-login onboarding tour.

**Spec:** `docs/superpowers/specs/2026-09-05-ux-batch-onboarding-design.md` —
read fully first. Every task's requirements implicitly include the spec.

**Base:** branch `feat/ux-batch-onboarding` off `main` @ 304808d (PR #5 merged).

## Global Constraints

- Python 3.11, FastAPI, SQLAlchemy, Alembic. Schema changes only via a new
  Alembic revision (`down_revision = clientmgmt01`).
- Gate: `python -m pytest --maxfail=1 --disable-warnings -q` green
  (use `/Users/dgherman/Documents/projects/personal/gym_tracker/.venv/bin/python`
  if `python` is absent). New tests from every task must be present.
- Templates: no base template; each page `{% include "_nav.html" %}`.
  Bootstrap 5.3 via CDN, inline vanilla JS. Match existing markup patterns
  (`templates/index.html`, `templates/admin/trainers.html`).
- Stored `Session.session_date` is naive UTC. Client sends UTC ISO
  (`...Z`). 5-minute future skew tolerance.
- driver.js pinned to a specific stable version from jsDelivr, loaded only in
  `templates/index.html`.
- `onboarded_at` is set ONLY by `POST /api/onboarding/complete`, never by the
  OAuth callback or `/dev/login`.
- Frequent commits, Conventional Commit messages. Do not merge.

---

## Task 1: Item 0 — consolidate BASE_URL / APP_BASE_URL

**Files:** `gym_tracker/config.py`, `main.py` (`build_confirm_url` ~903-911),
`gym_tracker/tests/test_email.py`, `gym_tracker/tests/test_client_management.py`,
`ARCHITECTURE.md`.

- [ ] **Step 1: Update the failing tests first.** In `test_email.py:10-20` and
  `test_client_management.py:618-632`, rename every `APP_BASE_URL` monkeypatch
  to `BASE_URL`. For the fallback-to-`request.base_url` test, set
  `monkeypatch.setenv("BASE_URL", "")` so the fallback path is reached. Run
  the two files — expect failures referencing `APP_BASE_URL` / wrong host.
- [ ] **Step 2: Remove `APP_BASE_URL`** from `gym_tracker/config.py` (the
  `os.getenv("APP_BASE_URL", "")` line and the attribute).
- [ ] **Step 3: Point `build_confirm_url` at `BASE_URL`:**
  `base = (settings.BASE_URL or str(request.base_url)).rstrip("/")`.
- [ ] **Step 4: Run** `test_email.py` + `test_client_management.py` — green.
- [ ] **Step 5: Docs.** `ARCHITECTURE.md:252,260` — `APP_BASE_URL` ->
  `BASE_URL`. Append a one-line note to
  `docs/superpowers/specs/2026-09-05-client-management-design.md` and
  `docs/superpowers/plans/2026-09-05-client-management.md`:
  "> Note (2026-09-05): APP_BASE_URL was consolidated into BASE_URL — see
  2026-09-05-ux-batch-onboarding-design.md."
- [ ] **Step 6: Full suite green. Commit:**
  `git commit -m "refactor(config): consolidate APP_BASE_URL into BASE_URL"`

---

## Task 2: Item 1 — History / Reports nav

**Files:** `templates/history.html`, `templates/reports.html`, a nav render
test (new `gym_tracker/tests/test_nav_pages.py` or extend an existing page
test).

- [ ] **Step 1: Failing test.** New `test_nav_pages.py`: log in via the
  existing dev-login/session helper; `GET /history` and `GET /reports`.
  Assert each body contains `href="/"` + `>Home</a>`, the substring `>Home</a>`
  appears before `<h1`, and does NOT contain `>Back</a>`. Assert
  `GET /history` body does NOT contain `navbar-brand`. Run — red.
- [ ] **Step 2: `templates/reports.html`** — delete the bottom
  `<a href="/" class="btn btn-secondary mt-4">Back</a>` (line ~148). Right
  after `{% include "_nav.html" %}` (line ~22) and before `<h1 ...>Reports</h1>`,
  add `<a href="/" class="btn btn-secondary mb-3">Home</a>`.
- [ ] **Step 3: `templates/history.html`** — delete the bottom `Back` anchor
  (line ~23) and the page-local `<nav class="navbar navbar-light mb-4">...
  </nav>` block (lines ~14-16). After `{% include "_nav.html" %}` (line ~13),
  add `<a href="/" class="btn btn-secondary mb-3">Home</a>`.
- [ ] **Step 4:** Run `test_nav_pages.py` — green. Full suite green.
- [ ] **Step 5: Commit:**
  `git commit -m "feat(ui): move History/Reports back button to top as Home"`

---

## Task 3: Item 2 — retroactive session date/time

**Files:** `gym_tracker/schemas.py`, `gym_tracker/crud.py`, `main.py`
(`POST /sessions/` ~264-281), `templates/index.html`, new
`gym_tracker/tests/test_session_datetime.py`.

- [ ] **Step 1: Failing tests.** `test_session_datetime.py`, using the
  authenticated test client + a purchased package fixture (copy setup from the
  existing session-create tests):
  - past `session_date` (e.g. `"2026-01-15T09:30:00Z"`) -> created row's
    `session_date` equals that instant as naive UTC.
  - omitted `session_date` -> row `session_date` within a few seconds of now.
  - `session_date` = now + 1h -> response `422`, no row created.
  - `session_date` = now + 2min -> accepted.
  - naive input `"2026-01-15T09:30:00"` and aware `"...Z"` -> identical stored
    value.
  Run — red (schema rejects unknown field / no future check).
- [ ] **Step 2: Schema.** `SessionCreate` (`schemas.py:13-18`): add
  `session_date: datetime | None = None`.
- [ ] **Step 3: CRUD.** In the session-create function (`crud.py` ~293-353):
  accept an optional `session_date`; resolve to naive UTC —
  `if session_date: dt = session_date; dt = dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt`
  else `dt = datetime.now(timezone.utc).replace(tzinfo=None)`; store `dt`.
- [ ] **Step 4: Route.** `POST /sessions/` (`main.py:264-281`): after parsing
  the payload, if `payload.session_date` resolves to > `datetime.utcnow()` +
  `timedelta(minutes=5)` (compare in naive UTC), raise
  `HTTPException(422, "Session date cannot be in the future.")`. Pass
  `payload.session_date` into the CRUD call.
- [ ] **Step 5: Run** `test_session_datetime.py` — green.
- [ ] **Step 6: Template.** `templates/index.html`:
  - Before the "Select Package" control (line ~46), inside `.modal-body`, add
    a `mb-3` block: `<label for="session-datetime" class="form-label">Session
    date &amp; time</label>` + `<input type="datetime-local" class="form-control"
    id="session-datetime" required>`.
  - In the modal show/init JS: set `#session-datetime` value to current local
    time truncated to minutes, and set its `max` to the same; formula in the
    spec (§Item 2). 
  - In the submit handler (~297-320): read `#session-datetime`; if empty,
    block submit + show the field invalid; else
    `payload.session_date = new Date(dtLocal).toISOString();`.
- [ ] **Step 7: Manual check (optional).** `uvicorn`, open `/`, open Log
  Session — field defaults to now, future is not selectable, a past submit
  succeeds.
- [ ] **Step 8: Full suite green. Commit:**
  `git commit -m "feat(sessions): pick session date/time for retroactive logging"`

---

## Task 4: Item 3 — onboarded_at column + migration

**Files:** `gym_tracker/models.py`, `alembic/versions/<rev>_onboarded_at.py`,
`gym_tracker/tests/test_onboarding.py`.

- [ ] **Step 1: Failing test.** `test_onboarding.py`:
  - `models.User.__table__.c` contains `onboarded_at`, nullable.
  - a freshly constructed/committed `User` has `onboarded_at is None`.
  Run — red.
- [ ] **Step 2: ORM.** Add `onboarded_at = Column(DateTime, nullable=True)` to
  `User` (`models.py`).
- [ ] **Step 3: Run** the two assertions — green.
- [ ] **Step 4: Alembic revision.** `alembic revision -m "onboarded_at"` (hand
  written), `down_revision = "clientmgmt01"`. `upgrade()`:
  `op.add_column("users", sa.Column("onboarded_at", sa.DateTime(), nullable=True))`
  then data step via `op.get_bind()`:
  `UPDATE users SET onboarded_at = :now WHERE onboarded_at IS NULL` with
  `now = datetime.utcnow()`. `downgrade()`: `op.drop_column("users",
  "onboarded_at")`. Dialect handling consistent with `clientmgmt01` (batch for
  SQLite if that file uses it).
- [ ] **Step 5: Migration test.** Build a DB stamped at `clientmgmt01` with a
  couple of `users` rows, `upgrade head`, assert: column exists; the
  pre-existing rows have non-NULL `onboarded_at`; an `INSERT` after upgrade
  defaults to NULL.
- [ ] **Step 6: Full suite green. Commit:**
  `git commit -m "feat(db): add users.onboarded_at with backfill"`

---

## Task 5: Item 3 — onboarding trigger + dismiss endpoint + replay link

**Files:** `main.py` (`GET /` ~194-197; new `POST /api/onboarding/complete`),
`templates/index.html` (conditional bootstrap marker), `templates/_nav.html`
(replay link), `gym_tracker/tests/test_onboarding.py`.

- [ ] **Step 1: Failing tests** in `test_onboarding.py`:
  - `GET /` as a user with `onboarded_at IS NULL` -> body contains
    `data-onboarding="1"` (the marker the template renders when
    `show_onboarding` is true).
  - `GET /` as a user with `onboarded_at` set -> body does NOT contain
    `data-onboarding="1"`.
  - `GET /?tour=1` as an onboarded user -> body contains `data-onboarding="1"`.
  - `POST /api/onboarding/complete` (authenticated) -> `204`; the user's
    `onboarded_at` is now set. Second `POST` -> `204`, value unchanged.
  - `POST /api/onboarding/complete` unauthenticated -> same behavior as other
    auth-required endpoints (redirect or `401` — match the codebase).
  - `_nav.html` rendered for an authenticated user contains
    `href="/?tour=1"` and the text "Show tips again".
  Run — red.
- [ ] **Step 2:** `GET /` handler — compute
  `show_onboarding = current_user.onboarded_at is None or request.query_params.get("tour") == "1"`
  and pass it into the template context.
- [ ] **Step 3:** `templates/index.html` — near the top of the page body,
  render `<div data-onboarding="1"></div>` (or set a JS var) only
  `{% if show_onboarding %}`. The actual tour script (Task 6) keys off this
  marker.
- [ ] **Step 4:** New route `POST /api/onboarding/complete`, auth dependency
  as used by other `/api/*` routes. If `current_user.onboarded_at is None`:
  set `datetime.utcnow()`, commit. Return `Response(status_code=204)`.
- [ ] **Step 5:** `templates/_nav.html` — inside the existing authenticated
  dropdown menu, add `<a class="dropdown-item" href="/?tour=1">Show tips
  again</a>` (match the surrounding item markup).
- [ ] **Step 6:** Run `test_onboarding.py` — green. Full suite green.
- [ ] **Step 7: Commit:**
  `git commit -m "feat(onboarding): first-login trigger, complete endpoint, replay link"`

---

## Task 6: Item 3 — driver.js interactive tour script

**Files:** `templates/index.html` (CDN tags + tour script). No unit test —
implement defensively per spec §Item 3.

- [ ] **Step 1:** Add to `templates/index.html` head/scripts, pinned:
  `<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/driver.js@1.3.1/dist/driver.css">`
  and `<script src="https://cdn.jsdelivr.net/npm/driver.js@1.3.1/dist/driver.js.iife.js"></script>`
  (confirm 1.3.1 resolves; otherwise pin the current stable).
- [ ] **Step 2:** Guarded tour script: run only if
  `document.querySelector('[data-onboarding="1"]')` exists. Build the 8-step
  driver.js sequence from spec §Item 3 (welcome; Purchase Package button ->
  opens modal; purchase modal fields; Log Session button -> opens modal; the
  five Log Session fields incl. `#session-datetime`; Reports nav link; History
  nav link; Done). Steps that open a Bootstrap modal must await
  `shown.bs.modal` (wrap `driver` `onNextClick` in a promise that resolves on
  the modal event) before highlighting a control inside it. Missing target ->
  skip that step.
- [ ] **Step 3:** On tour end (`onDestroyed` / Done / close / Esc):
  `fetch('/api/onboarding/complete', { method: 'POST' })` (ignore response).
- [ ] **Step 4: Manual verification.** `uvicorn`; fresh user (temporarily set
  `onboarded_at` NULL in the DB, or use a new dev-login email); load `/`; tour
  auto-starts, opens both modals, highlights `#session-datetime` and the
  package select, ends on Done; reload `/` — tour does NOT restart;
  `/?tour=1` — tour starts again. Note the manual result in the commit body.
- [ ] **Step 5: Full suite still green. Commit:**
  `git commit -m "feat(onboarding): interactive driver.js dashboard tour"`

---

## Task 7: Docs

**Files:** `ARCHITECTURE.md`.

- [ ] **Step 1:** Document: `BASE_URL` now the sole base-URL var (0);
  `Session.session_date` accepts a client-supplied past UTC value, future
  rejected (2); `users.onboarded_at` + the dashboard tour + `/?tour=1` replay
  + `POST /api/onboarding/complete` (3).
- [ ] **Step 2: Commit:**
  `git commit -m "docs: BASE_URL, retro session dating, onboarding tour"`

---

## Task 8: PR

- [ ] **Step 1:** `git push -u origin feat/ux-batch-onboarding`
- [ ] **Step 2:** `gh pr create --base main --title "UX batch: BASE_URL,
  History/Reports nav, retro session dating, first-login tour" --body`
  referencing the spec + plan paths, listing the four items, noting the
  History-*edit* timezone inconsistency as a known out-of-scope follow-up, and
  the post-merge op (remove `APP_BASE_URL` from the Oracle `.env`). Paste the
  final diffstat + full `pytest` output.
- [ ] **Step 3:** Do NOT merge.

---

## Self-review

- Spec item 0 -> Task 1; item 1 -> Task 2; item 2 -> Task 3; item 3
  persistence -> Task 4, trigger/endpoint/replay -> Task 5, tour script ->
  Task 6; docs -> Task 7; PR -> Task 8.
- Type consistency: `session_date: datetime | None` (Task 3) is the same name
  used in route + CRUD steps. `onboarded_at` column (Task 4) is the same name
  read in Task 5 (`show_onboarding`) and Task 6 fetch target
  `/api/onboarding/complete` (defined Task 5). `data-onboarding="1"` marker
  string is identical in Tasks 5 and 6.
