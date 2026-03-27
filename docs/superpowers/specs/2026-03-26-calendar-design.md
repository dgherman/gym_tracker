# Calendar Feature — Design Spec

**Date:** 2026-03-26
**Status:** Approved

---

## Summary

Add a calendar feature to gym_tracker that lets trainers schedule sessions for clients and gives clients a read-only view of their own upcoming and past sessions. The calendar displays both scheduled (future) and completed (past) sessions in a unified view using FullCalendar Standard (MIT, free).

Scheduling introduces a formal session lifecycle (`scheduled → completed | cancelled`), recurring weekly "spots", credit reservation on booking, and an invitation-only signup system to replace the existing `ALLOWED_EMAILS` env var.

---

## Scope

In scope:
- Calendar page with FullCalendar.js (Standard, MIT)
- Session status lifecycle and credit reservation/refund
- Recurring sessions with 3-month rolling horizon
- Trainer role and auto-linking to Trainer records
- Invitation-only signup (replaces `ALLOWED_EMAILS`)
- Package-less session scheduling (displayed differently)
- Trainer reschedule and cancel with recurrence scope prompt

Out of scope (future):
- Client self-booking
- Package management changes (admin already has `/admin/packages`)
- Email/push notifications

---

## Data Model Changes

### Modified: `sessions` table

| Column | Change | Notes |
|---|---|---|
| `purchase_id` | Now nullable | Allows package-less sessions |
| `status` | New VARCHAR(20) | `"scheduled"` \| `"completed"` \| `"cancelled"`. Existing rows backfilled as `"completed"` |
| `client_user_id` | New FK → users, nullable | Explicit client reference; avoids join through purchase for calendar queries and supports package-less sessions |
| `scheduled_by_user_id` | New FK → users, nullable | Who created the booking |
| `recurrence_group_id` | New FK → recurrence_groups, nullable | Links sessions in a recurring series; set to NULL when a session is individually rescheduled out of its group |
| `notes` | New TEXT, nullable | Optional trainer notes per session |

### Modified: `trainers` table

| Column | Change | Notes |
|---|---|---|
| `email` | New VARCHAR(255), nullable, unique | Used for auto-linking on OAuth signup |
| `user_id` | New FK → users, nullable | The linked user account |

### Modified: `users` table

`role` already supports arbitrary strings. `"trainer"` is now a valid value alongside `"client"` and `"admin"`. No schema change required — only application-level validation.

A trainer who is also an admin carries `role = "admin"`. Trainer-only access is gated on `role in ("trainer", "admin")`.

### New: `recurrence_groups` table

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `frequency` | VARCHAR(20) | `"weekly"` \| `"biweekly"` \| `"monthly"` |
| `day_of_week` | Integer | 0 = Monday … 6 = Sunday |
| `time_of_day` | Time | Wall-clock time of session |
| `duration_minutes` | Integer | |
| `trainer_id` | FK → trainers | Default trainer for the series |
| `client_user_id` | FK → users | Client the series belongs to |
| `purchase_id` | FK → purchases, nullable | Package the series draws from; NULL = package-less |
| `created_at` | DateTime | |
| `horizon_through` | DateTime | Latest date sessions have been generated through |

### New: `user_invites` table

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `email` | VARCHAR(255), unique | Case-insensitive match on signup |
| `role` | VARCHAR(50) | `"client"` \| `"trainer"` \| `"admin"` |
| `trainer_id` | FK → trainers, nullable | Pre-links invite to a Trainer record |
| `invited_by_user_id` | FK → users | |
| `created_at` | DateTime | |
| `accepted_at` | DateTime, nullable | Set on first successful OAuth login |

---

## Session Lifecycle

```
[scheduled] ──── complete ────► [completed]
     │
     └────── cancel ──────────► [cancelled]
```

- **Credit reservation**: on schedule, `purchase.sessions_remaining` is decremented for each session created (including all sessions generated upfront for a recurring series).
- **Credit refund**: on cancel, `sessions_remaining` is incremented for each cancelled session that had a non-null `purchase_id`.
- **Completion**: no credit change — credit was already reserved on scheduling.
- **Package-less sessions**: `purchase_id = NULL`, no credit operations. Displayed in orange on the calendar.

---

## Recurring Sessions

When a trainer enables the "Recurring" toggle on the scheduling modal:

1. A `recurrence_group` row is created with the series metadata.
2. Sessions are generated from the selected start date through `now + 3 months`, respecting the chosen frequency and day/time.
3. Each session row gets `recurrence_group_id` set and credits reserved immediately.

### Horizon auto-extension

On every `/api/calendar/events` request, the API checks all `recurrence_groups` where `horizon_through < now + 3 months`. For each, it generates the missing sessions and updates `horizon_through`. No background job required — lazy extension on calendar load.

### Reschedule scope (trainer only)

When rescheduling a session that belongs to a recurrence group, the UI prompts:

- **"Just this session"** — updates `session_date` on this row only; sets `recurrence_group_id = NULL` (detaches from group, so future horizon extension ignores it).
- **"This and all future sessions"** — applies the same date/time delta (new minus original) to all sessions in the group where `session_date >= this session's original date`.

### Cancel scope (trainer only)

Same two-option prompt as reschedule. Cancelled sessions have credits refunded. "This and all future" also sets `recurrence_group.horizon_through` to the date of the earliest cancelled session so no new sessions are generated beyond that point.

---

## Calendar UI

### Library

**FullCalendar Standard** — MIT license, free for all use including public repositories. Loaded via CDN. No `schedulerLicenseKey` required. Premium Scheduler bundle is not used.

Plugins used (all Standard/MIT):
- `dayGrid` (month view)
- `timeGrid` (week and day views)
- `interaction` (drag-and-drop, `dateClick`)
- `list` (agenda/list view)

### Page

New route: `GET /calendar` — server-renders `templates/calendar.html`.
Navigation link added to `templates/_nav.html` for all authenticated users.

### Event colours

| Status | Colour | Condition |
|---|---|---|
| Scheduled | Blue | `status = "scheduled"` and `purchase_id` set |
| Scheduled (no package) | Orange | `status = "scheduled"` and `purchase_id = NULL` |
| Completed | Green | `status = "completed"` |
| Cancelled | Grey | `status = "cancelled"` |

### Event title format

- **Trainer / admin view**: `"Session #<id> · <client full name>"`
- **Client view**: `"Session #<id> · <trainer name>"`

### Scheduling modal (trainer only)

Triggered by FullCalendar `dateClick`. Fields:

| Field | Input type | Notes |
|---|---|---|
| Client | Dropdown | Active users with `role = "client"` |
| Trainer | Dropdown | Active trainers |
| Date + time | DateTime picker | Pre-filled from calendar click |
| Duration | Dropdown | Populated from distinct `duration_minutes` values of active packages |
| Package | Dropdown | Client's active purchases with `sessions_remaining > 0`, plus "No package" |
| Recurring | Toggle | Reveals frequency selector |
| Frequency | Dropdown | Weekly / Biweekly / Monthly — shown when recurring |
| Notes | Textarea | Optional |

### Event detail modal

Clicking any event opens a detail modal with:
- Session info (client, trainer, date, duration, package, notes)
- "Mark Complete" button (trainer/admin only, `status = "scheduled"` only)
- "Reschedule" button (trainer/admin only)
- "Cancel" button (trainer/admin only)

Drag-and-drop on the calendar also triggers the reschedule flow (with recurrence scope prompt if applicable).

---

## API Routes

### Calendar

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/calendar` | yes | Calendar page (HTML) |
| `GET` | `/api/calendar/events` | yes | FullCalendar events (JSON). Params: `start`, `end` (ISO). Triggers horizon extension. |

### Session scheduling

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/sessions/schedule` | trainer | Create scheduled session(s). Body: client_user_id, trainer_id, session_date, duration_minutes, purchase_id (nullable), recurring (bool), frequency, notes |
| `POST` | `/api/sessions/{id}/complete` | trainer | Mark session completed |
| `POST` | `/api/sessions/{id}/reschedule` | trainer | Body: `new_date`, `scope: "this" \| "future"` |
| `POST` | `/api/sessions/{id}/cancel` | trainer | Body: `scope: "this" \| "future"` |

### Invite management

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/admin/invites` | admin | Invite management page (HTML) |
| `POST` | `/api/invites` | admin | Create invite. Body: email, role, trainer_id (optional) |
| `DELETE` | `/api/invites/{id}` | admin | Revoke invite |

---

## Authorization

### New dependency

`require_trainer` FastAPI dependency — mirrors existing `require_admin`. Passes if `user.role in ("trainer", "admin")`. Used on all scheduling endpoints.

### Permission matrix

| Action | client | trainer | admin |
|---|---|---|---|
| View own sessions on calendar | ✓ | ✓ | ✓ |
| View all sessions on calendar | — | ✓ | ✓ |
| Schedule / reschedule / cancel | — | ✓ | ✓ |
| Mark session complete | — | ✓ | ✓ |
| Manage invites | — | — | ✓ |
| Admin panel (trainers, packages) | — | — | ✓ |

### Calendar event filtering

- **Trainer / admin**: no filter on `client_user_id` — returns all sessions in the requested date range.
- **Client**: `sessions.client_user_id = current_user.id`.

Existing `_user_session_ids` and `_user_purchase_filter` helpers in `crud.py` are unchanged — they continue to serve history and dashboard pages.

---

## Invitation-Only Signup

### On `auth/callback`

After upserting the user record, the OAuth callback:

1. Looks up `user_invites` by email (case-insensitive). If no matching invite → reject with a "not invited" error page (HTTP 403).
2. If invite found and `accepted_at` is NULL → set `user.role` from invite, set `accepted_at = now`.
3. If invite has `trainer_id` → set `Trainer.user_id = user.id`.
4. If a `Trainer` row has matching `email` but no `user_id` (pre-existing record before invite system) → link it.

### Migration from `ALLOWED_EMAILS`

- The `ALLOWED_EMAILS` env var check is removed from the codebase.
- A migration script (or manual admin action) seeds `user_invites` rows from the existing `ALLOWED_EMAILS` value and from existing `users` rows (backfill `accepted_at = created_at` for current users so they aren't locked out).

---

## Alembic Migrations

Four migration steps (can be one script or split):

1. Add `email`, `user_id` to `trainers`
2. Create `recurrence_groups` table
3. Create `user_invites` table
4. Add `status`, `client_user_id`, `scheduled_by_user_id`, `recurrence_group_id`, `notes` to `sessions`; make `purchase_id` nullable; backfill `status = "completed"` on existing rows; backfill `client_user_id` from `purchases.logged_by_user_id` via JOIN on `sessions.purchase_id`

---

## Key Patterns Followed

- Soft deletes: not applicable to sessions (status-based instead)
- Admin guard: existing `require_admin` dependency unchanged
- ORM safety: `db.expunge()` pattern from `_annotate_purchases` should be followed if mutating session objects for calendar responses
- Config: `ALLOWED_EMAILS` env var removed; `config.py` updated accordingly
- Frontend: FullCalendar initialised in `calendar.html` with a `fetch`-based `events` function pointing at `/api/calendar/events`
