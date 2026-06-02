import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from gym_tracker.database import Base
from gym_tracker import models, activities, schemas

test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture
def db():
    Base.metadata.create_all(bind=test_engine)
    db = TestSessionLocal()
    # Seed a Strength category with reps (required int) + weight (decimal)
    cat = models.ActivityCategory(name="Strength", slug="strength", sort_order=1)
    db.add(cat)
    db.flush()
    db.add_all([
        models.CategoryField(category_id=cat.id, key="reps", label="Reps",
                             field_type="integer", is_required=True, sort_order=1),
        models.CategoryField(category_id=cat.id, key="weight", label="Weight",
                             field_type="decimal", unit="kg", is_required=False, sort_order=2),
    ])
    act = models.Activity(category_id=cat.id, name="Bench Press")
    db.add(act)
    db.commit()
    db.refresh(act)
    try:
        yield db, act
    finally:
        db.close()
        Base.metadata.drop_all(bind=test_engine)


def test_valid_values_are_coerced(db):
    db_, act = db
    cleaned = activities.validate_activity_values(db_, act, {"reps": "8", "weight": "60.5"})
    assert cleaned == {"reps": 8, "weight": 60.5}


def test_missing_required_field_raises(db):
    db_, act = db
    with pytest.raises(ValueError, match="Reps"):
        activities.validate_activity_values(db_, act, {"weight": "60"})


def test_unknown_key_raises(db):
    db_, act = db
    with pytest.raises(ValueError, match="Unknown"):
        activities.validate_activity_values(db_, act, {"reps": "8", "bogus": "1"})


def test_wrong_type_raises(db):
    db_, act = db
    with pytest.raises(ValueError, match="number|integer"):
        activities.validate_activity_values(db_, act, {"reps": "eight"})


def test_optional_blank_is_omitted(db):
    db_, act = db
    cleaned = activities.validate_activity_values(db_, act, {"reps": "8", "weight": ""})
    assert cleaned == {"reps": 8}


def test_create_activity_dedup_case_insensitive(db):
    db_, act = db
    a1 = activities.create_activity(db_, schemas.ActivityCreate(category_id=act.category_id, name="Squat"))
    a2 = activities.create_activity(db_, schemas.ActivityCreate(category_id=act.category_id, name="squat"))
    assert a1.id == a2.id  # dedup returns existing


def test_create_activity_bad_category_raises(db):
    db_, act = db
    with pytest.raises(ValueError, match="category"):
        activities.create_activity(db_, schemas.ActivityCreate(category_id=999, name="Nope"))


def test_list_active_activities_in_category(db):
    db_, act = db
    activities.create_activity(db_, schemas.ActivityCreate(category_id=act.category_id, name="Deadlift"))
    rows = activities.list_activities(db_, category_id=act.category_id)
    names = {r.name for r in rows}
    assert "Bench Press" in names and "Deadlift" in names


def _make_session(db_):
    # Minimal purchase + session to attach activities to
    pur = models.Purchase(duration_minutes=30, total_sessions=10, sessions_remaining=10)
    db_.add(pur)
    db_.flush()
    sess = models.Session(purchase_id=pur.id, duration_minutes=30, trainer="X")
    db_.add(sess)
    db_.commit()
    db_.refresh(sess)
    return sess


def test_reconcile_inserts_rows(db):
    db_, act = db
    sess = _make_session(db_)
    items = [schemas.SessionActivityInput(activity_id=act.id, values={"reps": "8", "weight": "60"})]
    activities.reconcile_session_activities(db_, sess, items)
    db_.commit()
    db_.refresh(sess)
    assert len(sess.activities) == 1
    assert sess.activities[0].values == {"reps": 8, "weight": 60.0}


def test_reconcile_updates_and_deletes(db):
    db_, act = db
    sess = _make_session(db_)
    activities.reconcile_session_activities(
        db_, sess, [schemas.SessionActivityInput(activity_id=act.id, values={"reps": "5"})]
    )
    db_.commit(); db_.refresh(sess)
    existing_id = sess.activities[0].id

    # Update the existing row, no new rows -> nothing else
    activities.reconcile_session_activities(
        db_, sess, [schemas.SessionActivityInput(id=existing_id, activity_id=act.id, values={"reps": "10"})]
    )
    db_.commit(); db_.refresh(sess)
    assert len(sess.activities) == 1
    assert sess.activities[0].values == {"reps": 10}

    # Empty list -> row removed
    activities.reconcile_session_activities(db_, sess, [])
    db_.commit(); db_.refresh(sess)
    assert len(sess.activities) == 0


def test_reconcile_foreign_row_id_rejected(db):
    db_, act = db
    sess = _make_session(db_)
    with pytest.raises(ValueError, match="not on this session"):
        activities.reconcile_session_activities(
            db_, sess, [schemas.SessionActivityInput(id=999, activity_id=act.id, values={"reps": "8"})]
        )


def test_reconcile_inactive_activity_rejected(db):
    db_, act = db
    sess = _make_session(db_)
    act.is_active = False
    db_.commit()
    with pytest.raises(ValueError, match="not available|inactive"):
        activities.reconcile_session_activities(
            db_, sess, [schemas.SessionActivityInput(activity_id=act.id, values={"reps": "8"})]
        )
