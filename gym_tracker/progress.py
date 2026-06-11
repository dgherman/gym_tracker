"""Pure aggregation of a user's activity rows into Progress summary + series.

Input rows are dicts (see crud.user_activity_rows) and field metadata is a dict
{category_id: [CategoryField-like objects with .key/.field_type/.unit/.sort_order]}.
No DB access here — keeps it unit-testable."""

NUMERIC_TYPES = ("integer", "decimal", "duration")
# These field KEYS are intensity/load metrics whose running sum is meaningless; a future
# category-level `summable` flag would generalize this instead of an explicit key list.
NON_SUMMABLE_KEYS = {"weight"}
# Primary (headline) numeric field per category slug; fallback = first numeric by sort_order.
PRIMARY_FIELD = {"strength": "weight", "cardio": "distance", "mobility": "duration"}


def _num(value):
    """Drop trailing .0 on integral floats; pass through ints, fractional floats, and non-numbers."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _fmt(field, value):
    """Format a single value with its unit (no unit -> bare)."""
    unit = getattr(field, "unit", None)
    v = _num(value)
    return f"{v} {unit}" if unit else f"{v}"


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
    # Group by (activity_id, activity_name): activity names are only unique PER category
    # (DB UniqueConstraint is on (category_id, name)), so two activities in different
    # categories can share a name and must stay as separate summary rows.
    by_activity = {}
    order = []
    for r in rows:
        key = (r["activity_id"], r["activity_name"])
        if key not in by_activity:
            by_activity[key] = []
            order.append(key)
        by_activity[key].append(r)

    summary = []
    series = {}
    for act_key in order:
        _act_id, name = act_key
        entries = sorted(by_activity[act_key], key=lambda r: r["session_date"])
        cat_id = entries[0]["category_id"]
        slug = entries[0]["category_slug"]
        fields = fields_by_cat.get(cat_id, [])
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
            if f.key in NON_SUMMABLE_KEYS:
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
            "primary_field": primary.key if primary is not None else None,
        })

        # series: keyed by display activity_name so the frontend can select by name.
        # If two activities ever share a name they share a series bucket — rare and
        # acceptable; the summary table still shows both rows correctly via act_key.
        series[name] = {}
        for f in _numeric_fields(fields):
            # rid = source row id so the frontend can align fields per entry
            # (several entries can share one session_date / day).
            pts = [{"date": e["session_date"], "value": e["values"][f.key],
                    "rid": e.get("row_id")}
                   for e in entries if e["values"].get(f.key) is not None]
            if pts:
                series[name][f.key] = pts

    return {"summary": summary, "series": series}
