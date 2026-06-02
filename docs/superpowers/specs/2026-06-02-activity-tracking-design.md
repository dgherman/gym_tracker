# Activity Tracking — Design

**Date:** 2026-06-02
**Status:** Approved (design), pending implementation plan
**Scope:** Logging + basic display. Progress charts deferred to a later spec.

## Goal

When a user logs a gym session, an optional section lets them record the
activities they did. Activities are grouped by category (Strength, Cardio,
etc.). A user picks from the existing global activity library or creates a new
activity inline. Any activity created by any user becomes available to all
users immediately.

## Decisions (from brainstorming)

- **Per-activity data:** structured metrics per category (e.g. Strength →
  reps/weight; Cardio → distance/duration/pace).
- **Categories:** admin-managed. App ships a seeded set; admins can add
  categories and define each category's metric fields.
- **Granularity:** one row of values per logged activity per session (no
  per-set repetition).
- **Scope:** capture + display in session history. No progress charts yet.
- **Activity governance:** any user creates a global activity; admins can
  rename / deactivate (soft delete).

## Data Model

Four new tables. Follows existing patterns: integer PK, `is_active` soft
delete, nullable `*_user_id` ownership FKs, `created_at` timestamps, alembic
migration.

### `activity_categories`
| column | type | notes |
|---|---|---|
| id | int PK | |
| name | str | unique |
| slug | str | unique, derived from name |
| is_active | bool | default true |
| sort_order | int | display order |
| created_at | datetime | |

Seed: Strength, Cardio, Mobility, Other.

### `category_fields`
Admin-defined metric schema for a category.

| column | type | notes |
|---|---|---|
| id | int PK | |
| category_id | int FK → activity_categories | |
| key | str | slug, unique per category (e.g. `weight`) |
| label | str | display label (e.g. "Weight") |
| field_type | str | one of `integer`, `decimal`, `duration`, `text` |
| unit | str? | nullable (e.g. `kg`, `km`) |
| is_required | bool | default false |
| is_active | bool | default true (soft delete — keeps old logs renderable) |
| sort_order | int | |
| created_at | datetime | |

`duration` stored as integer seconds; rendered as `mm:ss`.

Seed fields:
- Strength → `reps` (integer, required), `weight` (decimal, unit kg)
- Cardio → `distance` (decimal, unit km), `duration` (duration), `pace` (text)
- Mobility → `duration` (duration)
- Other → (no fields; free-form via note)

### `activities`
Global activity library.

| column | type | notes |
|---|---|---|
| id | int PK | |
| category_id | int FK → activity_categories | |
| name | str | indexed |
| is_active | bool | default true |
| created_by_user_id | int? FK → users | nullable ownership |
| created_at | datetime | |

Constraint: `UNIQUE(category_id, lower(name))` — case-insensitive dedup within
a category.

### `session_activities`
One row per logged activity per session.

| column | type | notes |
|---|---|---|
| id | int PK | |
| session_id | int FK → sessions | |
| activity_id | int FK → activities | |
| values | JSON | `{ field.key: value }`, validated against the category's fields at write |
| notes | str? | nullable free text |
| sort_order | int | |
| created_at | datetime | |

**Why JSON, not EAV:** scope is one-row-per-activity + basic display, no
cross-session aggregate queries yet. JSON keeps the model small. If/when
progress charts arrive and need to query a single field over time, revisit
(either JSON extraction or migrate to an EAV value table).

## API

Read (any authenticated user):
- `GET /api/categories` → categories with their active fields (drives form rendering)
- `GET /api/activities?category_id=` → active activities in a category
- `POST /api/activities` → create a global activity `{category_id, name}`;
  returns existing on dedup match

Session create (extended): the existing session-create endpoint accepts an
optional `activities[]` array of `{activity_id, values, notes}`. Session and
its `session_activities` rows are created in a single transaction.

Admin-only (`/api/admin/*`, role check like existing admin endpoints):
- `POST /api/admin/categories`, `PATCH /api/admin/categories/{id}`
- `POST /api/admin/categories/{id}/fields`, `PATCH`/`DELETE` `.../fields/{fid}`
  (DELETE is soft — sets `is_active=false`)
- `PATCH /api/admin/activities/{id}` → rename / deactivate

## UI

### Session log form (`templates/index.html`)
- New collapsible **"Log activities (optional)"** section, collapsed by default
  — zero friction when unused.
- Add flow: pick category → search/select activity (or inline "+ New activity")
  → dynamic fields rendered from the category's `category_fields` → optional
  note → "Add to session". Repeats for multiple activities; added rows listed
  with a remove control.
- Inline create: typing an unknown name offers create; `POST /api/activities`,
  auto-selects the new activity.
- Activities are submitted together with the session (add-at-log-time).
  Editing a past session's activities is out of scope for this spec.

### History (`templates/history.html`)
- Each session shows its logged activities grouped by category, values rendered
  from the JSON + field labels/units (e.g. "Bench Press — 8 × 60kg").

### Admin (`templates/admin/`)
- New **Activities** admin tab: list/add categories, manage a category's fields
  (add/edit/soft-delete), list activities per category with rename/deactivate.

## Validation & Edge Cases

- **Server-side value validation** against `category_fields`: required fields
  present; type coercion (integer / decimal / duration-seconds / text); unknown
  keys rejected.
- **Field soft-delete:** deactivating a field never destroys historical
  `values` JSON — old logs still render.
- **Duplicate activity name:** `UNIQUE(category_id, lower(name))`; inline-create
  returns the existing activity instead of erroring.
- **Atomicity:** session + `session_activities` created in one transaction; a
  bad activity row fails the whole log with a clear error.
- **Empty section:** logging zero activities is valid and unchanged from today.

## Testing

CRUD tests:
- create activity (new + dedup returns existing)
- log session with activities (values persisted, txn atomic)
- value validation: required missing → error, wrong type → error, unknown key → error
- soft-deleted field still renders in existing logs

Admin tests:
- non-admin blocked on `/api/admin/*`
- create category, create/soft-delete field, rename/deactivate activity

## Out of Scope (future specs)

- Progress charts / per-activity trends over time
- Editing activities on a past session
- Per-set / interval repetition (multiple rows per activity)
- Merging duplicate activities
