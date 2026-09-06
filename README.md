# gym_tracker
Simple gym sessions tracker

## Features

- **Session Logging**: Log gym sessions against purchased packages with duration, trainer, and optional partner sharing.
- **Package Management**: Buy personal or shared 2-person packages; automatic partner linking via email.
- **Activity Tracking**: Optionally log structured activities per session, grouped by admin-managed categories (Strength, Cardio, Mobility, Other) with category-defined metric fields (e.g. reps, weight in lbs, duration). A global activity library lets any user create activities that everyone can use; log at session-create time or edit retroactively.
- **Reports & History**: Session history, analytics by trainer/duration/partner, and remaining-session summaries.
- **Admin Tools**: Manage trainers, packages, and the activity library (categories, metric fields, deactivation).

## Changelog

### 2026-09-06 – First-Login Onboarding Tour, Retroactive Session Dating, BASE_URL Consolidation

- **Guided onboarding tour**: the first login shows an interactive driver.js walkthrough of the dashboard — purchase a package, then log a session field by field (date/time, package, trainer, activities), then Reports and History. New nullable `users.onboarded_at` column (migration `onboard01`, `down_revision = clientmgmt01`) records completion; existing users are backfilled on upgrade so only genuinely new accounts see the tour. Replay anytime via the "Show tips again" menu link or `/?tour=1`. New endpoint `POST /api/onboarding/complete` (login required, idempotent, returns 204) is the only writer of `onboarded_at` — the OAuth callback and `/dev/login` never set it.
- **Retroactive session date/time**: the Log Session form has a date/time picker defaulting to the current local time, so sessions can be logged for past dates. `POST /sessions/` accepts an optional `session_date` (client sends a UTC ISO string); it is stored as naive UTC. Timestamps more than 5 minutes in the future are rejected with `422` and no row is created.
- **History / Reports navigation**: the bottom "Back" button moved to the top of both pages and is relabelled "Home" (spaced below the header banner); History's redundant page-local title link was removed.
- **`APP_BASE_URL` removed**: invite and confirmation links now build from the existing `BASE_URL` setting, falling back to the incoming request host. Remove the `APP_BASE_URL` line from deployment env after upgrading.

### 2026-09-06 – Branch Test Images in CI

- Pushing any non-`main` branch now builds and publishes an `arm64` test image to `ghcr.io/<owner>/gym-tracker-app:branch-<sanitized-ref>` (plus a commit-SHA tag), so a feature branch can be deployed and verified before it is merged. `main` still publishes the multi-arch `latest` and SHA tags unchanged. A non-blocking retention job prunes untagged and older `branch-*` image versions while protecting `latest` and released images.

### 2026-09-05 – Client Management (Admin-Managed Access)

- **Replaced the `ALLOWED_EMAILS` allowlist with an admin-managed invite system.** Admin Console → Client Management (`/admin/clients`, admin only) adds and removes users. Adding a client creates a `pending` account and emails a tokened confirmation link; clicking it (`GET /invite/confirm`) activates the account, after which the user signs in with Google. Removal is a soft disable that revokes login but keeps the user's purchases, sessions, and progress history.
- Schema (migration `clientmgmt01`, `down_revision = pe01standalone`): `users` gains `status` (`pending`/`active`/`disabled`), `invite_token_hash` (SHA-256 of the raw token; the raw token exists only in the email link), `invited_by_id`, `invited_at`, and `confirmed_at`; `google_sub` is now nullable (a pending invite has none until first login). The migration backfills existing rows to `active`, seeds a row per current `ALLOWED_EMAILS` entry, and aborts without changes if any pre-existing emails collide case-insensitively. Case-insensitive email uniqueness is enforced at the database level and via a fail-closed lookup helper across OAuth login, client creation, partner matching, and `/dev/login`.
- Admin API (all admin only): `POST /api/admin/clients` (add + send invite), `POST /api/admin/clients/{id}/resend`, `POST /api/admin/clients/{id}/disable`, `POST /api/admin/clients/{id}/reinvite`. Invite tokens do not expire; admins can resend a fresh token or re-invite a disabled user. Concurrent creates resolve to `409`, not `500`.
- New `gym_tracker/email.py`: outbound email behind a small provider abstraction, with a Resend transport (`RESEND_API_KEY`). With `EMAIL_ENABLED` unset or false (the default for local runs and tests) the confirmation URL is logged instead of sent. Env: `EMAIL_ENABLED`, `EMAIL_PROVIDER`, `RESEND_API_KEY`, `EMAIL_FROM`, `EMAIL_REPLY_TO`, `BASE_URL`.
- Deploy code and `alembic upgrade head` together, once; then remove `ALLOWED_EMAILS` from deployment env.

### 2026-06-12 – Standalone Progress Entries

- **Standalone progress entries**: record activity progress for any past date without a session (Reports → Progress → "+ Add progress"). Admins and trainers can log entries on behalf of other users. New `progress_entries` table (migration `pe01standalone`); entries merge seamlessly into Progress charts.

### 2026-06-11 – Person-Slot Semantics Fix, History Display, Multi-Field Trend Charts

- **Fixed swapped per-person activity attribution in couples sessions.** The frontend sent `person_slot` relative to the logged-in user (1=me) while the backend stored/displayed it as absolute (1=purchase owner), so activities logged by the purchase *partner* showed under the wrong person. The wire contract is now explicitly requester-relative (1=me, 2=the other person) and the backend translates to/from absolute storage (`activities.user_slot_in_session` / `relative_to_stored_slot` on write; viewer-relative `person_slot` in `SessionActivityRead` via `person_slot_for_viewer` on read). Stored slots remain absolute; progress attribution is unchanged.
- **Data migration `4093faf32ea1`** (data-only, no schema change) repairs rows written inverted before the fix: swaps slots 1↔2 on couples-session activity rows whose creator is not the purchase owner. Deploy code + `alembic upgrade head` together, once.
- History: couples sessions now render activities with the same per-category grouping as solo sessions (uppercase category header per person) instead of inline category badges.
- Reports → Progress: trend chart and per-category mini charts now plot **all numeric fields** of an activity in one graph (e.g. weight + reps), with the category's primary field on the left y-axis and remaining fields on a right y-axis; the single-field selector is gone. Points are aligned per logged entry (new `rid` row id in `/reports/progress/data` series), so multiple entries on the same day stay distinct.
- New `scripts/restore_prod_backup.sh`: pulls the latest prod S3 dump into the local Docker DB (drop + reload + `alembic upgrade head`).

### 2026-06-09 – Reports Tabs + Progress (Per-User Activity Stats)

- Reorganized the Reports page into three tabs: **Sessions** (training-minute charts), **Billing** (total cost + sessions-remaining per package), and a new **Progress** tab.
- **Progress** shows the logged-in user's own activity stats — a summary table (Times / Best / Total / Latest per activity) plus a trend chart of any numeric field over time. Attribution is per `person_slot`: solo sessions and the user's tagged rows in couples sessions count; the other person's rows do not. `weight` totals are omitted (Σweight is meaningless); additive fields (reps/distance/duration) are summed.
- New endpoint `GET /reports/progress/data` (login required) backed by `crud.user_activity_rows` + a pure `gym_tracker/progress.py` aggregator.
- Progress summary table sorts by Category (ascending/descending), and each category can expand to show a grid of mini trend charts (one per activity, primary field over time).
- Added a **Custom** date range (from/to) to a shared range control now used by both Reports and History; range is page-level and drives all tabs.
- Mobile-responsive: scrollable tabs, wrapped pills, horizontally-scrollable tables, fluid charts. Numeric values trim trailing `.0`.

### 2026-06-03 – Per-Person Activity Tracking & Couples Edit-Auth

- Added per-person activity recording for couples sessions via `person_slot` field on `session_activities` (1=owner/Person A, 2=partner/Person B, null=Both/Shared for legacy rows).
- Log/edit UI renders separate add-activity sections for each person in couples sessions; legacy shared rows display read-only.
- Either participant (owner or partner) can now edit or delete a couples session; previously restricted to the user who logged the session.
- Pack reallocation on duration change is always scoped to the purchase owner, regardless of which participant edits.
- Activity value display now shows units consistently in the log/edit modal (previously raw `key: value`); duration fields render as whole minutes (`N min`) instead of an ambiguous `mm:ss`, and the `min` unit is seeded on duration fields so the entry form hints minutes.

### 2026-06-02 – Activity Tracking

- Added optional structured activity logging to sessions with category-based metric schemas (Strength, Cardio, Mobility, Other).
- New tables: `activity_categories`, `category_fields` (admin-defined metric schema), `activities` (global, deduplicated library), `session_activities` (one JSON `values` row per activity per session).
- Global activity library: any user creates activities; deduplicated case-insensitively per category; soft-deleted by admin.
- Log at session-create time and add/edit/remove retroactively on existing sessions; values validated server-side against the category's active fields; reconciliation upserts by id and deletes omitted rows. Activities cascade-delete with their session.
- Admin page `/admin/activities` for managing categories, fields, and the activity library.
- Dev-only `GET /dev/login` (gated by `DEV_LOGIN` env) for local UI testing without Google OAuth.
