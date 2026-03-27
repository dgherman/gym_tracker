from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import Session

from gym_tracker import models

# Horizon: generate sessions 3 months ahead
HORIZON_MONTHS = 3

STATUS_COLORS = {
    "scheduled": "#0d6efd",   # blue
    "completed": "#198754",   # green
    "cancelled": "#6c757d",   # grey
}
NO_PACKAGE_COLOR = "#fd7e14"  # orange


def schedule_session(
    db: Session,
    *,
    trainer_id: int,
    client_user_id: int,
    session_date: datetime,
    duration_minutes: int,
    purchase_id: Optional[int],
    scheduled_by_user_id: int,
    notes: Optional[str] = None,
    recurrence_group_id: Optional[int] = None,
) -> models.Session:
    """
    Create one scheduled session. Decrements sessions_remaining on the purchase
    if purchase_id is set. Raises ValueError if purchase has no remaining sessions.
    """
    if purchase_id is not None:
        purchase = db.get(models.Purchase, purchase_id)
        if not purchase:
            raise ValueError("Purchase not found")
        if purchase.sessions_remaining <= 0:
            raise ValueError("No sessions remaining in this package")
        purchase.sessions_remaining -= 1

    sess = models.Session(
        trainer_id=trainer_id,
        client_user_id=client_user_id,
        session_date=session_date,
        duration_minutes=duration_minutes,
        purchase_id=purchase_id,
        scheduled_by_user_id=scheduled_by_user_id,
        notes=notes,
        status="scheduled",
        recurrence_group_id=recurrence_group_id,
        trainer="",     # legacy NOT NULL field; unused for calendar-scheduled sessions
    )
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return sess


def _generate_dates(
    start_date: date,
    day_of_week: int,
    frequency: str,
    through: date,
) -> list[date]:
    """
    Return all dates from start_date through 'through' that fall on day_of_week
    with the given frequency (weekly/biweekly/monthly).
    """
    days_ahead = (day_of_week - start_date.weekday()) % 7
    current = start_date + timedelta(days=days_ahead)

    result = []
    while current <= through:
        result.append(current)
        if frequency == "weekly":
            current += timedelta(weeks=1)
        elif frequency == "biweekly":
            current += timedelta(weeks=2)
        elif frequency == "monthly":
            current += relativedelta(months=1)
        else:
            raise ValueError(f"Unknown frequency: {frequency}")
    return result


def schedule_recurring(
    db: Session,
    *,
    trainer_id: int,
    client_user_id: int,
    start_date: date,
    session_time: time,
    duration_minutes: int,
    frequency: str,
    purchase_id: Optional[int],
    scheduled_by_user_id: int,
    notes: Optional[str] = None,
) -> tuple[list[models.Session], models.RecurrenceGroup]:
    """
    Create a RecurrenceGroup and generate all sessions through now+3 months.
    Credits are reserved immediately for each generated session.
    Returns (sessions_list, group).
    """
    now = datetime.now(timezone.utc)
    horizon_date = (now + relativedelta(months=HORIZON_MONTHS)).date()

    group = models.RecurrenceGroup(
        frequency=frequency,
        day_of_week=start_date.weekday(),
        time_of_day=session_time,
        duration_minutes=duration_minutes,
        trainer_id=trainer_id,
        client_user_id=client_user_id,
        purchase_id=purchase_id,
        created_at=now,
        horizon_through=datetime.combine(horizon_date, session_time),
    )
    db.add(group)
    db.flush()   # get group.id before creating sessions

    dates = _generate_dates(start_date, start_date.weekday(), frequency, horizon_date)
    sessions = []
    for d in dates:
        sess = schedule_session(
            db,
            trainer_id=trainer_id,
            client_user_id=client_user_id,
            session_date=datetime.combine(d, session_time),
            duration_minutes=duration_minutes,
            purchase_id=purchase_id,
            scheduled_by_user_id=scheduled_by_user_id,
            notes=notes,
            recurrence_group_id=group.id,
        )
        sessions.append(sess)

    db.commit()
    db.refresh(group)
    return sessions, group


def extend_horizon(db: Session, group: models.RecurrenceGroup) -> list[models.Session]:
    """
    Extend a recurrence group's sessions through now+3 months if needed.
    Returns newly created sessions (empty list if nothing to do).
    """
    now = datetime.now(timezone.utc)
    target_horizon = (now + relativedelta(months=HORIZON_MONTHS)).date()
    current_horizon = group.horizon_through.date()

    if current_horizon >= target_horizon:
        return []

    new_start = current_horizon + timedelta(days=1)
    new_dates = _generate_dates(
        new_start,
        group.day_of_week,
        group.frequency,
        target_horizon,
    )

    new_sessions = []
    for d in new_dates:
        sess = schedule_session(
            db,
            trainer_id=group.trainer_id,
            client_user_id=group.client_user_id,
            session_date=datetime.combine(d, group.time_of_day),
            duration_minutes=group.duration_minutes,
            purchase_id=group.purchase_id,
            scheduled_by_user_id=group.trainer.user_id if group.trainer and group.trainer.user_id else None,
            recurrence_group_id=group.id,
        )
        new_sessions.append(sess)

    group.horizon_through = datetime.combine(target_horizon, group.time_of_day)
    db.commit()
    db.refresh(group)
    return new_sessions


def complete_session(db: Session, sess: models.Session) -> models.Session:
    """Mark a session as completed. No credit change (already reserved on schedule)."""
    if sess.status != "scheduled":
        raise ValueError(f"Cannot complete session with status '{sess.status}'")
    sess.status = "completed"
    db.commit()
    return sess


def cancel_session(
    db: Session,
    sess: models.Session,
    scope: str,  # "this" or "future"
) -> list[models.Session]:
    """
    Cancel a session. scope='this' cancels only this session.
    scope='future' cancels this and all future sessions in the same recurrence group.
    Credits are refunded for each cancelled session that has a purchase_id.
    Returns list of cancelled sessions.
    """
    if scope not in ("this", "future"):
        raise ValueError("scope must be 'this' or 'future'")

    to_cancel = [sess]

    if scope == "future" and sess.recurrence_group_id:
        group_id = sess.recurrence_group_id
        pivot_date = sess.session_date
        future_siblings = (
            db.query(models.Session)
            .filter(
                models.Session.recurrence_group_id == group_id,
                models.Session.session_date >= pivot_date,
                models.Session.status == "scheduled",
                models.Session.id != sess.id,
            )
            .all()
        )
        to_cancel.extend(future_siblings)

        # Stop horizon extension beyond pivot
        group = db.get(models.RecurrenceGroup, group_id)
        if group and group.horizon_through > pivot_date:
            group.horizon_through = pivot_date

    cancelled = []
    for s in to_cancel:
        if s.status == "scheduled":
            s.status = "cancelled"
            if s.purchase_id:
                purchase = db.get(models.Purchase, s.purchase_id)
                if purchase:
                    purchase.sessions_remaining += 1
            cancelled.append(s)

    db.commit()
    return cancelled


def reschedule_session(
    db: Session,
    sess: models.Session,
    new_date: datetime,
    scope: str,  # "this" or "future"
) -> list[models.Session]:
    """
    Reschedule a session to new_date.
    scope='this': update only this session; detach from recurrence group.
    scope='future': apply the same delta to this and all future sessions in the group.
    Returns list of rescheduled sessions.
    """
    if scope not in ("this", "future"):
        raise ValueError("scope must be 'this' or 'future'")

    if scope == "this":
        sess.session_date = new_date
        sess.recurrence_group_id = None   # detach from group
        db.commit()
        return [sess]

    # scope == "future"
    original_date = sess.session_date          # save before modifying
    delta = new_date - original_date
    rescheduled = [sess]
    sess.session_date = new_date

    if sess.recurrence_group_id:
        group_id = sess.recurrence_group_id
        pivot_date = original_date             # clearly the original date
        future_siblings = (
            db.query(models.Session)
            .filter(
                models.Session.recurrence_group_id == group_id,
                models.Session.session_date >= pivot_date,
                models.Session.status == "scheduled",
                models.Session.id != sess.id,
            )
            .all()
        )
        for s in future_siblings:
            s.session_date = s.session_date + delta
            rescheduled.append(s)

        # Shift horizon too
        group = db.get(models.RecurrenceGroup, group_id)
        if group:
            group.horizon_through = group.horizon_through + delta

    db.commit()
    return rescheduled


def _session_color(sess: models.Session) -> str:
    if sess.status == "scheduled" and sess.purchase_id is None:
        return NO_PACKAGE_COLOR
    return STATUS_COLORS.get(sess.status, "#0d6efd")


def _session_to_event(sess: models.Session, viewer_role: str) -> dict:
    trainer_name = (
        sess.trainer_rel.name if sess.trainer_rel else (sess.trainer or "Unknown")
    )
    client_name = (
        (sess.client_user.full_name or sess.client_user.email)
        if sess.client_user
        else "Unknown"
    )

    if viewer_role in ("trainer", "admin"):
        title = f"Session #{sess.id} \u00b7 {client_name}"
    else:
        title = f"Session #{sess.id} \u00b7 {trainer_name}"

    return {
        "id": str(sess.id),
        "title": title,
        "start": sess.session_date.isoformat(),
        "end": (sess.session_date + timedelta(minutes=sess.duration_minutes)).isoformat(),
        "color": _session_color(sess),
        "extendedProps": {
            "status": sess.status,
            "purchase_id": sess.purchase_id,
            "recurrence_group_id": sess.recurrence_group_id,
            "notes": sess.notes,
            "client_name": client_name,
            "trainer_name": trainer_name,
            "client_user_id": sess.client_user_id,
        },
    }


def get_calendar_events(
    db: Session,
    *,
    start: datetime,
    end: datetime,
    viewer_role: str,
    viewer_user_id: int,
) -> list[dict]:
    """
    Return FullCalendar-compatible event dicts for the given date range.
    Trainers/admins see all sessions. Clients see only their own.
    Also triggers horizon extension for any recurrence groups needing it.
    """
    # Extend horizons lazily
    groups_needing_extension = (
        db.query(models.RecurrenceGroup)
        .filter(
            models.RecurrenceGroup.horizon_through
            < datetime.now(timezone.utc) + relativedelta(months=HORIZON_MONTHS)
        )
        .all()
    )
    for group in groups_needing_extension:
        extend_horizon(db, group)

    q = db.query(models.Session).filter(
        models.Session.session_date >= start,
        models.Session.session_date <= end,
    )
    if viewer_role not in ("trainer", "admin"):
        q = q.filter(models.Session.client_user_id == viewer_user_id)

    sessions = q.order_by(models.Session.session_date).all()
    return [_session_to_event(s, viewer_role) for s in sessions]
