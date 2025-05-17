from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import func

from gym_tracker import models, schemas

# --------------------
# Purchase CRUD
# --------------------

def create_purchase(db: Session, purchase_in: schemas.PurchaseCreate):
    """
    Create a new purchase package:
      - duration_minutes and cost come from the schema
      - total_sessions and sessions_remaining default to 10
    """
    db_purchase = models.Purchase(
        duration_minutes   = purchase_in.duration_minutes,
        total_sessions     = 10,
        sessions_remaining = 10,
        cost               = purchase_in.cost,                    # ← store cost
        purchase_date      = datetime.now(timezone.utc)
    )
    db.add(db_purchase)
    db.commit()
    db.refresh(db_purchase)
    return db_purchase

def get_purchases(db: Session, skip: int = 0, limit: int = 100):
    return (
        db.query(models.Purchase)
          .order_by(models.Purchase.purchase_date.desc())
          .offset(skip)
          .limit(limit)
          .all()
    )

def get_purchases_history(db: Session, start: datetime = None, end: datetime = None):
    query = db.query(models.Purchase)
    if start:
        query = query.filter(models.Purchase.purchase_date >= start)
    if end:
        query = query.filter(models.Purchase.purchase_date <= end)
    return query.order_by(models.Purchase.purchase_date.desc()).all()


def get_summary(db: Session):
    """
    Returns a dict mapping duration -> total remaining sessions.
    """
    results = (
        db.query(
            models.Purchase.duration_minutes,
            func.sum(models.Purchase.sessions_remaining)
        )
        .group_by(models.Purchase.duration_minutes)
        .all()
    )
    return {duration: int(remaining) for duration, remaining in results}


# --------------------
# Session CRUD
# --------------------

def create_session(db: Session, duration_minutes: int, trainer: str = "Rachel"):
    purchase = (
        db.query(models.Purchase)
          .filter(
              models.Purchase.duration_minutes == duration_minutes,
              models.Purchase.sessions_remaining > 0
          )
          .order_by(models.Purchase.purchase_date)
          .first()
    )
    if not purchase:
        raise ValueError("No available purchase with remaining sessions for this duration")

    purchase.sessions_remaining -= 1
    db_session = models.Session(
        purchase_id      = purchase.id,
        duration_minutes = duration_minutes,
        trainer          = trainer,
        session_date     = datetime.now(timezone.utc)
    )
    db.add(db_session)
    db.commit()
    db.refresh(db_session)

    # Expose whether that purchase is now exhausted
    db_session.purchase_exhausted = (purchase.sessions_remaining == 0)
    return db_session

def get_sessions(db: Session, start: datetime = None, end: datetime = None):
    query = db.query(models.Session)
    if start:
        query = query.filter(models.Session.session_date >= start)
    if end:
        query = query.filter(models.Session.session_date <= end)
    sessions = query.order_by(models.Session.session_date.desc()).all()
    for sess in sessions:
        purchase = db.get(models.Purchase, sess.purchase_id)
        sess.purchase_exhausted = (purchase.sessions_remaining == 0)
    return sessions

# --------------------
# Reports Helpers
# --------------------

def get_training_by_trainer(db: Session, start: datetime, end: datetime):
    """
    Returns list of tuples: (trainer, total_minutes) 
    for sessions between start and end.
    """
    results = (
        db.query(
            models.Session.trainer,
            func.sum(models.Session.duration_minutes)
        )
        .filter(models.Session.session_date >= start,
                models.Session.session_date <= end)
        .group_by(models.Session.trainer)
        .all()
    )
    return results

def get_total_cost(db: Session, start: datetime, end: datetime):
    """
    Returns the total cost (float) of purchases between start and end.
    """
    total = (
        db.query(func.sum(models.Purchase.cost))
          .filter(models.Purchase.purchase_date >= start,
                  models.Purchase.purchase_date <= end)
          .scalar()
    )
    return total or 0.0
