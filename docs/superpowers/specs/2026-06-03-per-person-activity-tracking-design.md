# Per-Person Activity Tracking for Couples Sessions

**Date:** 2026-06-03
**Status:** Approved design

## Problem

A training session can be logged for two people (couples training). Today, activities
attach to a session as a whole — there is no way to record that person A did different
work than person B. We want optional per-person activity recording.

A second, related issue: only the user who logged a couples session can edit or delete
it. Either participant (owner or partner) should be able to.

## Current State

- `SessionActivity` rows attach to a `Session` via `session_id` only. Columns:
  `id, session_id, activity_id, values(JSON), notes, sort_order, created_at`. No
  per-person dimension.
- A couples session belongs to a `Purchase` with `num_people = 2`. The two participants:
  - **Owner** — `purchase.logged_by_user_id`.
  - **Partner** — `session.partner_user_id` (per-session override) or
    `purchase.partner_user_id`, or just `purchase.partner_email` when the partner has no
    account.
- Edit/delete guards in `main.py` (`api_edit_session`, `api_delete_session`) gate on
  `s.created_by_user_id != user_id` → 403.
- The duration-change branch of `api_edit_session` reallocates packs scoped to
  `logged_by_user_id == user_id`.
- `_annotate_session(sess, purchase, user_id)` already resolves owner/partner names.
- `reconcile_session_activities` (in `gym_tracker/activities.py`) manages the desired set
  of `SessionActivity` rows on create/edit.

## Part A — Per-Person Activity Tracking

### Data model

Add one nullable column to `session_activities`:

```
person_slot INT NULL    -- NULL = shared / whole-session (legacy + single-person default)
                        -- 1    = owner  (Person A)
                        -- 2    = partner (Person B)
```

No backfill. Existing rows stay `NULL` and render as "Both / Shared". Single-person
sessions always store `NULL`.

Alembic migration: add column `person_slot` (nullable Integer) to `session_activities`,
down-revision = current head (`ab12activity01_add_activity_tracking`). Confirm the actual
head with `alembic heads` before writing the migration.

### Schemas (`schemas.py`)

- `SessionActivityInput`: add `person_slot: int | None = None`.
- `SessionActivityRead`: add `person_slot: int | None` and resolved
  `person_name: str | None` (display label: owner name, partner name, or "Both / Shared").

### Validation rules

Enforced in `reconcile_session_activities` (it has the session + can load the purchase):

- `person_slot ∈ {None, 1, 2}` — reject other values (`ValueError` → 422/400 at the
  endpoint, matching existing reconcile error handling).
- Purchase `num_people <= 1` → force `person_slot = None` (reject 1 or 2).
- `person_slot == 2` requires a resolvable partner on the session/purchase
  (`session.partner_user_id`, `purchase.partner_user_id`, or `purchase.partner_email`);
  otherwise reject.

### Read annotation (`crud.py`)

Extend `_annotate_session_activities(db, sess)` to also set `person_slot` and
`person_name` on each `SessionActivity`. It must resolve names the same way
`_annotate_session` does:

- slot `1` → owner: `purchase.logged_by_user` name/email.
- slot `2` → partner: session partner override, else purchase partner, else
  `partner_email`.
- `NULL` → `"Both / Shared"`.

`_annotate_session_activities` currently takes only `(db, sess)`; it will need the
purchase (load via `db.get(models.Purchase, sess.purchase_id)`) to resolve names.

### UI

**Log-session modal partial** (activity logging partial, shared between create + edit):

- Couples session (`num_people > 1`): render two blocks —
  `Person A — <owner name>` and `Person B — <partner name>` — each with its own
  add-activity list. Each row carries a hidden `person_slot` (1 or 2). No "Shared" block
  for new entries.
- Single-person session: one block, no slot UI, sends `person_slot = null`.

**History view:** group a session's logged activities by person, mirroring the blocks.
Legacy `NULL` rows render under a read-only "Both / Shared" group.

**Edit-in-place:** preserve each row's existing `person_slot`. Legacy "Both / Shared"
rows may optionally be reassigned to A or B on edit, but are never forced.

## Part B — Edit/Delete by Any Participant

### Helper (`crud.py`)

```python
def session_participant_ids(session, purchase) -> set[int]:
    ids = {
        session.created_by_user_id,
        purchase.logged_by_user_id if purchase else None,
        session.partner_user_id,
        purchase.partner_user_id if purchase else None,
    }
    ids.discard(None)
    return ids

def user_can_edit_session(session, purchase, user_id) -> bool:
    return user_id in session_participant_ids(session, purchase)
```

partner_email-only partners have no user account and cannot log in, so they need no row.

### `main.py` changes

`api_edit_session` and `api_delete_session`:

- Load the purchase, replace the `created_by_user_id != user_id` 403 gate with
  `if not crud.user_can_edit_session(s, purchase, user_id): raise HTTPException(403)`.
- **Duration-change pack logic** (`api_edit_session`): scope the refund + reallocation to
  **`purchase.logged_by_user_id`** (the pack owner), not the editing `user_id`. The
  original-purchase refund, the new-pack lookup filter, and the
  "modify packs you don't own" guard all switch from `user_id` to the owner id. Result:
  whoever edits, packs always move against the owner's account.
- **Delete refund** (`api_delete_session`): refund to the funding purchase regardless of
  editor — drop the `purchase.logged_by_user_id == user_id` condition, keep the refund to
  that purchase.

Purchase edit/delete endpoints (`api_edit_purchase`, `api_delete_purchase`) are out of
scope — they remain owner-only.

## Testing

Per-person:
- `person_slot` persists on create and edit.
- `num_people == 1` forces `person_slot = NULL` (1/2 rejected/coerced).
- `person_slot == 2` with no partner → rejected (400/422).
- Read annotation resolves slot → correct owner/partner name; `NULL` → "Both / Shared".
- Legacy `NULL` rows still display (grouped as shared).

Edit/delete auth:
- Partner can edit a couples session.
- Partner can delete a couples session.
- Partner changing duration reallocates the **owner's** packs correctly (no false
  "no package available" / "packs you don't own").
- Non-participant still gets 403 on edit and delete.

Tests use in-memory SQLite + `StaticPool`; send `accept: application/json` to bypass the
login redirect (per existing test conventions).

## Out of Scope / YAGNI

- No "Shared" block for new couples entries (only legacy NULL rows are shared).
- No forced reassignment of legacy NULL rows.
- No per-person reporting/aggregation changes (only logging + display).
- Purchase edit/delete remain owner-only.
