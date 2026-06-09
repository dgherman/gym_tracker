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
