from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone
from gym_tracker import models, schemas


def create_purchase(db: Session, purchase: schemas.PurchaseCreate):
    db_purchase = models.Purchase(
        duration_minutes=purchase.duration_minutes,
        total_sessions=10,
        sessions_remaining=10
    )
    db.add(db_purchase)
    db.commit()
    db.refresh(db_purchase)
    if db_purchase.purchase_date.tzinfo is None:
        db_purchase.purchase_date = db_purchase.purchase_date.replace(tzinfo=timezone.utc)
    return db_purchase


def get_purchases(db: Session, skip: int = 0, limit: int = 100):
    purchases = (
        db.query(models.Purchase)
        .order_by(models.Purchase.purchase_date.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    for p in purchases:
        if p.purchase_date.tzinfo is None:
            p.purchase_date = p.purchase_date.replace(tzinfo=timezone.utc)
    return purchases


def get_summary(db: Session):
    results = (
        db.query(
            models.Purchase.duration_minutes,
            func.sum(models.Purchase.sessions_remaining)
        )
        .group_by(models.Purchase.duration_minutes)
        .all()
    )
    return {duration: int(remaining) for duration, remaining in results}


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
        purchase_id=purchase.id,
        duration_minutes=duration_minutes,
        trainer=trainer
    )
    db.add(db_session)
    db.commit()
    db.refresh(db_session)
    db_session.purchase_exhausted = (purchase.sessions_remaining == 0)
    if db_session.session_date.tzinfo is None:
        db_session.session_date = db_session.session_date.replace(tzinfo=timezone.utc)
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
        if sess.session_date.tzinfo is None:
            sess.session_date = sess.session_date.replace(tzinfo=timezone.utc)
    return sessions


def get_purchases_history(db: Session, start: datetime = None, end: datetime = None):
    query = db.query(models.Purchase)
    if start:
        query = query.filter(models.Purchase.purchase_date >= start)
    if end:
        query = query.filter(models.Purchase.purchase_date <= end)
    purchases = query.order_by(models.Purchase.purchase_date.desc()).all()
    for p in purchases:
        if p.purchase_date.tzinfo is None:
            p.purchase_date = p.purchase_date.replace(tzinfo=timezone.utc)
    return purchases