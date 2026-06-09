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
    assert s["total"] == "16"             # reps summed; weight excluded (NON_SUMMABLE); no unit -> bare
    assert s["latest"] == "8 · 100 lbs"  # most recent entry's values; reps has no unit


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


def test_same_name_different_activity_id_produces_two_summary_rows():
    """Critical: two activities sharing a display name but with different activity_ids
    and category_ids must NOT merge into a single summary row."""
    STRENGTH2 = [F("reps", "integer", None, 1), F("weight", "decimal", "lbs", 2)]
    CARDIO2 = [F("distance", "decimal", "km", 1), F("duration", "duration", "min", 2)]

    def row2(date, act, act_id, cat_id, slug, vals):
        return {
            "session_date": date,
            "activity_id": act_id,
            "activity_name": act,
            "category_id": cat_id,
            "category_slug": slug,
            "category_name": slug.title(),
            "values": vals,
        }

    rows = [
        row2("2026-01-01", "Squat", 10, 1, "strength", {"reps": 5, "weight": 100}),
        row2("2026-01-02", "Squat", 20, 2, "cardio",   {"distance": 3, "duration": 15}),
    ]
    fields_by_cat = {1: STRENGTH2, 2: CARDIO2}
    out = progress.summarize(rows, fields_by_cat)

    assert len(out["summary"]) == 2, (
        f"Expected 2 summary rows for same name / different activity_id, got {len(out['summary'])}"
    )

    # Identify each row by category
    by_cat = {r["category"]: r for r in out["summary"]}
    assert "Strength" in by_cat and "Cardio" in by_cat

    # Strength row: best = max weight (100 lbs); total = reps only (weight is NON_SUMMABLE_KEYS)
    s_row = by_cat["Strength"]
    assert s_row["best"] == "100 lbs"
    assert s_row["total"] == "5"   # reps summed; weight excluded

    # Cardio row: best = distance (3 km); total = distance + duration
    c_row = by_cat["Cardio"]
    assert c_row["best"] == "3 km"
    assert c_row["total"] == "3 km · 15 min"


def test_integral_float_weight_trims_decimal():
    """Float values that are whole numbers must display without .0 (e.g. 100.0 → '100 lbs')."""
    rows = [
        row("2026-01-01", "Squat", 1, "strength", {"reps": 5, "weight": 80.0}),
        row("2026-02-01", "Squat", 1, "strength", {"reps": 5, "weight": 100.0}),
    ]
    out = progress.summarize(rows, {1: STRENGTH})
    s = out["summary"][0]
    assert s["best"] == "100 lbs", f"Expected '100 lbs', got {s['best']!r}"
    assert s["latest"] == "5 · 100 lbs", f"Expected '5 · 100 lbs', got {s['latest']!r}"


def test_fractional_float_distance_preserved():
    """Fractional float values must NOT be truncated (2.5 km stays '2.5 km')."""
    rows = [
        row("2026-01-01", "Run", 2, "cardio", {"distance": 2.5, "duration": 20, "pace": "4:00/km"}),
    ]
    out = progress.summarize(rows, {2: CARDIO})
    s = out["summary"][0]
    assert s["best"] == "2.5 km", f"Expected '2.5 km', got {s['best']!r}"
    assert "2.5 km" in s["latest"], f"Expected '2.5 km' in latest, got {s['latest']!r}"


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


def test_progress_endpoint_unauthenticated_returns_401():
    """No session cookie → endpoint must reject with 401, not leak rows."""
    from starlette.testclient import TestClient
    import sys, os
    # Ensure DEV_LOGIN is off so the middleware doesn't auto-pass JSON requests
    # (the guard in the route itself should fire regardless of middleware).
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
    import main as app_module
    anon_client = TestClient(app_module.app, raise_server_exceptions=False)
    r = anon_client.get(
        "/reports/progress/data?start=2026-01-01T00:00:00&end=2026-02-01T00:00:00",
        headers={"accept": "application/json"},
    )
    assert r.status_code == 401, f"Expected 401, got {r.status_code}: {r.text}"


def test_reports_data_unauthenticated_returns_401():
    """No session cookie → /reports/data must reject with 401."""
    from starlette.testclient import TestClient
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
    import main as app_module
    anon_client = TestClient(app_module.app, raise_server_exceptions=False)
    r = anon_client.get(
        "/reports/data?start=2026-01-01T00:00:00&end=2026-02-01T00:00:00",
        headers={"accept": "application/json"},
    )
    assert r.status_code == 401, f"Expected 401, got {r.status_code}: {r.text}"


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
