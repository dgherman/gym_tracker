# Two-Person Session Support — Design

## Summary

Enable shared gym sessions where a package purchased by one user can be used by two people. The buyer owns the cost; the partner sees the package and sessions in their account with $0 cost. Partner is specified by email and auto-linked when they sign up.

## Design Decisions

- **1 session consumed per visit** regardless of number of people
- **Default partner on purchase**, overridable per session
- **Partner sees purchase** in their dashboard (remaining sessions visible, cost = $0)
- **Partner specified by email** (free-text, no user dropdown for privacy)
- **Auto-link on signup**: if partner hasn't signed up yet, store email and link when they do
- **Both users can log sessions** against the shared purchase

## Data Model Changes

### Purchase table — new columns

| Column | Type | Notes |
|--------|------|-------|
| `num_people` | Integer, default 1 | Mirrors Package.num_people; marks this as a shared purchase |
| `partner_email` | String(255), nullable | Partner's email (always stored for reference) |
| `partner_user_id` | FK to users, nullable | Linked partner account (set when user exists or signs up) |

### Session table — new columns

| Column | Type | Notes |
|--------|------|-------|
| `partner_user_id` | FK to users, nullable | Per-session partner override. If NULL on a 2-person session, inherits from purchase |

### Auto-linking logic

In the OAuth callback (`auth.py`), after upserting the user: query for purchases where `partner_email = user.email` and `partner_user_id IS NULL`. Set `partner_user_id = user.id` for all matches.

## Query Changes

### Dashboard summary (`GET /summary/`)

Currently: purchases where `logged_by_user_id = current_user`.
New: purchases where `logged_by_user_id = current_user` **OR** `partner_user_id = current_user`.

Both buyer and partner see the same `sessions_remaining` count.

### Session history (`GET /history/sessions/`)

Currently: sessions where `created_by_user_id = current_user`.
New: sessions where `created_by_user_id = current_user` **OR** `partner_user_id = current_user` **OR** (session has no per-session partner AND the session's purchase has `partner_user_id = current_user`).

### Purchase history (`GET /history/purchases/`)

Currently: purchases where `logged_by_user_id = current_user`.
New: also include purchases where `partner_user_id = current_user`. For those, return `cost = 0`.

### Reports (`GET /reports/data`)

- **Minutes by trainer / duration**: include sessions where user is creator or partner.
- **Total cost**: only purchases where `logged_by_user_id = current_user` (unchanged). Partner sees $0 cost.

### Logging a session (`POST /sessions/`)

When creating a session, find available purchases matching the duration where `logged_by_user_id = current_user` **OR** `partner_user_id = current_user`. Both users can consume from the shared package.

If the purchase is `num_people >= 2`:
- Accept an optional `partner_email` in the request
- If provided and different from purchase default, resolve to user_id and store on session
- If not provided, inherit partner from the purchase

### Purchase creation (`POST /purchases/`)

When creating a purchase from a 2-person package:
- Accept `partner_email` in the request
- Store `num_people` from the selected package
- Attempt to resolve `partner_email` to an existing user -> set `partner_user_id`
- If no matching user, store `partner_email` only (auto-link later)

## UI Changes

### Dashboard (index.html)

**Purchase modal**: When a 2-person package is selected, show a "Partner email" text input. Required for 2-person packages.

**Log session modal**: When the selected duration matches a 2-person purchase, show a "Partner email" field pre-filled from the purchase's partner. Editable for per-session override.

### History (history.html)

**Purchase table**: Show a "Partner" column for 2-person purchases (email or name if linked). For the partner's view, show cost as "$0.00" and indicate "Shared by [buyer name]".

**Session table**: Show partner name/email on 2-person sessions.

### Reports (reports.html)

No structural changes. Partner's sessions automatically included in their reports. Cost remains buyer-only.

## Migration

Single Alembic migration adding:
- `purchases.num_people` (Integer, default 1, NOT NULL)
- `purchases.partner_email` (String(255), nullable)
- `purchases.partner_user_id` (FK to users, nullable)
- `sessions.partner_user_id` (FK to users, nullable)

## API Changes Summary

| Endpoint | Change |
|----------|--------|
| `POST /purchases/` | Accept `partner_email`, `num_people` |
| `POST /sessions/` | Accept optional `partner_email` for 2-person sessions |
| `GET /summary/` | Include partner purchases in remaining count |
| `GET /history/sessions/` | Include sessions where user is partner |
| `GET /history/purchases/` | Include partner purchases (cost = $0) |
| `GET /reports/data` | Include partner sessions in minutes; cost buyer-only |
| `GET /auth/callback` | Auto-link partner purchases on signup |
