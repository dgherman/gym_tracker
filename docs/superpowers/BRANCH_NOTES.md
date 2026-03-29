# Branch: feature/calendar-and-settings

**Created:** 2026-03-29
**Status:** On hold — pushed to origin, ready to resume
**Branch point:** 42 commits ahead of origin/main at time of branch creation

---

## What Was Built

### 1. Completed Session Multi-Action Modal

Replaced the single "Modify" button on completed calendar sessions with a 3-option modal:

- **Modify date/time** — reschedule the session (existing flow)
- **Mark as not done** — reopens the session back to `scheduled` status
- **Cancel session (refund credit)** — cancels and refunds the client's credit

Key changes:
- `calendar_crud.reopen_session()` — new function, validates `completed` status before reverting
- `calendar_crud.cancel_session()` — extended to handle `completed` status (was `scheduled`-only); scope=future sibling query also updated
- `POST /api/sessions/{session_id}/reopen` — new route
- `templates/calendar.html` — `completedActionsModal`, `openCompletedActions()` function
- 6 new tests in `tests/test_calendar_crud.py` (28 total passing)

### 2. Admin Settings: Auto-Complete Past Sessions

New `AppSetting` key-value table stores persistent admin configuration.

- Toggle in `/admin/settings`: auto-marks past-scheduled sessions as completed when the calendar loads
- `auto_complete_past_sessions()` runs lazily on `GET /api/calendar/events` when enabled
- Migration: `alembic/versions/8a6f4103eec8_app_settings.py` — creates `app_settings` table, seeds `auto_complete_sessions = 'false'`
- `POST /api/admin/settings` uses a `VALID_KEYS` allowlist (prevents arbitrary key injection)

### 3. Gym Hours & Days Setting

Admin can set the calendar's visible time range and open days.

- Settings: `gym_open_time` (HH:MM), `gym_close_time` (HH:MM), `gym_closed_days` (comma-separated 0–6, where 0=Sun)
- Stored in `app_settings`, seeded with sensible defaults (06:00–22:00, no closed days)
- Calendar reads them at page load: `slotMinTime`, `slotMaxTime`, `hiddenDays` on FullCalendar config
- "Save hours & days" button on settings page saves all three in parallel

### 4. Calendar Visual Redesign

Custom event cards replacing FullCalendar's default rendering:

- Top line: **client name** (bold)
- Second line: time range (smaller text) — omitted for ≤30min sessions to avoid overflow
- Top-right badge: trainer initials in a colored circle (hash-based stable color per trainer)

Key implementation:
- `eventContent` callback in `calendar.html`
- Helper functions: `getInitials()`, `getTrainerColor()` (8-color palette), `escHtml()`, `formatTimeRange()`
- Short-event detection: `durationMs <= 30 * 60 * 1000` → skip time row, apply `.fc-custom-event--short` class
- `displayEventTime: false` to prevent FullCalendar injecting its own time display

---

## What's On Hold: Client Management

**Spec:** `docs/superpowers/specs/2026-03-29-client-management-design.md`

The design is fully approved and written. No code has been written yet.

### Summary of What Needs to Be Built

Replace the standalone Invites page with a unified Client Management section. Admins can:
- Invite clients (with optional name pre-fill)
- View, edit, deactivate/reactivate all clients
- Revoke pending invites

### Files to Create/Modify

| File | Change |
|------|--------|
| `gym_tracker/models.py` | Add 5 columns to `User`, 2 to `UserInvite` |
| `gym_tracker/schemas.py` | Update `InviteCreate`; add `ClientUpdate` |
| `gym_tracker/invite_crud.py` | `create_invite` accepts `first_name`, `last_name` |
| `gym_tracker/auth.py` | Name priority logic on login callback |
| `alembic/versions/<rev>_client_management.py` | Migration |
| `main.py` | New routes, remove old invites route, update console card |
| `templates/admin/clients.html` | New unified page (create) |
| `templates/admin/invites.html` | Delete |
| `templates/admin/index.html` | Update Client Management card link |
| `templates/_nav.html` | Remove "Invites" link |

### New DB Columns

**`user_invites`:** `first_name VARCHAR(100)`, `last_name VARCHAR(100)`

**`users`:** `name_override BOOLEAN NOT NULL DEFAULT FALSE`, `phone VARCHAR(30)`, `date_of_birth DATE`, `emergency_contact VARCHAR(255)`, `notes TEXT`

### Name Priority Logic (auth.py)

On new user creation:
```python
if invite.first_name or invite.last_name:
    full_name = f"{invite.first_name or ''} {invite.last_name or ''}".strip()
    name_override = True
else:
    full_name = google_full_name
    name_override = False
```

On existing user login:
```python
if not user.name_override:
    user.full_name = google_full_name or user.full_name
```

### New Routes

- `GET /admin/clients` — renders clients.html; passes `clients`, `pending_invites`, `trainers`, `current_user`
- `PUT /api/admin/clients/{user_id}` — updates client profile; setting name always sets `name_override = True`
- Remove `GET /admin/invites`
- `POST /api/invites` — gains optional `first_name`, `last_name` fields (existing route, extended)

### To Resume

1. Read the full spec: `docs/superpowers/specs/2026-03-29-client-management-design.md`
2. Run the writing-plans skill to create a detailed implementation plan (or use the spec directly with subagent-driven-development)
3. Start with the Alembic migration, then models → schemas → invite_crud → auth → routes → templates

---

## Planned But Not Designed Yet

These were mentioned by the user but not scoped:

- **Calendar filters** — filter by trainer(s) and/or client(s)
- **Client stats in admin** — admin sees client's own stats view (from Client Management page)
- **Package purchase** — admin can add/purchase a package for a client
- **Trainer admin features** — extend some admin functionality to trainers
