# Standalone Progress Entries — Design

**Date:** 2026-06-12
**Status:** Approved

## Goal

Let users retroactively record activity progress without attaching it to a training
session. Covers both workouts done outside paid sessions (home gym, solo) and
backfilling data that was never logged for past dates.

## Decisions (from brainstorming)

- **Use case:** both standalone workouts and retroactive backfill.
- **Display:** standalone entries merge seamlessly into existing Progress charts and
  stats. No badge, no separate view — date is what matters.
- **Ownership:** a user records their own entries; users with `role` of `admin` or
  `trainer` may record entries on behalf of another user. No new role infrastructure —
  uses the existing `User.role` column (`models.py`, default `"client"`).
- **Storage approach:** new `progress_entries` table (chosen over nullable
  `SessionActivity.session_id` and phantom sessions) — keeps session/package logic
  untouched and isolates the new semantics.

## Data model

New table `progress_entries`:

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `user_id` | FK `users.id`, not null | whose progress this is |
| `activity_id` | FK `activities.id`, not null | |
| `entry_date` | Date, not null | retroactive date, user-picked |
| `values` | JSON, not null | same shape as `SessionActivity.values` |
| `notes` | Text, nullable | |
| `created_by_user_id` | FK `users.id`, not null | audit; differs from `user_id` when admin/trainer logs for someone else |
| `created_at` | DateTime | server default now |

Alembic migration adds the table. `sessions` and `session_activities` are unchanged.

## API

| Route | Behavior |
|---|---|
| `POST /api/progress-entries` | Create. Body: `activity_id`, `entry_date`, `values`, `notes?`, `user_id?` (defaults to current user). |
| `GET /api/progress-entries` | List own entries. Admin/trainer: `?user_id=` lists another user's. |
| `PUT /api/progress-entries/{id}` | Edit. Same permission rule as create. |
| `DELETE /api/progress-entries/{id}` | Delete. Same permission rule. |

Pydantic schemas mirror the activity-values validation already used for session
activities.

## Permissions

- Acting on your own entries: always allowed.
- Targeting another `user_id` (create/list/edit/delete): allowed only when
  `current_user.role in ("admin", "trainer")`, otherwise 403.

## Progress aggregation merge

`crud.user_activity_rows` is the single union point. After building session-derived
rows, it queries `progress_entries` for `user_id` within `[start, end]` and appends
row dicts of the same shape:

- `session_date` ← `entry_date`
- `row_id` ← `f"p{entry.id}"` — the `p` prefix namespaces standalone row ids so they
  cannot collide with session-activity row ids in chart series (`rid` values).
- activity/category fields resolved the same way as session rows.

`progress.py` (`summarize`) is unchanged — it already consumes row dicts.

## UI

- "Add progress" button on the Reports → Progress tab.
- Modal form: date picker (defaults to today, past dates allowed), activity selector,
  dynamic value fields. Reuses the `_activity_section.html` machinery used by the
  session form.
- Admin/trainer additionally sees a user selector.
- A list of the user's standalone entries below the form with edit/delete actions.
  Delete buttons permanently visible (standing UI rule — no hover-only actions).

## Testing

- **crud:** create/list/edit/delete; permission matrix — client acting on self (ok),
  client targeting other user (403), admin/trainer targeting other user (ok).
- **progress:** standalone rows merge into `summary` and `series`; `row_id`
  namespacing (`p` prefix) does not collide with session rows.
- **filtering:** entries outside the requested date range are excluded.

## Out of scope

- Full RBAC build-out (role management UI, trainer-user linkage).
- Any change to package/session accounting — standalone entries never consume
  package sessions.
- Person-slot semantics — standalone entries are single-person by definition.
