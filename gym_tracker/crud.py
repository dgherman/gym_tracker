from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from gym_tracker import models, schemas

# --------------------
# Purchase CRUD
# --------------------

def create_purchase(
    db: Session,
    purchase_in: schemas.PurchaseCreate,
    *,
    logged_by_user_id: Optional[int] = None,
):
    """
    Create a new purchase package:
      - duration_minutes and cost come from the schema
      - total_sessions and sessions_remaining default to 10
      - logged_by_user_id (optional) links the row to the actor
    """
    db_purchase = models.Purchase(
        duration_minutes=purchase_in.duration_minutes,
        total_sessions=10,
        sessions_remaining=10,
        cost=purchase_in.cost,
        purchase_date=datetime.now(timezone.utc),
        logged_by_user_id=logged_by_user_id,  # NEW
    )
    db.add(db_purchase)
    db.commit()
    db.refresh(db_purchase)
    return db_purchase


def get_purchases(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    *,
    user_id: Optional[int] = None,
):
    q = db.query(models.Purchase)
    if user_id is not None:
        q = q.filter(models.Purchase.logged_by_user_id == user_id)
    return (
        q.order_by(models.Purchase.purchase_date.desc())
         .offset(skip)
         .limit(limit)
         .all()
    )


def get_purchases_history(
    db: Session,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    *,
    user_id: Optional[int] = None,
):
    q = db.query(models.Purchase)
    if user_id is not None:
        q = q.filter(models.Purchase.logged_by_user_id == user_id)
    if start:
        q = q.filter(models.Purchase.purchase_date >= start)
    if end:
        q = q.filter(models.Purchase.purchase_date <= end)
    return q.order_by(models.Purchase.purchase_date.desc()).all()


def get_summary(db: Session, *, user_id: Optional[int] = None):
    """
    Returns a dict mapping duration -> total remaining sessions
    scoped to the given user (if provided).
    """
    q = db.query(
        models.Purchase.duration_minutes,
        func.sum(models.Purchase.sessions_remaining),
    )
    if user_id is not None:
        q = q.filter(models.Purchase.logged_by_user_id == user_id)
    results = q.group_by(models.Purchase.duration_minutes).all()
    return {duration: int(remaining) for duration, remaining in results}


# --------------------
# Session CRUD
# --------------------

def create_session(
    db: Session,
    duration_minutes: int,
    trainer: str,
    *,
    created_by_user_id: Optional[int] = None,
):
    """
    Creates a session by consuming one matching purchase (oldest first),
    and records who created it (created_by_user_id).
    Only consumes from THIS user's packs when a user_id is provided.
    """
    # Validate trainer is not empty
    if not trainer or not trainer.strip():
        raise ValueError("Trainer name is required and cannot be empty")
    pack_q = (
        db.query(models.Purchase)
        .filter(
            models.Purchase.duration_minutes == duration_minutes,
            models.Purchase.sessions_remaining > 0,
        )
        .order_by(models.Purchase.purchase_date)
    )
    if created_by_user_id is not None:
        pack_q = pack_q.filter(models.Purchase.logged_by_user_id == created_by_user_id)

    purchase = pack_q.first()
    if not purchase:
        raise ValueError("No available purchase with remaining sessions for this duration")

    purchase.sessions_remaining -= 1
    db_session = models.Session(
        purchase_id=purchase.id,
        duration_minutes=duration_minutes,
        trainer=trainer,
        session_date=datetime.now(timezone.utc),
        created_by_user_id=created_by_user_id,  # NEW
    )
    db.add(db_session)
    db.commit()
    db.refresh(db_session)

    # Expose whether that purchase is now exhausted
    db_session.purchase_exhausted = (purchase.sessions_remaining == 0)
    return db_session


def get_sessions(
    db: Session,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    *,
    user_id: Optional[int] = None,
):
    q = db.query(models.Session)
    if user_id is not None:
        q = q.filter(models.Session.created_by_user_id == user_id)
    if start:
        q = q.filter(models.Session.session_date >= start)
    if end:
        q = q.filter(models.Session.session_date <= end)
    sessions = q.order_by(models.Session.session_date.desc()).all()
    for sess in sessions:
        purchase = db.get(models.Purchase, sess.purchase_id)
        sess.purchase_exhausted = (purchase.sessions_remaining == 0)
    return sessions


# --------------------
# Reports Helpers
# --------------------

def get_training_by_trainer(
    db: Session,
    start: datetime,
    end: datetime,
    *,
    user_id: Optional[int] = None,
):
    """
    Returns list of tuples: (trainer, total_minutes)
    for sessions between start and end, scoped by user if provided.
    """
    q = db.query(
        models.Session.trainer,
        func.sum(models.Session.duration_minutes),
    ).filter(
        models.Session.session_date >= start,
        models.Session.session_date <= end,
    )
    if user_id is not None:
        q = q.filter(models.Session.created_by_user_id == user_id)
    return q.group_by(models.Session.trainer).all()


def get_total_cost(
    db: Session,
    start: datetime,
    end: datetime,
    *,
    user_id: Optional[int] = None,
):
    """
    Returns the total cost (float) of purchases between start and end,
    scoped by user if provided.
    """
    q = db.query(func.sum(models.Purchase.cost)).filter(
        models.Purchase.purchase_date >= start,
        models.Purchase.purchase_date <= end,
    )
    if user_id is not None:
        q = q.filter(models.Purchase.logged_by_user_id == user_id)
    total = q.scalar()
    return total or 0.0


# --------------------
# Trainer CRUD
# --------------------

def create_trainer(db: Session, trainer_in: schemas.TrainerCreate):
    """Create a new trainer."""
    db_trainer = models.Trainer(
        name=trainer_in.name,
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    db.add(db_trainer)
    db.commit()
    db.refresh(db_trainer)
    return db_trainer


def get_trainers(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    active_only: bool = True,
):
    """Get list of trainers, optionally filtered by active status."""
    q = db.query(models.Trainer)
    if active_only:
        q = q.filter(models.Trainer.is_active == True)
    return q.order_by(models.Trainer.name).offset(skip).limit(limit).all()


def get_trainer(db: Session, trainer_id: int):
    """Get a specific trainer by ID."""
    return db.query(models.Trainer).filter(models.Trainer.id == trainer_id).first()


def update_trainer(
    db: Session,
    trainer_id: int,
    trainer_update: schemas.TrainerUpdate,
):
    """Update a trainer."""
    trainer = get_trainer(db, trainer_id)
    if not trainer:
        return None

    update_data = trainer_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(trainer, field, value)

    db.commit()
    db.refresh(trainer)
    return trainer


def delete_trainer(db: Session, trainer_id: int):
    """Soft delete a trainer by setting is_active to False."""
    trainer = get_trainer(db, trainer_id)
    if not trainer:
        return None

    trainer.is_active = False
    db.commit()
    return trainer


# --------------------
# Package CRUD
# --------------------

def create_package(db: Session, package_in: schemas.PackageCreate):
    """Create a new package."""
    db_package = models.Package(
        name=package_in.name,
        duration_minutes=package_in.duration_minutes,
        num_people=package_in.num_people,
        total_sessions=package_in.total_sessions,
        price_per_session=package_in.price_per_session,
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    db.add(db_package)
    db.commit()
    db.refresh(db_package)
    return db_package


def get_packages(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    active_only: bool = True,
):
    """Get list of packages, optionally filtered by active status."""
    q = db.query(models.Package)
    if active_only:
        q = q.filter(models.Package.is_active == True)
    return q.order_by(models.Package.duration_minutes, models.Package.num_people).offset(skip).limit(limit).all()


def get_package(db: Session, package_id: int):
    """Get a specific package by ID."""
    return db.query(models.Package).filter(models.Package.id == package_id).first()


def update_package(
    db: Session,
    package_id: int,
    package_update: schemas.PackageUpdate,
):
    """Update a package."""
    package = get_package(db, package_id)
    if not package:
        return None

    update_data = package_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(package, field, value)

    db.commit()
    db.refresh(package)
    return package


def delete_package(db: Session, package_id: int):
    """Soft delete a package by setting is_active to False."""
    package = get_package(db, package_id)
    if not package:
        return None

    package.is_active = False
    db.commit()
    return package
