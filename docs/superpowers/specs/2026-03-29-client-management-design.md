# Client Management Design

**Date:** 2026-03-29
**Status:** Approved

---

## Goal

Replace the standalone Invites page with a unified Client Management section in the Admin Console. Admins can invite new clients (with optional name pre-set), view and edit all client profiles, deactivate/reactivate clients, and revoke pending invites — all from one page.

---

## Data Model Changes

### `user_invites` table — new columns

| Column | Type | Nullable | Notes |
|--------|------|----------|-------|
| `first_name` | VARCHAR(100) | YES | Optional; set at invite time |
| `last_name` | VARCHAR(100) | YES | Optional; set at invite time |

### `users` table — new columns

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `name_override` | BOOLEAN | NO | FALSE | When true, Google login never updates `full_name` |
| `phone` | VARCHAR(30) | YES | NULL | Mandatory on client edit form but nullable in DB |
| `date_of_birth` | DATE | YES | NULL | |
| `emergency_contact` | VARCHAR(255) | YES | NULL | |
| `notes` | TEXT | YES | NULL | Free-text admin notes |

### Name priority rule

- **Invite with name set** → on first login, `full_name = "{first_name} {last_name}".strip()` and `name_override = True`. Google's name is ignored.
- **Invite without name** → on first login, `full_name` comes from Google account (existing behaviour).
- **Admin edits name later** → sets `name_override = True`. Google login will never overwrite it again.
- **Admin clears name override** → not supported in this version; admin must always set a new name (cannot revert to Google name sync).
- **Subsequent Google logins** → `full_name` update is skipped if `user.name_override` is true.

---

## Removed

- `GET /admin/invites` page route
- "Invites" link in `_nav.html` admin nav section
- `templates/admin/invites.html` (replaced by `templates/admin/clients.html`)

**Kept as-is:** `DELETE /api/invites/{invite_id}` — revoke still works the same way, called from the new clients page.

---

## Updated

### `POST /api/invites`

Request body gains two optional fields:

```json
{
  "email": "client@example.com",
  "role": "client",
  "trainer_id": null,
  "first_name": "Ana",
  "last_name": "Costa"
}
```

`first_name` and `last_name` are stored on the `UserInvite` row. If either is set, `full_name` is pre-populated and `name_override = True` on first login.

### `schemas.InviteCreate`

Add `first_name: Optional[str] = None` and `last_name: Optional[str] = None`.

### `gym_tracker/auth.py` — callback flow

On **new user creation**:
```
if invite.first_name or invite.last_name:
    full_name = f"{invite.first_name or ''} {invite.last_name or ''}".strip()
    name_override = True
else:
    full_name = google_full_name
    name_override = False
```

On **existing user login**:
```
if not user.name_override:
    user.full_name = google_full_name or user.full_name
```
(email, avatar_url, last_login_at still update as before)

### Admin Console index card

"Client Management" card button changes from disabled `Coming Soon` to `<a href="/admin/clients">Manage Clients</a>`.

---

## New

### `GET /admin/clients`

Server-renders `templates/admin/clients.html`. Passes to template:
- `clients` — all `User` rows with `role="client"`, ordered: active first, then inactive
- `pending_invites` — all `UserInvite` rows where `accepted_at IS NULL`, ordered by `created_at DESC`
- `trainers` — all active trainers (for the Add Client modal)
- `current_user`

### `PUT /api/admin/clients/{user_id}`

Admin-only. Updates the following fields on a `User`:

| Field | Type | Required |
|-------|------|----------|
| `first_name` | str | yes (together with last_name) |
| `last_name` | str | yes (together with first_name) |
| `phone` | str | yes |
| `date_of_birth` | date (ISO 8601) | no |
| `emergency_contact` | str | no |
| `notes` | str | no |
| `is_active` | bool | no (deactivate/reactivate) |

Setting `first_name` or `last_name` always sets `name_override = True`.

New `ClientUpdate` Pydantic schema:
```python
class ClientUpdate(BaseModel):
    first_name: str
    last_name: str
    phone: Optional[str] = None
    date_of_birth: Optional[date] = None
    emergency_contact: Optional[str] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None
```

Returns `{"ok": True}`.

---

## UI — `/admin/clients`

### Table layout

Three logical groups rendered in one table, each group alphabetical by name:

1. **Active** — `User.is_active = True`, `role = "client"`
2. **Pending** — `UserInvite.accepted_at IS NULL`
3. **Inactive** — `User.is_active = False`, `role = "client"`

| Name | Email | Phone | Status | Joined | Actions |
|------|-------|-------|--------|--------|---------|
| Ana Costa | ana@gym.com | 555-1234 | ✓ Active | 2025-01-10 | Edit |
| Bob (invite) | bob@gym.com | — | ⏳ Pending | — | Revoke |
| Old Client | old@gym.com | 555-9999 | ✗ Inactive | 2024-06-01 | Edit |

- Active rows: normal styling
- Pending rows: muted, italic name if set, otherwise "—"
- Inactive rows: muted text, name with strikethrough

### "Add Client" modal

Fields: Email (required), First name (optional), Last name (optional), Role (default: client), Link to Trainer (optional). Submits to `POST /api/invites`.

### Edit modal (active/inactive clients)

Fields:
- First name (required)
- Last name (required)
- Phone (required)
- Date of birth (optional, date picker)
- Emergency contact (optional)
- Notes (optional, textarea)
- `🔒 Name is pinned` badge shown if `name_override = True`

Footer buttons:
- "Save" → `PUT /api/admin/clients/{user_id}`
- "Deactivate" (green → red, shown if active) / "Reactivate" (shown if inactive) → same endpoint with `{"is_active": false/true}`

---

## Migration

One Alembic migration (`client_management`):
- Add `first_name`, `last_name` to `user_invites`
- Add `name_override`, `phone`, `date_of_birth`, `emergency_contact`, `notes` to `users`
- Backfill: `UPDATE users SET name_override = 0` (safe default)

---

## Files Changed

| File | Action |
|------|--------|
| `gym_tracker/models.py` | Add 5 columns to `User`, 2 to `UserInvite` |
| `gym_tracker/schemas.py` | Update `InviteCreate`; add `ClientUpdate` |
| `gym_tracker/invite_crud.py` | Update `create_invite` to accept and store `first_name`, `last_name` |
| `gym_tracker/auth.py` | Apply name priority logic |
| `alembic/versions/<rev>_client_management.py` | Migration |
| `main.py` | Add `GET /admin/clients`, `PUT /api/admin/clients/{id}`; remove `GET /admin/invites`; update console card |
| `templates/admin/clients.html` | New unified page |
| `templates/admin/invites.html` | Delete |
| `templates/admin/index.html` | Update Client Management card |
| `templates/_nav.html` | Remove Invites link |

---

## Out of Scope

- Email notifications when an invite is sent (no email system in the app)
- Reverting `name_override` back to Google-sync mode
- Bulk deactivation
- Client-visible profile page
