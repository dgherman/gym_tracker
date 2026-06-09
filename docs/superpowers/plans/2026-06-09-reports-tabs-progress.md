# Reports Tabs + Progress (Per-User Activity Stats) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize `/reports` into Sessions/Billing/Progress tabs, add a per-user activity-stats "Progress" tab (summary table + trend chart), and add a shared "Custom" date range used by Reports and History — all mobile-friendly.

**Architecture:** New pure aggregation module `gym_tracker/progress.py` turns a user's attributed `session_activity` rows into `{summary, series}`. A new `crud.user_activity_rows` does the attribution query (reusing `_user_session_ids`). A new endpoint `GET /reports/progress/data` serves it. A shared Jinja script partial `_range_control.html` replaces the duplicated inline range JS in reports.html + history.html and adds Custom. `reports.html` becomes a tabbed shell.

**Tech Stack:** FastAPI, SQLAlchemy 1.4, Jinja2, vanilla JS + Bootstrap 5, Chart.js (already loaded on reports page), pytest + in-memory SQLite (StaticPool).

**Conventions (read before starting):**
- venv only: `.venv/bin/python -m pytest gym_tracker/tests/ -v` from repo root. Bare `python`/`pytest` not on PATH.
- Tests: in-memory SQLite + StaticPool; bypass login with header `accept: application/json`; dev-login is `GET /dev/login` gated by env `DEV_LOGIN=1`, logs in as `DEV_LOGIN_EMAIL` (must match a seeded user). Shared fixtures live in `gym_tracker/tests/conftest.py` (`client_factory`, `couples`) + `gym_tracker/tests/db_test_utils.py` (`TestSessionLocal`).
- No `StaticFiles` mount exists — share JS via a Jinja `{% include %}` script partial, like `templates/_activity_section.html`.
- No new DB columns. No Alembic migration in this plan.
- Per repo CLAUDE.md: changelog entry in README.md before push; documentation is delegated to the worker model (Task 9).
- Local manual testing: prod-copy DB has data for `thereallove@gmail.com` (owner) + `annemarie.hubbers@gmail.com` (partner), couples purchases 14 & 18.

---

## File Structure

- `gym_tracker/progress.py` — NEW. Pure aggregation: `PRIMARY_FIELD`, `NON_SUMMABLE`, `summarize(rows, fields_by_cat)`, format helpers. One responsibility: rows → {summary, series}.
- `gym_tracker/crud.py` — add `user_activity_rows(db, *, user_id, start, end)`.
- `main.py` — add `ProgressSummaryRow`/`ProgressData` Pydantic models + `GET /reports/progress/data`.
- `templates/_range_control.html` — NEW shared script partial: range pills incl. Custom + `getRange()` JS.
- `templates/reports.html` — tabbed shell (Sessions/Billing/Progress); use range partial; remove redundant 2nd `<nav>`.
- `templates/history.html` — use the range partial (gains Custom).
- `gym_tracker/tests/test_progress.py` — NEW. `summarize` + `user_activity_rows` + endpoint tests.
- `README.md` — changelog (Task 9).

---

## Task 1: `progress.summarize` — pure aggregation (primary field, best, additive total, latest, series)

**Files:**
- Create: `gym_tracker/progress.py`
- Test: `gym_tracker/tests/test_progress.py`

Rules (from spec, Option A):
- **primary numeric field** per category, by category slug: `strength→weight`, `cardio→distance`, `mobility→duration`; fallback = first numeric field by `sort_order`.
- numeric field_types = `integer`, `decimal`, `duration`. `text` (e.g. pace) is never numeric.
- **best** = max of the primary field (formatted with unit).
- **total** = Σ over *summable* fields, joined by " · " with units; a field is summable iff numeric AND its key not in `NON_SUMMABLE = {"weight"}`. If no summable field present → `None`.
- **latest** = the values of the most recent entry (by date), joined by " · " with units (this is where text fields like pace show).
- **series** = `{activity_name: {field_key: [{date, value}], ...}}` for numeric fields only, ordered by date, one point per entry.

- [ ] **Step 1: Write failing tests**

Create `gym_tracker/tests/test_progress.py`:

```python
from gym_tracker import progress


# Minimal field-meta stand-in (mirrors models.CategoryField attrs used by summarize)
class F:
    def __init__(self, key, field_type, unit=None, sort_order=0):
        self.key = key
        self.field_type = field_type
        self.unit = unit
        self.sort_order = sort_order


STRENGTH = [F("reps", "integer", None, 1), F("weight", "decimal", "lbs", 2)]
CARDIO = [F("distance", "decimal", "km", 1), F("duration", "duration", "min", 2), F("pace", "text", None, 3)]

# rows: each {session_date, activity_id, activity_name, category_id, category_slug, category_name, values}
def row(date, act, cat_id, slug, vals):
    return {"session_date": date, "activity_id": 1, "activity_name": act,
            "category_id": cat_id, "category_slug": slug, "category_name": slug.title(),
            "values": vals}


def test_strength_best_is_max_weight_total_excludes_weight():
    rows = [
        row("2026-01-01", "Bench Press", 1, "strength", {"reps": 8, "weight": 80}),
        row("2026-02-01", "Bench Press", 1, "strength", {"reps": 8, "weight": 100}),
    ]
    out = progress.summarize(rows, {1: STRENGTH})
    s = out["summary"][0]
    assert s["activity"] == "Bench Press"
    assert s["times"] == 2
    assert s["best"] == "100 lbs"        # max of primary field (weight)
    assert s["total"] == "16 reps"        # reps summed; weight excluded (NON_SUMMABLE)
    assert s["latest"] == "8 reps · 100 lbs"  # most recent entry's values


def test_cardio_total_sums_distance_and_duration_pace_only_in_latest():
    rows = [
        row("2026-01-01", "Bike", 2, "cardio", {"distance": 10, "duration": 30, "pace": "3:00/km"}),
        row("2026-02-01", "Bike", 2, "cardio", {"distance": 12, "duration": 35, "pace": "2:55/km"}),
    ]
    out = progress.summarize(rows, {2: CARDIO})
    s = out["summary"][0]
    assert s["best"] == "12 km"                       # primary = distance
    assert s["total"] == "22 km · 65 min"              # distance + duration summed; pace excluded
    assert "2:55/km" in s["latest"]                    # text field appears only in latest


def test_series_orders_numeric_fields_by_date_one_point_per_entry():
    rows = [
        row("2026-02-01", "Bench Press", 1, "strength", {"reps": 8, "weight": 100}),
        row("2026-01-01", "Bench Press", 1, "strength", {"reps": 8, "weight": 80}),
    ]
    out = progress.summarize(rows, {1: STRENGTH})
    series = out["series"]["Bench Press"]
    assert [p["value"] for p in series["weight"]] == [80, 100]   # date-ascending
    assert "pace" not in series                                   # no text series


def test_primary_fallback_first_numeric_when_slug_unknown():
    fields = [F("foo", "integer", None, 1), F("bar", "decimal", "x", 2)]
    rows = [row("2026-01-01", "Thing", 9, "weirdcat", {"foo": 3, "bar": 5})]
    out = progress.summarize(rows, {9: fields})
    assert out["summary"][0]["best"] == "3"   # first numeric by sort_order = foo
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest gym_tracker/tests/test_progress.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gym_tracker.progress'`.

- [ ] **Step 3: Implement `gym_tracker/progress.py`**

```python
"""Pure aggregation of a user's activity rows into Progress summary + series.

Input rows are dicts (see crud.user_activity_rows) and field metadata is a dict
{category_id: [CategoryField-like objects with .key/.field_type/.unit/.sort_order]}.
No DB access here — keeps it unit-testable."""

NUMERIC_TYPES = ("integer", "decimal", "duration")
# Fields whose running sum is meaningless (intensity/load, not additive volume).
NON_SUMMABLE = {"weight"}
# Primary (headline) numeric field per category slug; fallback = first numeric by sort_order.
PRIMARY_FIELD = {"strength": "weight", "cardio": "distance", "mobility": "duration"}


def _fmt(field, value):
    """Format a single value with its unit (no unit -> bare)."""
    unit = getattr(field, "unit", None)
    return f"{value} {unit}" if unit else f"{value}"


def _numeric_fields(fields):
    return [f for f in sorted(fields, key=lambda x: x.sort_order)
            if f.field_type in NUMERIC_TYPES]


def _primary_field(slug, fields):
    numeric = _numeric_fields(fields)
    if not numeric:
        return None
    want = PRIMARY_FIELD.get(slug)
    for f in numeric:
        if f.key == want:
            return f
    return numeric[0]


def summarize(rows, fields_by_cat):
    """rows: list of dicts. fields_by_cat: {category_id: [field-meta]}.
    Returns {"summary": [...], "series": {activity_name: {field_key: [{date,value}]}}}."""
    # group rows per activity (by name; names are unique within the library use here)
    by_activity = {}
    order = []
    for r in rows:
        name = r["activity_name"]
        if name not in by_activity:
            by_activity[name] = []
            order.append(name)
        by_activity[name].append(r)

    summary = []
    series = {}
    for name in order:
        entries = sorted(by_activity[name], key=lambda r: r["session_date"])
        cat_id = entries[0]["category_id"]
        slug = entries[0]["category_slug"]
        fields = fields_by_cat.get(cat_id, [])
        by_key = {f.key: f for f in fields}
        primary = _primary_field(slug, fields)

        # best = max of primary field across entries that have it
        best = None
        if primary is not None:
            vals = [e["values"][primary.key] for e in entries
                    if e["values"].get(primary.key) is not None]
            if vals:
                best = _fmt(primary, max(vals))

        # total = sum of each summable field present in any entry
        total_parts = []
        for f in _numeric_fields(fields):
            if f.key in NON_SUMMABLE:
                continue
            present = [e["values"][f.key] for e in entries if e["values"].get(f.key) is not None]
            if present:
                total_parts.append(_fmt(f, sum(present)))
        total = " · ".join(total_parts) if total_parts else None

        # latest = most recent entry's values (text fields included), in field sort order
        last = entries[-1]["values"]
        latest_parts = []
        for f in sorted(fields, key=lambda x: x.sort_order):
            if f.key in last and last[f.key] is not None and last[f.key] != "":
                latest_parts.append(_fmt(f, last[f.key]))
        latest = " · ".join(latest_parts) if latest_parts else None

        summary.append({
            "activity": name, "category": entries[0]["category_name"],
            "times": len(entries), "best": best, "total": total, "latest": latest,
        })

        # series: numeric fields only, date-ascending, one point per entry that has the value
        series[name] = {}
        for f in _numeric_fields(fields):
            pts = [{"date": e["session_date"], "value": e["values"][f.key]}
                   for e in entries if e["values"].get(f.key) is not None]
            if pts:
                series[name][f.key] = pts

    return {"summary": summary, "series": series}
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest gym_tracker/tests/test_progress.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add gym_tracker/progress.py gym_tracker/tests/test_progress.py
git commit -m "feat(progress): pure activity-stats aggregation (summary + series)"
```

---

## Task 2: `crud.user_activity_rows` — attribution query

**Files:**
- Modify: `gym_tracker/crud.py` (add near other report helpers, after `get_minutes_by_partner`)
- Test: `gym_tracker/tests/test_progress.py`

Attribution: a `session_activity` counts for `user_id` when, within a session visible to the
user (`_user_session_ids`), either (a) the purchase is solo (`num_people == 1`) — include
all its activity rows (slot is null), or (b) couples and `person_slot` equals the user's
slot in that session (1 if the user is the purchase owner, 2 if the user is the partner).
Couples rows with null slot are excluded.

- [ ] **Step 1: Write failing tests**

Append to `gym_tracker/tests/test_progress.py`. Reuse the shared `couples`/`client_factory`
fixtures from `conftest.py` and `TestSessionLocal` from `db_test_utils` (import them). The
`couples` fixture seeds owner (`owner@x.com`) + partner (`partner@x.com`) + a couples
session; you'll add activities directly via the ORM.

```python
import datetime
from gym_tracker import crud, models, activities as activities_mod, schemas
from gym_tracker.tests.db_test_utils import TestSessionLocal


def _seed_activity(db, session_id, activity_id, values, person_slot):
    db.add(models.SessionActivity(session_id=session_id, activity_id=activity_id,
                                  values=values, person_slot=person_slot, sort_order=0,
                                  created_at=datetime.datetime(2026, 1, 1)))


def test_user_activity_rows_couples_slot_attribution(couples):
    ids = couples._ids
    db = TestSessionLocal()
    sess = db.get(models.Session, ids["session"])
    sess.session_date = datetime.datetime(2026, 1, 15)
    _seed_activity(db, ids["session"], ids["act"], {"reps": 5}, 1)  # owner's
    _seed_activity(db, ids["session"], ids["act"], {"reps": 9}, 2)  # partner's
    db.commit()
    start, end = datetime.datetime(2026, 1, 1), datetime.datetime(2026, 2, 1)

    owner_rows = crud.user_activity_rows(db, user_id=ids["owner"], start=start, end=end)
    partner_rows = crud.user_activity_rows(db, user_id=ids["partner"], start=start, end=end)
    other_rows = crud.user_activity_rows(db, user_id=ids["outsider"], start=start, end=end)

    assert [r["values"]["reps"] for r in owner_rows] == [5]
    assert [r["values"]["reps"] for r in partner_rows] == [9]
    assert other_rows == []
    db.close()


def test_user_activity_rows_solo_counts_for_owner(client_factory):
    c = client_factory(num_people=1, with_partner=False)
    ids = c._ids
    db = TestSessionLocal()
    sess = db.get(models.Session, ids["session"])
    sess.session_date = datetime.datetime(2026, 1, 10)
    _seed_activity(db, ids["session"], ids["act"], {"reps": 7}, None)  # solo -> null slot
    db.commit()
    rows = crud.user_activity_rows(db, user_id=ids["owner"],
                                   start=datetime.datetime(2026, 1, 1),
                                   end=datetime.datetime(2026, 2, 1))
    assert [r["values"]["reps"] for r in rows] == [7]
    assert rows[0]["category_slug"]  # slug populated
    db.close()


def test_user_activity_rows_respects_date_range(client_factory):
    c = client_factory(num_people=1, with_partner=False)
    ids = c._ids
    db = TestSessionLocal()
    sess = db.get(models.Session, ids["session"])
    sess.session_date = datetime.datetime(2025, 12, 1)  # before range
    _seed_activity(db, ids["session"], ids["act"], {"reps": 7}, None)
    db.commit()
    rows = crud.user_activity_rows(db, user_id=ids["owner"],
                                   start=datetime.datetime(2026, 1, 1),
                                   end=datetime.datetime(2026, 2, 1))
    assert rows == []
    db.close()
```

NOTE: confirm the `couples`/`client_factory` fixtures expose `_ids` with keys
`owner`/`partner`/`outsider`/`session`/`act` (they do as of the per-person feature). If a
key differs, adapt the test to the actual fixture.

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest gym_tracker/tests/test_progress.py -k user_activity_rows -v`
Expected: FAIL — `AttributeError: module 'gym_tracker.crud' has no attribute 'user_activity_rows'`.

- [ ] **Step 3: Implement in `gym_tracker/crud.py`**

Add after `get_minutes_by_partner`:

```python
def _user_slot_in_session(sess, purchase, user_id):
    """Which person_slot belongs to user_id in this session: 1 if they are the
    purchase owner, 2 if they are the partner, else None."""
    if purchase and purchase.logged_by_user_id == user_id:
        return 1
    partner_id = (sess.partner_user_id
                  or (purchase.partner_user_id if purchase else None))
    if partner_id == user_id:
        return 2
    return None


def user_activity_rows(db: Session, *, user_id: int, start, end):
    """Activity rows attributable to user_id within [start, end].
    Solo sessions (num_people<=1): all rows (null slot). Couples: rows whose
    person_slot equals the user's slot. Couples null rows are excluded.
    Returns dicts consumed by gym_tracker.progress.summarize."""
    visible = _user_session_ids(db, user_id, start, end)
    sessions = (
        db.query(models.Session)
        .filter(models.Session.id.in_(select(visible.c.id)))
        .all()
    )
    rows = []
    for sess in sessions:
        purchase = db.get(models.Purchase, sess.purchase_id)
        num_people = purchase.num_people if purchase else 1
        my_slot = _user_slot_in_session(sess, purchase, user_id)
        for sa in sess.activities:
            if num_people <= 1:
                pass  # solo: include all
            elif sa.person_slot == my_slot and my_slot is not None:
                pass  # couples: my tagged rows
            else:
                continue
            activity = sa.activity or db.get(models.Activity, sa.activity_id)
            if not activity:
                continue
            category = db.get(models.ActivityCategory, activity.category_id)
            rows.append({
                "session_date": sess.session_date,
                "activity_id": activity.id,
                "activity_name": activity.name,
                "category_id": category.id if category else 0,
                "category_slug": category.slug if category else "",
                "category_name": category.name if category else "(unknown)",
                "values": sa.values or {},
            })
    return rows
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest gym_tracker/tests/test_progress.py -k user_activity_rows -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add gym_tracker/crud.py gym_tracker/tests/test_progress.py
git commit -m "feat(progress): user_activity_rows attribution query"
```

---

## Task 3: `GET /reports/progress/data` endpoint

**Files:**
- Modify: `main.py` (Pydantic models near other reports models ~line 110; route near `reports_data` ~line 426)
- Test: `gym_tracker/tests/test_progress.py`

- [ ] **Step 1: Write failing test**

Append to `gym_tracker/tests/test_progress.py`:

```python
def test_progress_endpoint_shape_and_attribution(couples):
    ids = couples._ids
    db = TestSessionLocal()
    sess = db.get(models.Session, ids["session"])
    sess.session_date = datetime.datetime(2026, 1, 15)
    _seed_activity(db, ids["session"], ids["act"], {"reps": 5}, 1)
    _seed_activity(db, ids["session"], ids["act"], {"reps": 9}, 2)
    db.commit(); db.close()

    # log in as the partner via dev-login; expect only the partner's row (reps=9)
    import os
    os.environ["DEV_LOGIN_EMAIL"] = "partner@x.com"
    couples.get("/dev/login")
    r = couples.get("/reports/progress/data?start=2026-01-01T00:00:00&end=2026-02-01T00:00:00",
                    headers={"accept": "application/json"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "summary" in body and "series" in body
    # partner sees their slot-2 row only -> times == 1
    assert body["summary"][0]["times"] == 1
```

(`couples` fixture must have `DEV_LOGIN=1` set in conftest — it does, from the per-person
work. If `partner@x.com` is not the seeded partner email, use the fixture's actual value.)

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest gym_tracker/tests/test_progress.py -k endpoint -v`
Expected: FAIL — 404 (route missing).

- [ ] **Step 3: Implement in `main.py`**

Add Pydantic models near the other reports models (after `ReportsData`):

```python
class ProgressSummaryRow(BaseModel):
    activity: str
    category: str
    times: int
    best: str | None = None
    total: str | None = None
    latest: str | None = None

class ProgressData(BaseModel):
    summary: List[ProgressSummaryRow]
    series: dict  # {activity: {field_key: [{date, value}]}}
```

Add the route near `reports_data` (ensure `from gym_tracker import progress` at top of file
alongside the existing `crud` import):

```python
@app.get("/reports/progress/data", response_model=ProgressData)
def reports_progress_data(
    request: Request,
    start: datetime = Query(...),
    end: datetime = Query(...),
    db: Session = Depends(get_db),
):
    user_id = request.session.get("user_id")
    rows = crud.user_activity_rows(db, user_id=user_id, start=start, end=end)
    cat_ids = {r["category_id"] for r in rows}
    fields_by_cat = {}
    for cid in cat_ids:
        fields_by_cat[cid] = (
            db.query(models.CategoryField)
            .filter(models.CategoryField.category_id == cid)
            .order_by(models.CategoryField.sort_order)
            .all()
        )
    return progress.summarize(rows, fields_by_cat)
```

Add `import` for `progress` at the top of `main.py` (find the line importing `crud` from
`gym_tracker` and add `progress`).

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest gym_tracker/tests/test_progress.py -v`
Expected: PASS (all progress tests).

- [ ] **Step 5: Full regression**

Run: `.venv/bin/python -m pytest gym_tracker/tests/ -v`
Expected: PASS (no regressions; 50 prior + new).

- [ ] **Step 6: Commit**

```bash
git add main.py gym_tracker/tests/test_progress.py
git commit -m "feat(progress): GET /reports/progress/data endpoint"
```

---

## Task 4: Shared range control partial (with Custom)

**Files:**
- Create: `templates/_range_control.html`
- Modify: `templates/reports.html`, `templates/history.html`

Goal: one script partial providing the range pills (incl. **Custom**) and a `getRange()`
function returning `{start, end}` ISO strings. Both pages include it and call `getRange()`.

- [ ] **Step 1: Create `templates/_range_control.html`**

```html
<!-- Shared date-range control. Host page must call RangeControl.init(onChange);
     onChange(range) fires with {start, end} ISO strings (or null start = all-time). -->
<div class="btn-group flex-wrap mb-2" id="rangeButtons" role="group">
  <button type="button" class="btn btn-outline-primary active" data-range="current_month">Current Month</button>
  <button type="button" class="btn btn-outline-primary" data-range="last_6_months">Last 6 Months</button>
  <button type="button" class="btn btn-outline-primary" data-range="last_12_months">Last 12 Months</button>
  <button type="button" class="btn btn-outline-primary" data-range="current_year">Current Year</button>
  <button type="button" class="btn btn-outline-secondary" data-range="custom">Custom</button>
</div>
<div id="customRange" class="d-none align-items-center gap-2 mb-2 flex-wrap">
  <span class="text-muted small">From</span>
  <input type="date" id="customStart" class="form-control form-control-sm" style="width:auto">
  <span class="text-muted small">To</span>
  <input type="date" id="customEnd" class="form-control form-control-sm" style="width:auto">
  <button type="button" id="customApply" class="btn btn-sm btn-success">Apply</button>
</div>

<script>
const RangeControl = (function () {
  let onChange = null;
  let current = 'current_month';

  function presetDates(range) {
    const now = new Date(), start = new Date();
    switch (range) {
      case 'current_month':  start.setDate(1); start.setHours(0,0,0,0); break;
      case 'last_6_months':  start.setMonth(now.getMonth()-5, 1); start.setHours(0,0,0,0); break;
      case 'last_12_months': start.setFullYear(now.getFullYear()-1, now.getMonth(), 1); start.setHours(0,0,0,0); break;
      case 'current_year':   start.setMonth(0, 1); start.setHours(0,0,0,0); break;
    }
    return { start: start.toISOString(), end: now.toISOString() };
  }

  function getRange() {
    if (current === 'custom') {
      const s = document.getElementById('customStart').value;
      const e = document.getElementById('customEnd').value;
      if (!s || !e) return null;
      return { start: new Date(s + 'T00:00:00').toISOString(),
               end:   new Date(e + 'T23:59:59').toISOString() };
    }
    return presetDates(current);
  }

  function init(cb) {
    onChange = cb;
    const btns = document.querySelectorAll('#rangeButtons button');
    const custom = document.getElementById('customRange');
    btns.forEach(b => b.addEventListener('click', () => {
      btns.forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      current = b.dataset.range;
      custom.classList.toggle('d-none', current !== 'custom');
      if (current !== 'custom') onChange(getRange());
    }));
    document.getElementById('customApply').addEventListener('click', () => {
      const r = getRange();
      if (r) onChange(r); else alert('Pick both From and To dates');
    });
    onChange(getRange()); // initial load (current_month)
  }

  return { init, getRange };
})();
</script>
```

- [ ] **Step 2: Wire into `templates/reports.html`**

Replace the existing `<div class="btn-group ..." id="rangeButtons">…</div>` block (the range
pills) with `{% include "_range_control.html" %}`. Then replace the existing range wiring
(the `buttons`/`currentRange`/`getDates` block and the `DOMContentLoaded` loader) so the
page loads via `RangeControl`:

```js
window.addEventListener('DOMContentLoaded', () => {
  initCharts();
  RangeControl.init((range) => loadReports(range));  // range = {start, end}
});
```

And change `loadReports(currentRange)` to accept `{start, end}` directly — update its fetch
to use `range.start`/`range.end` instead of calling `getDates`. Delete the now-unused
`getDates` function from reports.html.

- [ ] **Step 3: Wire into `templates/history.html`**

Same: replace its `#rangeButtons` block with `{% include "_range_control.html" %}`, delete
its local `getDates`/range wiring, and drive its session load through
`RangeControl.init((range) => loadHistory(range))`, using `range.start`/`range.end` in the
fetch. (Match the existing function name in history.html — read it first; it may be inline.)

- [ ] **Step 4: Verify templates compile + no backend regressions**

Run:
```
.venv/bin/python -c "from jinja2 import Environment,FileSystemLoader; e=Environment(loader=FileSystemLoader('templates')); [e.get_template(t) for t in ['_range_control.html','reports.html','history.html']]; print('OK')"
.venv/bin/python -m pytest gym_tracker/tests/ -q
```
Expected: `OK` and tests pass.

- [ ] **Step 5: Manual check (dev server)**

```
DEV_LOGIN=1 DEV_LOGIN_EMAIL=thereallove@gmail.com .venv/bin/python -m uvicorn main:app --port 8013
```
Visit `/dev/login` then `/reports` and `/history`: preset pills work as before; Custom
reveals date inputs; Apply reloads with the chosen window.

- [ ] **Step 6: Commit**

```bash
git add templates/_range_control.html templates/reports.html templates/history.html
git commit -m "feat(ui): shared range control with Custom range on reports + history"
```

---

## Task 5: Reports tabbed shell (Sessions / Billing / Progress)

**Files:**
- Modify: `templates/reports.html`

Restructure the body into 3 Bootstrap tabs under the page-level range. **Sessions** holds
the existing Total Time + the trainer/duration/partner charts; **Billing** holds Total Cost
+ sessions-remaining-per-package; **Progress** is a placeholder filled in Task 6. Remove the
redundant second `<nav class="navbar">…Gym Tracker…</nav>` (keep `_nav.html` + bottom Back).

- [ ] **Step 1: Add the tab markup**

Below `{% include "_range_control.html" %}`, add a Bootstrap nav-tabs bar + panes:

```html
<ul class="nav nav-tabs flex-nowrap overflow-auto mb-3" id="reportTabs" role="tablist">
  <li class="nav-item"><button class="nav-link active" data-bs-toggle="tab" data-bs-target="#tab-sessions" type="button">Sessions</button></li>
  <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#tab-billing" type="button">Billing</button></li>
  <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#tab-progress" type="button">Progress</button></li>
</ul>
<div class="tab-content">
  <div class="tab-pane fade show active" id="tab-sessions"><!-- Total Time + 3 charts move here --></div>
  <div class="tab-pane fade" id="tab-billing"><!-- Total Cost + packages-remaining move here --></div>
  <div class="tab-pane fade" id="tab-progress"><!-- Task 6 --></div>
</div>
```

Move the existing **Total Time** `<h3>` + the trainer/duration/partner chart `.row`s into
`#tab-sessions`. Move **Total Cost** `<h3>` into `#tab-billing`. Ensure Bootstrap JS bundle
is loaded (the page uses Bootstrap; confirm the `<script src=".../bootstrap.bundle...">` is
present — add it if only the CSS is linked, since tabs need the JS).

**Billing also gets sessions-remaining-per-package** (spec requirement; not surfaced today).
`crud.get_summary(db, user_id=...)` already returns `[{duration, num_people, remaining}]` but
`/reports/data` does not include it. Add it:

- In `main.py`, extend the `ReportsData` model with a field
  `sessions_remaining: List[dict] = []`, and in `reports_data` add
  `"sessions_remaining": crud.get_summary(db, user_id=user_id)` to the returned dict.
  (Note: `get_summary` is not date-filtered — remaining is a current snapshot; that's
  intended.)
- In `#tab-billing`, under Total Cost, add a packages table:
  ```html
  <h5 class="mt-3">Sessions remaining</h5>
  <div class="table-responsive">
    <table class="table table-sm" id="remainingTable">
      <thead><tr><th>Package</th><th>People</th><th>Remaining</th></tr></thead>
      <tbody></tbody>
    </table>
  </div>
  ```
- In `loadReports(range)`'s `.then(...)`, render it:
  ```js
  document.querySelector('#remainingTable tbody').innerHTML =
    (data.sessions_remaining || []).map(p =>
      `<tr><td>${p.duration} min</td><td>${p.num_people}</td><td>${p.remaining}</td></tr>`
    ).join('');
  ```

- [ ] **Step 2: Remove redundant nav**

Delete the `<nav class="navbar navbar-light mb-4"><a class="navbar-brand" href="/">Gym Tracker</a></nav>`
block (the one right after `{% include "_nav.html" %}`). Keep the page `<h1>Reports</h1>`
and the bottom `<a href="/" class="btn btn-secondary">Back</a>`.

- [ ] **Step 3: Verify charts still render after the move**

Chart.js `getContext` calls run on hidden tab panes too — Chart.js handles hidden canvases,
but confirm the canvases still have their ids and `initCharts()` still finds them. Compile
templates + run the dev server; on `/reports` the Sessions tab shows the 3 charts as before,
Billing shows Total Cost, tabs switch.

Run: `.venv/bin/python -c "from jinja2 import Environment,FileSystemLoader; FileSystemLoader; e=Environment(loader=FileSystemLoader('templates')); e.get_template('reports.html'); print('OK')"`

- [ ] **Step 4: Commit**

```bash
git add templates/reports.html
git commit -m "feat(ui): reports page split into Sessions/Billing/Progress tabs"
```

---

## Task 6: Progress tab — summary table + trend chart

**Files:**
- Modify: `templates/reports.html`

Fill `#tab-progress`: a summary table + a Chart.js line chart driven by activity + field
dropdowns; clicking a row loads that activity. Fetch `/reports/progress/data` on range
change (wire into the existing `RangeControl.init` callback so Progress reloads with the
range too).

- [ ] **Step 1: Add Progress markup into `#tab-progress`**

```html
<div id="progressEmpty" class="text-muted d-none">No activities logged in this range.</div>
<div class="table-responsive">
  <table class="table table-sm align-middle" id="progressTable">
    <thead><tr><th>Activity</th><th>Category</th><th>Times</th><th>Best</th><th>Total</th><th>Latest</th></tr></thead>
    <tbody></tbody>
  </table>
</div>
<div class="border rounded p-2 mt-2">
  <div class="d-flex justify-content-between align-items-center flex-wrap gap-2 mb-2">
    <strong>Trend</strong>
    <div class="d-flex gap-2">
      <select id="trendActivity" class="form-select form-select-sm" style="width:auto"></select>
      <select id="trendField" class="form-select form-select-sm" style="width:auto"></select>
    </div>
  </div>
  <canvas id="progressChart" height="120"></canvas>
</div>
```

- [ ] **Step 2: Add Progress JS (in the page `<script>`)**

```js
let progressData = { summary: [], series: {} };
let progressChart = null;

function loadProgress(range) {
  if (!range) return;
  fetch(`/reports/progress/data?start=${encodeURIComponent(range.start)}&end=${encodeURIComponent(range.end)}`,
        { headers: { accept: 'application/json' } })
    .then(r => r.json())
    .then(d => { progressData = d; renderProgress(); });
}

function renderProgress() {
  const tbody = document.querySelector('#progressTable tbody');
  const empty = document.getElementById('progressEmpty');
  const table = document.getElementById('progressTable');
  if (!progressData.summary.length) {
    empty.classList.remove('d-none'); table.classList.add('d-none');
    if (progressChart) { progressChart.destroy(); progressChart = null; }
    document.getElementById('trendActivity').innerHTML = '';
    document.getElementById('trendField').innerHTML = '';
    return;
  }
  empty.classList.add('d-none'); table.classList.remove('d-none');
  tbody.innerHTML = progressData.summary.map(s => `
    <tr style="cursor:pointer" data-activity="${escapeHtml(s.activity)}">
      <td><strong>${escapeHtml(s.activity)}</strong></td>
      <td class="text-muted">${escapeHtml(s.category)}</td>
      <td>${s.times}</td><td>${escapeHtml(s.best ?? '—')}</td>
      <td>${escapeHtml(s.total ?? '—')}</td><td>${escapeHtml(s.latest ?? '—')}</td>
    </tr>`).join('');
  tbody.querySelectorAll('tr').forEach(tr =>
    tr.addEventListener('click', () => selectActivity(tr.dataset.activity)));

  // activity dropdown
  const actSel = document.getElementById('trendActivity');
  actSel.innerHTML = progressData.summary.map(s => `<option>${escapeHtml(s.activity)}</option>`).join('');
  selectActivity(progressData.summary[0].activity);
}

function selectActivity(name) {
  document.getElementById('trendActivity').value = name;
  const fields = Object.keys(progressData.series[name] || {});
  const fieldSel = document.getElementById('trendField');
  fieldSel.innerHTML = fields.map(f => `<option>${escapeHtml(f)}</option>`).join('');
  drawTrend();
}

function drawTrend() {
  const name = document.getElementById('trendActivity').value;
  const field = document.getElementById('trendField').value;
  const pts = ((progressData.series[name] || {})[field]) || [];
  const ctx = document.getElementById('progressChart').getContext('2d');
  if (progressChart) progressChart.destroy();
  progressChart = new Chart(ctx, {
    type: 'line',
    data: { labels: pts.map(p => p.date.slice(0, 10)),
            datasets: [{ label: `${name} · ${field}`, data: pts.map(p => p.value),
                         borderColor: '#2d6cdf', tension: 0.2 }] },
    options: { responsive: true, maintainAspectRatio: false }
  });
}

document.getElementById('trendActivity').addEventListener('change', e => selectActivity(e.target.value));
document.getElementById('trendField').addEventListener('change', drawTrend);
```

Use the page's existing `escapeHtml` if present; if reports.html lacks one, add the standard
helper used in `_activity_section.html`.

- [ ] **Step 3: Wire Progress into the range callback**

Update the `RangeControl.init` callback so it loads all tabs:

```js
RangeControl.init((range) => { loadReports(range); loadProgress(range); });
```

- [ ] **Step 4: Manual verify (dev server, real data)**

```
DEV_LOGIN=1 DEV_LOGIN_EMAIL=thereallove@gmail.com .venv/bin/python -m uvicorn main:app --port 8013
```
`/dev/login` → `/reports` → Progress tab: table lists activities; clicking a row loads its
trend; field dropdown switches the series; pick a range with no activities → empty state.
(Seed a couple of activities on recent sessions if the range is empty.)

- [ ] **Step 5: Commit**

```bash
git add templates/reports.html
git commit -m "feat(ui): Progress tab — summary table + trend chart"
```

---

## Task 7: Mobile responsiveness pass

**Files:**
- Modify: `templates/reports.html` (+ `_range_control.html` if needed)

- [ ] **Step 1: Apply responsive fixes**

- Tabs bar already `flex-nowrap overflow-auto` (Task 5) — confirm it scrolls horizontally on
  narrow screens.
- Summary table already wrapped in `.table-responsive` (Task 6) — confirm horizontal scroll.
- Range pills `.btn-group flex-wrap` + custom inputs `flex-wrap` (Task 4) — confirm they wrap.
- Charts: the Sessions pies use a `.chart-container` with fixed width — make it fluid on
  mobile. In the `<style>`, change `.chart-container { width: 66%; }` (or similar) to:
  ```css
  .chart-container { width: 100%; }
  @media (min-width: 768px) { .chart-container { width: 66%; } }
  ```
- Ensure `<meta name="viewport" content="width=device-width, initial-scale=1">` is in
  `<head>` (add if missing).
- Progress chart canvas: set a wrapping div with a fixed height so `maintainAspectRatio:false`
  has a box, e.g. wrap `#progressChart` in `<div style="height:240px">`.

- [ ] **Step 2: Manual verify at phone width**

In the dev server, open `/reports`, set browser devtools to ~375px (iPhone SE). Check: tabs
scroll, pills wrap, Custom inputs stack, summary table scrolls horizontally without breaking
the page, both Sessions charts and the Progress trend fit the width and are readable.

- [ ] **Step 3: Commit**

```bash
git add templates/reports.html templates/_range_control.html
git commit -m "fix(ui): mobile-responsive reports tabs, tables, and charts"
```

---

## Task 8: Full verification

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/python -m pytest gym_tracker/tests/ -v`
Expected: PASS (50 prior + new progress tests).

- [ ] **Step 2: Compile all templates**

Run:
```
.venv/bin/python -c "import os; from jinja2 import Environment,FileSystemLoader; e=Environment(loader=FileSystemLoader('templates')); [e.get_template(f) for f in os.listdir('templates') if f.endswith('.html')]; print('all templates OK')"
```
Expected: `all templates OK`.

---

## Task 9: Changelog + docs (delegated)

**Files:**
- Modify: `README.md` (changelog — required by CLAUDE.md before push)

Per repo CLAUDE.md, documentation is delegated to the worker model.

- [ ] **Step 1: Generate the changelog entry via worker**

```bash
ask-kimi --paths docs/superpowers/specs/2026-06-09-reports-tabs-progress-design.md README.md \
  --question "Produce one README.md changelog entry dated 2026-06-09 titled 'Reports Tabs + Progress', matching the style of the existing entries. Cover: Reports page split into Sessions/Billing/Progress tabs; new Progress tab with per-user activity stats (summary table + trend chart) attributed via person_slot; shared date-range control with new Custom range on Reports and History; mobile-responsive. Tell me the exact insertion point (after which heading)."
```

- [ ] **Step 2: Apply the worker's suggested changelog edit** via the Edit tool (place above the most recent existing entry; match style).

- [ ] **Step 3: Final test run**

Run: `.venv/bin/python -m pytest gym_tracker/tests/ -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: reports tabs + Progress activity stats"
```

---

## Self-Review Notes

- **Spec coverage:** tabs IA (T5) · keep title/nav/Back, drop redundant navbar (T5) · page-level range + Custom shared to History (T4) · Progress attribution "just me", solo + couples slot, exclude couples-null (T2) · endpoint summary+series (T3) · Option A totals incl. weight→null, pace text→latest only (T1) · hybrid table "Times" + trend chart + row click + empty state (T6) · mobile (T7) · tests (T1–T3 backend; T4–T7 manual) · changelog (T9). All spec sections mapped.
- **Type consistency:** `user_activity_rows` row dict keys (`session_date`/`activity_name`/`category_id`/`category_slug`/`category_name`/`values`) are produced in T2 and consumed by `summarize` in T1 and the endpoint in T3 — consistent. Summary keys (`activity`/`category`/`times`/`best`/`total`/`latest`) match the Pydantic `ProgressSummaryRow` (T3) and the table render (T6). `RangeControl.init(onChange)` / `getRange()` consistent across T4/T5/T6.
- **Known soft spots:** (a) Tasks 4–7 are frontend with no JS test harness — each ends in a manual dev-server check; the implementer must actually run it. (b) reports.html/history.html exact current markup for the range block + loader function names must be read before editing (flagged inline in T4). (c) confirm Bootstrap JS bundle is loaded for tabs (flagged in T5).
