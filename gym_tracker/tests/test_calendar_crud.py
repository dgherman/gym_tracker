import pytest
from datetime import datetime, timedelta, time
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from gym_tracker.database import Base
from gym_tracker import models, calendar_crud

test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="module")
def db():
    Base.metadata.create_all(bind=test_engine)
    session = TestSessionLocal()

    trainer_user = models.User(
        google_sub="sub-trainer", email="trainer@gym.com", email_verified=True,
        role="trainer", is_active=True, created_at=datetime.utcnow(), last_login_at=datetime.utcnow(),
    )
    client_user = models.User(
        google_sub="sub-client", email="client@gym.com", email_verified=True,
        role="client", is_active=True, created_at=datetime.utcnow(), last_login_at=datetime.utcnow(),
    )
    session.add_all([trainer_user, client_user])
    session.flush()

    trainer = models.Trainer(
        name="Coach Ana", is_active=True, created_at=datetime.utcnow(),
        email="trainer@gym.com", user_id=trainer_user.id,
    )
    session.add(trainer)
    session.flush()

    purchase = models.Purchase(
        duration_minutes=60, total_sessions=10, sessions_remaining=10,
        purchase_date=datetime.utcnow(), cost=500.0,
        logged_by_user_id=client_user.id, num_people=1,
    )
    session.add(purchase)
    session.commit()

    session._trainer_id = trainer.id
    session._trainer_user_id = trainer_user.id
    session._client_user_id = client_user.id
    session._purchase_id = purchase.id
    try:
        yield session
    finally:
        session.close()


def test_schedule_single_session_reserves_credit(db):
    before = db.get(models.Purchase, db._purchase_id).sessions_remaining
    future = datetime.utcnow() + timedelta(days=3)
    sess = calendar_crud.schedule_session(
        db,
        trainer_id=db._trainer_id,
        client_user_id=db._client_user_id,
        session_date=future,
        duration_minutes=60,
        purchase_id=db._purchase_id,
        scheduled_by_user_id=db._trainer_user_id,
        notes="First session",
    )
    db.refresh(db.get(models.Purchase, db._purchase_id))
    after = db.get(models.Purchase, db._purchase_id).sessions_remaining
    assert sess.status == "scheduled"
    assert sess.client_user_id == db._client_user_id
    assert after == before - 1


def test_schedule_session_without_package(db):
    future = datetime.utcnow() + timedelta(days=5)
    sess = calendar_crud.schedule_session(
        db,
        trainer_id=db._trainer_id,
        client_user_id=db._client_user_id,
        session_date=future,
        duration_minutes=60,
        purchase_id=None,
        scheduled_by_user_id=db._trainer_user_id,
    )
    assert sess.status == "scheduled"
    assert sess.purchase_id is None


def test_schedule_session_no_credit_deducted_for_no_package(db):
    before = db.get(models.Purchase, db._purchase_id).sessions_remaining
    future = datetime.utcnow() + timedelta(days=7)
    calendar_crud.schedule_session(
        db,
        trainer_id=db._trainer_id,
        client_user_id=db._client_user_id,
        session_date=future,
        duration_minutes=60,
        purchase_id=None,
        scheduled_by_user_id=db._trainer_user_id,
    )
    after = db.get(models.Purchase, db._purchase_id).sessions_remaining
    assert after == before  # no change


def test_schedule_recurring_generates_sessions_in_horizon(db):
    # day_of_week=0 (Monday), weekly, starting from next Monday
    today = datetime.utcnow().date()
    days_until_monday = (0 - today.weekday()) % 7 or 7
    start_date = today + timedelta(days=days_until_monday)

    sessions, group = calendar_crud.schedule_recurring(
        db,
        trainer_id=db._trainer_id,
        client_user_id=db._client_user_id,
        start_date=start_date,
        session_time=time(10, 0),
        duration_minutes=60,
        frequency="weekly",
        purchase_id=None,
        scheduled_by_user_id=db._trainer_user_id,
    )
    assert group.id is not None
    assert group.frequency == "weekly"
    assert len(sessions) >= 1
    # All sessions should be on Mondays
    for s in sessions:
        assert s.session_date.weekday() == 0


def test_extend_horizon_generates_new_sessions(db):
    group = db.query(models.RecurrenceGroup).first()
    original_count = len(group.sessions)
    # Force horizon back by 2 months so extension is triggered
    group.horizon_through = datetime.utcnow() - timedelta(days=30)
    db.commit()

    calendar_crud.extend_horizon(db, group)
    db.refresh(group)
    assert len(group.sessions) > original_count


def test_complete_session_no_credit_change(db):
    future = datetime.utcnow() + timedelta(days=10)
    sess = calendar_crud.schedule_session(
        db, trainer_id=db._trainer_id, client_user_id=db._client_user_id,
        session_date=future, duration_minutes=60,
        purchase_id=db._purchase_id, scheduled_by_user_id=db._trainer_user_id,
    )
    before = db.get(models.Purchase, db._purchase_id).sessions_remaining
    calendar_crud.complete_session(db, sess)
    after = db.get(models.Purchase, db._purchase_id).sessions_remaining
    assert sess.status == "completed"
    assert after == before  # no change on completion


def test_cancel_session_refunds_credit(db):
    future = datetime.utcnow() + timedelta(days=11)
    sess = calendar_crud.schedule_session(
        db, trainer_id=db._trainer_id, client_user_id=db._client_user_id,
        session_date=future, duration_minutes=60,
        purchase_id=db._purchase_id, scheduled_by_user_id=db._trainer_user_id,
    )
    before = db.get(models.Purchase, db._purchase_id).sessions_remaining
    calendar_crud.cancel_session(db, sess, scope="this")
    after = db.get(models.Purchase, db._purchase_id).sessions_remaining
    assert sess.status == "cancelled"
    assert after == before + 1


def test_reschedule_single_session(db):
    future = datetime.utcnow() + timedelta(days=12)
    sess = calendar_crud.schedule_session(
        db, trainer_id=db._trainer_id, client_user_id=db._client_user_id,
        session_date=future, duration_minutes=60,
        purchase_id=None, scheduled_by_user_id=db._trainer_user_id,
    )
    new_date = future + timedelta(days=1)
    calendar_crud.reschedule_session(db, sess, new_date=new_date, scope="this")
    assert sess.session_date == new_date
    assert sess.recurrence_group_id is None   # detached from group (was None anyway)


def test_cancel_future_sessions(db):
    """Cancel a recurring session with scope='future' cancels all future sessions in the group."""
    today = datetime.utcnow().date()
    days_until_wednesday = (2 - today.weekday()) % 7 or 7
    start = today + timedelta(days=days_until_wednesday)

    sessions, group = calendar_crud.schedule_recurring(
        db, trainer_id=db._trainer_id, client_user_id=db._client_user_id,
        start_date=start, session_time=time(9, 0), duration_minutes=60,
        frequency="weekly", purchase_id=None,
        scheduled_by_user_id=db._trainer_user_id,
    )
    # Cancel from second session onward
    assert len(sessions) >= 2
    pivot = sessions[1]
    calendar_crud.cancel_session(db, pivot, scope="future")

    for s in sessions[1:]:
        db.refresh(s)
        assert s.status == "cancelled"
    db.refresh(sessions[0])
    assert sessions[0].status == "scheduled"  # first session untouched


def test_get_calendar_events_trainer_sees_all(db):
    start = datetime.utcnow() - timedelta(days=1)
    end = datetime.utcnow() + timedelta(days=30)
    events = calendar_crud.get_calendar_events(
        db, start=start, end=end, viewer_role="trainer", viewer_user_id=db._trainer_user_id
    )
    assert len(events) >= 1
    # Trainer view titles: "client · trainer"
    for e in events:
        assert "\u00b7" in e["title"]


def test_get_calendar_events_client_sees_own_only(db):
    start = datetime.utcnow() - timedelta(days=1)
    end = datetime.utcnow() + timedelta(days=30)
    events = calendar_crud.get_calendar_events(
        db, start=start, end=end, viewer_role="client", viewer_user_id=db._client_user_id
    )
    for e in events:
        assert e["extendedProps"]["client_user_id"] == db._client_user_id


def test_cancel_completed_session_refunds_credit(db):
    """A completed session can be cancelled retroactively and its credit is refunded."""
    future = datetime.utcnow() + timedelta(days=20)
    sess = calendar_crud.schedule_session(
        db, trainer_id=db._trainer_id, client_user_id=db._client_user_id,
        session_date=future, duration_minutes=60,
        purchase_id=db._purchase_id, scheduled_by_user_id=db._trainer_user_id,
    )
    # Force status to completed directly (simulating auto-complete or manual complete)
    sess.status = "completed"
    db.commit()

    before = db.get(models.Purchase, db._purchase_id).sessions_remaining
    calendar_crud.cancel_session(db, sess, scope="this")
    after = db.get(models.Purchase, db._purchase_id).sessions_remaining

    assert sess.status == "cancelled"
    assert after == before + 1


def test_reopen_session_reverts_to_scheduled(db):
    """reopen_session changes status from completed back to scheduled."""
    future = datetime.utcnow() + timedelta(days=21)
    sess = calendar_crud.schedule_session(
        db, trainer_id=db._trainer_id, client_user_id=db._client_user_id,
        session_date=future, duration_minutes=60,
        purchase_id=None, scheduled_by_user_id=db._trainer_user_id,
    )
    sess.status = "completed"
    db.commit()

    calendar_crud.reopen_session(db, sess)
    assert sess.status == "scheduled"


def test_reopen_session_raises_if_not_completed(db):
    """reopen_session raises ValueError if session is not completed."""
    future = datetime.utcnow() + timedelta(days=22)
    sess = calendar_crud.schedule_session(
        db, trainer_id=db._trainer_id, client_user_id=db._client_user_id,
        session_date=future, duration_minutes=60,
        purchase_id=None, scheduled_by_user_id=db._trainer_user_id,
    )
    with pytest.raises(ValueError, match="not completed"):
        calendar_crud.reopen_session(db, sess)


def test_auto_complete_past_sessions(db):
    """auto_complete_past_sessions marks past scheduled sessions as completed."""
    past = datetime.utcnow() - timedelta(days=2)
    sess = calendar_crud.schedule_session(
        db, trainer_id=db._trainer_id, client_user_id=db._client_user_id,
        session_date=past, duration_minutes=60,
        purchase_id=None, scheduled_by_user_id=db._trainer_user_id,
    )
    assert sess.status == "scheduled"

    completed = calendar_crud.auto_complete_past_sessions(db)
    db.refresh(sess)

    assert sess.id in [s.id for s in completed]
    assert sess.status == "completed"


def test_auto_complete_does_not_touch_future_sessions(db):
    """auto_complete_past_sessions leaves future sessions untouched."""
    future = datetime.utcnow() + timedelta(days=5)
    sess = calendar_crud.schedule_session(
        db, trainer_id=db._trainer_id, client_user_id=db._client_user_id,
        session_date=future, duration_minutes=60,
        purchase_id=None, scheduled_by_user_id=db._trainer_user_id,
    )
    calendar_crud.auto_complete_past_sessions(db)
    db.refresh(sess)
    assert sess.status == "scheduled"
