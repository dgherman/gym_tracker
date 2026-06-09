# Reports Redesign: Tabs + Progress (Per-User Activity Stats)

**Date:** 2026-06-09
**Status:** Approved design

## Problem

The `/reports` page is a flat list of session/cost charts. We want to (a) reorganize it
into tabs, (b) add per-user **activity** statistics ("Progress") built from logged
activities, and (c) add a "Custom" date range. Progress must work whether activities were
logged in a solo or a couples session.

## Current State

- `/reports` (`templates/reports.html`) includes `_nav.html` (blue "Welcome, {name}!"
  banner + Menu burger) **and** a redundant second `<nav>Gym Tracker</nav>`. It has a
  date-range pill group (`current_month`, `last_6_months`, `last_12_months`,
  `current_year`) whose date math lives inline in JS (`getDates`), a Total Cost / Total
  Time summary, and three Chart.js pies: minutes by trainer, by duration, by partner.
- Reached via a "View Reports" link on the home page.
- Backend `GET /reports/data` aggregates via `crud.get_training_by_trainer`,
  `crud.get_minutes_by_partner`, `crud.get_summary` (sessions remaining per package).
- `/history` has its own range selector.
- `session_activities` has `person_slot` (1=owner, 2=partner, null=shared/solo),
  `values` (JSON per `category_fields.key`), and links session → purchase. Category fields
  (prod): Strength→reps(int), weight(lbs, decimal); Cardio→distance(km), duration(min),
  pace(text); Mobility→duration(min).

## Design

### 1. Page structure (IA)

`/reports` keeps the title **"Reports"** and its "View Reports" entry point. Keep
`_nav.html` and the bottom **Back** button. **Remove** the redundant second `<nav>`.

Body becomes three tabs, with a **page-level date range above the tabs** that drives all
three and persists across tab switches (default `current_month`):

- **Sessions** — today's content: Total Time, minutes by trainer / duration / partner.
- **Billing** — Total Cost + sessions-remaining per package (`get_summary`).
- **Progress** — new per-user activity stats (below).

### 2. Date range + Custom

Pills: Current Month / Last 6 Months / Last 12 Months / Current Year / **Custom**.
Selecting **Custom** reveals `from` / `to` date inputs + **Apply**; the chosen range is
sent to the data endpoints as explicit `start`/`end`.

Extract the range logic (currently inline `getDates` in reports.html) into a shared JS
helper used by **Reports and History** so both get Custom. The helper maps a range key →
`{start, end}` ISO dates; Custom passes the inputs through. History adopts the same pill
set + Custom.

### 3. Progress — attribution (read-only, NO schema change)

Progress shows **only the logged-in user's** activities ("Just me"). A `session_activity`
counts for user `U` when its effective user is `U`:

- **Solo** session owned by `U` (`purchase.logged_by_user_id == U` and
  `purchase.num_people == 1`); `person_slot` is null here.
- **Couples** row whose slot resolves to `U`: `person_slot == 1` →
  `purchase.logged_by_user_id`; `person_slot == 2` → the partner
  (`session.partner_user_id` else `purchase.partner_user_id`).

Couples rows with `person_slot` null are **excluded** (ambiguous legacy; none exist in
data today). No new column — attribution is computed at query time.

New crud helper:
```
user_activity_rows(db, user_id, start, end) -> list of rows, each:
  { session_date, activity_id, activity_name, category_name,
    category_id, values(dict) }
```
SQL: `session_activities` JOIN `sessions` JOIN `purchases`, filtered to the user's
effective rows (the slot logic above) and `sessions.session_date` in `[start, end]`.

### 4. Backend endpoint

`GET /reports/progress/data?range=<key>&start=<iso>&end=<iso>` (auth required; uses
session `user_id`). Returns:

```json
{
  "summary": [
    { "activity": "Bench Press", "category": "Strength", "times": 12,
      "best": "100 lbs", "total": null, "latest": "95 lbs · 8 reps" }
  ],
  "series": {
    "Bench Press": { "weight": [ {"date":"2026-01-04","value":80}, ... ],
                     "reps":   [ ... ] }
  }
}
```

Aggregation rules (per activity, over the user's rows in range):

- **times** = count of entries.
- **best** = max of the activity's *primary numeric field* (Strength→weight,
  Cardio→distance, Mobility→duration), formatted with unit.
- **total** (Option A): Σ for *additive* numeric fields (reps, distance, duration),
  formatted with unit; for `weight` → `null` ("—" in UI, since Σweight is meaningless);
  multiple additive fields joined (e.g. "142 km · 240 min").
- **latest** = the most recent entry's values, joined (e.g. "95 lbs · 8 reps"); the only
  place text fields like `pace` appear.
- **series**: per activity, per *numeric* field → `[{date, value}]` ordered by
  `session_date` (one point per entry; multiple same day → multiple points). `pace` (text)
  is not charted.

Number formatting and primary-field mapping live in one place (a small helper) so the
summary and series stay consistent.

### 5. Frontend — Progress tab (hybrid)

- **Summary table** on top: columns Activity · Category · **Times** · Best · Total ·
  Latest. (Note: "Logged" was renamed to **"Times"**.)
- **Trend graph** below: Chart.js line over time, with an **activity** dropdown and a
  **field** dropdown (numeric fields of the chosen activity). Clicking a table row loads
  that activity into the graph. Defaults to the first activity with data + its primary
  field.
- **Empty state**: "No activities logged in this range."
- Reuse the Chart.js already loaded on the reports page.

### 6. Mobile (hard requirement)

The whole page must render and function on a phone browser:

- Tab bar horizontally scrollable / wrapping; date pills wrap; Custom inputs stack.
- Summary table wrapped in a horizontally-scrollable container (`overflow-x:auto`) so 6
  columns don't break layout.
- Chart.js charts `responsive: true, maintainAspectRatio` tuned so they fit narrow
  widths.
- Verified manually at ~375px width (see Testing).

### 7. Code organization

- `reports.html` grows; split sensibly: the tabbed shell, a shared range-control JS
  snippet (also imported by `history.html`), and one render function per tab. Don't
  over-split — match existing template style.
- New crud progress helpers grouped near the existing report helpers
  (`get_training_by_trainer`, `get_minutes_by_partner`, `get_summary`).

## Testing

Backend (pytest, in-memory SQLite + StaticPool, `accept: application/json`):

- `user_activity_rows`: solo rows counted for owner; couples slot-1 → owner only;
  couples slot-2 → partner only; partner's query returns only their slot; a third user
  (non-participant) sees none; range filtering on `session_date`; couples-null excluded.
- Aggregation: `times`/`best`/`total`/`latest` correct per field type; weight `total` is
  null; additive fields summed; text field (`pace`) only in `latest`, never best/total;
  `series` ordered by date with one point per entry.
- Endpoint `GET /reports/progress/data`: auth required; returns `summary`+`series` shape;
  respects `range` keys and explicit `start`/`end` (Custom).

Frontend (no JS test harness → manual, against the prod-copy local DB which has data for
`thereallove@gmail.com` + `annemarie.hubbers@gmail.com`):

- Tabs switch; range (incl. Custom) drives all three; range persists across tabs.
- Progress: table + graph render; row click loads graph; field dropdown switches series;
  empty state on a range with no activities.
- Mobile at ~375px: tabs/pills/table/chart all usable, no overflow breakage.

## Out of Scope / YAGNI

- No partner/other-user progress view (just me). No `participant_user_id` column.
- No training-volume weight total (Option B rejected; weight total = "—").
- No CSV export, no goal-setting, no PRs-over-time beyond the single trend chart.
- Sessions/Billing tab content is the existing reports content reorganized — no new
  session/billing analytics in this spec.
