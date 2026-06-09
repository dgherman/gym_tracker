from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from gym_tracker import models, schemas
from gym_tracker import activities as activities_mod


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


def session_participant_ids(session, purchase) -> set:
    """User ids allowed to edit/delete a session: creator, purchase owner,
    and the session/purchase partner. partner_email-only partners have no
    account and cannot log in, so they need no entry."""
    ids = {
        getattr(session, "created_by_user_id", None),
        getattr(session, "partner_user_id", None),
        getattr(purchase, "logged_by_user_id", None) if purchase else None,
        getattr(purchase, "partner_user_id", None) if purchase else None,
    }
    ids.discard(None)
    return ids


def user_can_edit_session(session, purchase, user_id) -> bool:
    return user_id in session_participant_ids(session, purchase)


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
    """Add partner_email, partner_name, num_people, is_owner, can_edit to a session.
    partner_name always shows the OTHER person, not yourself.
    is_owner is creator-only (used for Shared badge and cost display).
    can_edit is true for any participant (creator, purchase owner, or either partner)."""
    sess.is_owner = (sess.created_by_user_id == user_id)
    sess.can_edit = user_id in session_participant_ids(sess, purchase)
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

def _person_name_for_slot(db, purchase, sess, slot):
    """Absolute person label for an activity row's slot.
    1=owner, 2=partner, None=shared. Returns a display string.

    Partner-name priority: session.partner_user_id -> purchase.partner_user_id
    -> purchase.partner_email. Mirrors _resolve_person_slot in activities.py; update both
    together when the priority chain changes."""
    if slot is None:
        return "Both / Shared"
    if slot == 1:
        owner = purchase.logged_by_user if purchase else None
        if owner:
            return owner.full_name or owner.email
        return "Person A"
    # slot == 2 (partner): session override -> purchase partner user -> partner_email
    if sess.partner_user_id and getattr(sess, "partner_user", None):
        return sess.partner_user.full_name or sess.partner_user.email
    if purchase and purchase.partner_user_id and getattr(purchase, "partner_user", None):
        return purchase.partner_user.full_name or purchase.partner_user.email
    if purchase and purchase.partner_email:
        return purchase.partner_email
    return "Person B"


def _annotate_session_activities(db, sess):
    """Attach activity_name, category_id, category_name, person_slot and
    person_name onto each SessionActivity for SessionActivityRead."""
    purchase = db.get(models.Purchase, sess.purchase_id)
    for sa in sess.activities:
        activity = sa.activity or db.get(models.Activity, sa.activity_id)
        sa.activity_name = activity.name if activity else "(unknown)"
        category = db.get(models.ActivityCategory, activity.category_id) if activity else None
        sa.category_id = category.id if category else 0
        sa.category_name = category.name if category else "(unknown)"
        sa.person_name = _person_name_for_slot(db, purchase, sess, sa.person_slot)


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
    activities: Optional[list] = None,
):
    """
    Creates a session by consuming one matching purchase (oldest first),
    records who created it, and optionally attaches activities.
    The purchase decrement + session + activities are committed atomically:
    a bad activity raises ValueError before commit and nothing is persisted.
    """
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
    db.flush()  # assign db_session.id without committing

    if activities:
        # Raises ValueError on bad data -> caller/endpoint maps to 400; no commit happened
        activities_mod.reconcile_session_activities(
            db, db_session, activities, created_by_user_id=created_by_user_id
        )

    db.commit()
    db.refresh(db_session)

    db_session.purchase_exhausted = (purchase.sessions_remaining == 0)
    _annotate_session(db_session, purchase, created_by_user_id)
    _annotate_session_activities(db, db_session)
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
        _annotate_session_activities(db, sess)
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


def _user_slot_in_session(sess, purchase, user_id):
    """Which person_slot belongs to user_id in this session: 1 if they are the
    purchase owner, 2 if they are the partner, else None."""
    if purchase and purchase.logged_by_user_id == user_id:
        return 1
    partner_id = (sess.partner_user_id
                  or (purchase.partner_user_id if purchase else None))
    if partner_id == user_id:
        return 2
    return None


def user_activity_rows(db: Session, *, user_id: int, start, end):
    """Activity rows attributable to user_id within [start, end].
    Solo sessions (num_people<=1): all rows (null slot). Couples: rows whose
    person_slot equals the user's slot. Couples null rows are excluded.
    Returns dicts consumed by gym_tracker.progress.summarize."""
    visible = _user_session_ids(db, user_id, start, end)
    sessions = (
        db.query(models.Session)
        .filter(models.Session.id.in_(select(visible.c.id)))
        .all()
    )
    rows = []
    cat_cache = {}
    for sess in sessions:
        purchase = db.get(models.Purchase, sess.purchase_id)
        num_people = purchase.num_people if purchase else 1
        my_slot = _user_slot_in_session(sess, purchase, user_id)
        for sa in sess.activities:
            if num_people <= 1:
                pass  # solo: include all
            elif sa.person_slot == my_slot and my_slot is not None:
                pass  # couples: my tagged rows
            else:
                continue
            # sa.activity lazy-loads via the relationship; None = soft-deleted/orphaned
            activity = sa.activity
            if not activity:
                continue
            cid = activity.category_id
            if cid not in cat_cache:
                cat_cache[cid] = db.get(models.ActivityCategory, cid)
            category = cat_cache[cid]
            rows.append({
                "session_date": sess.session_date,
                "activity_id": activity.id,
                "activity_name": activity.name,
                "category_id": category.id if category else 0,
                "category_slug": category.slug if category else "",
                "category_name": category.name if category else "(unknown)",
                "values": sa.values or {},
            })
    return rows


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
