from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from gym_tracker import models, schemas


def _user_purchase_filter(user_id: int):
    """Filter purchases where user is owner OR partner."""
    return or_(
        models.Purchase.logged_by_user_id == user_id,
        models.Purchase.partner_user_id == user_id,
    )


def _user_session_ids(db: Session, user_id: int, start=None, end=None):
    """Subquery returning distinct session IDs visible to a user."""
    q = (
        db.query(models.Session.id)
        .outerjoin(models.Purchase, models.Session.purchase_id == models.Purchase.id)
        .filter(or_(
            models.Session.created_by_user_id == user_id,
            models.Session.partner_user_id == user_id,
            models.Purchase.partner_user_id == user_id,
            models.Purchase.logged_by_user_id == user_id,
        ))
    )
    if start:
        q = q.filter(models.Session.session_date >= start)
    if end:
        q = q.filter(models.Session.session_date <= end)
    return q.distinct().subquery()


def _resolve_partner(db: Session, partner_email: Optional[str]) -> Optional[int]:
    """Look up a user by email. Returns user ID or None."""
    if not partner_email:
        return None
    user = db.query(models.User).filter(
        models.User.email == partner_email.lower().strip()
    ).first()
    return user.id if user else None


def _annotate_purchases(db, purchases, user_id: int):
    """Add is_owner, partner_name, and adjust cost for partner views.
    partner_name always shows the OTHER person, not yourself."""
    for p in purchases:
        is_owner = (p.logged_by_user_id == user_id)
        p.is_owner = is_owner
        # Show the other person's name, not your own
        if is_owner:
            if p.partner_user_id and hasattr(p, 'partner_user') and p.partner_user:
                p.partner_name = p.partner_user.full_name or p.partner_user.email
            elif p.partner_email:
                p.partner_name = p.partner_email
        else:
            if p.logged_by_user_id and hasattr(p, 'logged_by_user') and p.logged_by_user:
                p.partner_name = p.logged_by_user.full_name or p.logged_by_user.email
            # Expunge from session before mutating cost to prevent DB flush
            db.expunge(p)
            p.cost = 0.0


def _annotate_session(sess, purchase, user_id: int):
    """Add partner_email, partner_name, num_people, is_owner to a session.
    partner_name always shows the OTHER person, not yourself."""
    sess.is_owner = (sess.created_by_user_id == user_id)
    sess.num_people = purchase.num_people if purchase else 1

    if not purchase or purchase.num_people <= 1:
        return

    # Per-session partner override: show that person (unless it's you)
    if sess.partner_user_id and hasattr(sess, 'partner_user') and sess.partner_user:
        if sess.partner_user_id != user_id:
            sess.partner_name = sess.partner_user.full_name or sess.partner_user.email
            sess.partner_email = sess.partner_user.email
            return

    # Fall back to purchase-level partner/owner — show the OTHER person
    # If I'm the purchaser, show the partner. If I'm the partner, show the purchaser.
    is_purchaser = (purchase.logged_by_user_id == user_id)
    if is_purchaser:
        if purchase.partner_user_id and hasattr(purchase, 'partner_user') and purchase.partner_user:
            sess.partner_name = purchase.partner_user.full_name or purchase.partner_user.email
            sess.partner_email = purchase.partner_user.email
        elif purchase.partner_email:
            sess.partner_name = purchase.partner_email
            sess.partner_email = purchase.partner_email
    else:
        if purchase.logged_by_user_id and hasattr(purchase, 'logged_by_user') and purchase.logged_by_user:
            sess.partner_name = purchase.logged_by_user.full_name or purchase.logged_by_user.email
            sess.partner_email = purchase.logged_by_user.email

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
      - partner_email / num_people for 2-person packages
    """
    partner_user_id = _resolve_partner(db, purchase_in.partner_email)

    db_purchase = models.Purchase(
        duration_minutes=purchase_in.duration_minutes,
        total_sessions=10,
        sessions_remaining=10,
        cost=purchase_in.cost,
        purchase_date=datetime.now(timezone.utc),
        logged_by_user_id=logged_by_user_id,
        num_people=purchase_in.num_people,
        partner_email=purchase_in.partner_email,
        partner_user_id=partner_user_id,
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
        q = q.filter(_user_purchase_filter(user_id))
    purchases = (
        q.order_by(models.Purchase.purchase_date.desc())
         .offset(skip)
         .limit(limit)
         .all()
    )
    if user_id is not None:
        _annotate_purchases(db, purchases, user_id)
    return purchases


def get_purchases_history(
    db: Session,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    *,
    user_id: Optional[int] = None,
):
    q = db.query(models.Purchase)
    if user_id is not None:
        q = q.filter(_user_purchase_filter(user_id))
    if start:
        q = q.filter(models.Purchase.purchase_date >= start)
    if end:
        q = q.filter(models.Purchase.purchase_date <= end)
    purchases = q.order_by(models.Purchase.purchase_date.desc()).all()
    if user_id is not None:
        _annotate_purchases(db, purchases, user_id)
    return purchases


def get_summary(db: Session, *, user_id: Optional[int] = None):
    """
    Returns a list of dicts with duration, num_people, remaining
    scoped to the given user (if provided). Includes partner purchases.
    Groups by (duration_minutes, num_people) to distinguish package types.
    """
    q = db.query(
        models.Purchase.duration_minutes,
        models.Purchase.num_people,
        func.sum(models.Purchase.sessions_remaining),
    )
    if user_id is not None:
        q = q.filter(_user_purchase_filter(user_id))
    results = q.group_by(
        models.Purchase.duration_minutes,
        models.Purchase.num_people,
    ).all()
    return [
        {"duration": duration, "num_people": num_people, "remaining": int(remaining)}
        for duration, num_people, remaining in results
    ]


# --------------------
# Session CRUD
# --------------------

def create_session(
    db: Session,
    duration_minutes: int,
    trainer: str,
    *,
    created_by_user_id: Optional[int] = None,
    partner_email: Optional[str] = None,
    num_people: int = 1,
):
    """
    Creates a session by consuming one matching purchase (oldest first),
    and records who created it (created_by_user_id).
    Consumes from user's own packs or packs where they are partner.
    """
    # Validate trainer is not empty
    if not trainer or not trainer.strip():
        raise ValueError("Trainer name is required and cannot be empty")
    pack_q = (
        db.query(models.Purchase)
        .filter(
            models.Purchase.duration_minutes == duration_minutes,
            models.Purchase.num_people == num_people,
            models.Purchase.sessions_remaining > 0,
        )
        .order_by(models.Purchase.purchase_date)
    )
    if created_by_user_id is not None:
        pack_q = pack_q.filter(_user_purchase_filter(created_by_user_id))

    purchase = pack_q.first()
    if not purchase:
        raise ValueError("No available purchase with remaining sessions for this duration")

    # Resolve per-session partner override
    session_partner_id = None
    if partner_email:
        session_partner_id = _resolve_partner(db, partner_email)

    purchase.sessions_remaining -= 1
    db_session = models.Session(
        purchase_id=purchase.id,
        duration_minutes=duration_minutes,
        trainer=trainer,
        session_date=datetime.now(timezone.utc),
        created_by_user_id=created_by_user_id,
        partner_user_id=session_partner_id,
    )
    db.add(db_session)
    db.commit()
    db.refresh(db_session)

    # Expose whether that purchase is now exhausted
    db_session.purchase_exhausted = (purchase.sessions_remaining == 0)
    # Annotate partner info for response
    _annotate_session(db_session, purchase, created_by_user_id)
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
        visible_ids = _user_session_ids(db, user_id, start, end)
        q = q.filter(models.Session.id.in_(select(visible_ids.c.id)))
    if start:
        q = q.filter(models.Session.session_date >= start)
    if end:
        q = q.filter(models.Session.session_date <= end)
    sessions = q.order_by(models.Session.session_date.desc()).all()
    for sess in sessions:
        purchase = db.get(models.Purchase, sess.purchase_id)
        sess.purchase_exhausted = (purchase.sessions_remaining == 0)
        if user_id is not None:
            _annotate_session(sess, purchase, user_id)
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
    Includes sessions where user is creator or partner.
    """
    q = db.query(
        models.Session.trainer,
        func.sum(models.Session.duration_minutes),
    ).filter(
        models.Session.session_date >= start,
        models.Session.session_date <= end,
    )
    if user_id is not None:
        visible_ids = _user_session_ids(db, user_id, start, end)
        q = q.filter(models.Session.id.in_(select(visible_ids.c.id)))
    return q.group_by(models.Session.trainer).all()


def get_total_minutes_by_duration(
    db: Session,
    start: datetime,
    end: datetime,
    *,
    user_id: Optional[int] = None,
):
    """
    Returns list of tuples: (duration_minutes, total_minutes)
    for sessions between start and end, scoped by user.
    """
    q = db.query(
        models.Session.duration_minutes,
        func.sum(models.Session.duration_minutes),
    ).filter(
        models.Session.session_date >= start,
        models.Session.session_date <= end,
    )
    if user_id is not None:
        visible_ids = _user_session_ids(db, user_id, start, end)
        q = q.filter(models.Session.id.in_(select(visible_ids.c.id)))
    return q.group_by(models.Session.duration_minutes).all()


def get_minutes_by_partner(
    db: Session,
    start: datetime,
    end: datetime,
    *,
    user_id: int,
):
    """
    Returns list of dicts: {partner: name_or_Solo, minutes: int}
    breaking down training minutes by who the user trained with.
    """
    visible_ids = _user_session_ids(db, user_id, start, end)
    sessions = (
        db.query(models.Session)
        .filter(models.Session.id.in_(select(visible_ids.c.id)))
        .all()
    )

    by_partner = {}
    for sess in sessions:
        purchase = db.get(models.Purchase, sess.purchase_id)
        num_people = purchase.num_people if purchase else 1

        if num_people <= 1:
            partner_label = "Solo"
        else:
            # Determine who the other person is
            partner_label = None
            # Per-session partner override
            if sess.partner_user_id and sess.partner_user_id != user_id:
                u = sess.partner_user
                if u:
                    partner_label = u.full_name or u.email
            # Fall back to purchase-level
            if not partner_label and purchase:
                is_purchaser = (purchase.logged_by_user_id == user_id)
                if is_purchaser:
                    if purchase.partner_user_id and purchase.partner_user:
                        partner_label = purchase.partner_user.full_name or purchase.partner_user.email
                    elif purchase.partner_email:
                        partner_label = purchase.partner_email
                else:
                    if purchase.logged_by_user_id and purchase.logged_by_user:
                        partner_label = purchase.logged_by_user.full_name or purchase.logged_by_user.email
            if not partner_label:
                partner_label = "Partner (unknown)"

        by_partner[partner_label] = by_partner.get(partner_label, 0) + sess.duration_minutes

    return [{"partner": k, "minutes": v} for k, v in by_partner.items()]


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
