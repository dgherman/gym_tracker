# gym_tracker
Simple gym sessions tracker

## Features

- **Session Logging**: Log gym sessions against purchased packages with duration, trainer, and optional partner sharing.
- **Package Management**: Buy personal or shared 2-person packages; automatic partner linking via email.
- **Activity Tracking**: Optionally log structured activities per session, grouped by admin-managed categories (Strength, Cardio, Mobility, Other) with category-defined metric fields (e.g. reps, weight in lbs, duration). A global activity library lets any user create activities that everyone can use; log at session-create time or edit retroactively.
- **Reports & History**: Session history, analytics by trainer/duration/partner, and remaining-session summaries.
- **Admin Tools**: Manage trainers, packages, and the activity library (categories, metric fields, deactivation).

## Changelog

### 2026-06-09 – Reports Tabs + Progress (Per-User Activity Stats)

- Reorganized the Reports page into three tabs: **Sessions** (training-minute charts), **Billing** (total cost + sessions-remaining per package), and a new **Progress** tab.
- **Progress** shows the logged-in user's own activity stats — a summary table (Times / Best / Total / Latest per activity) plus a trend chart of any numeric field over time. Attribution is per `person_slot`: solo sessions and the user's tagged rows in couples sessions count; the other person's rows do not. `weight` totals are omitted (Σweight is meaningless); additive fields (reps/distance/duration) are summed.
- New endpoint `GET /reports/progress/data` (login required) backed by `crud.user_activity_rows` + a pure `gym_tracker/progress.py` aggregator.
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
