import re
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from gym_tracker import models, schemas


def _coerce(field_type: str, label: str, raw):
    if field_type in ("integer", "duration"):
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"Field '{label}' must be a whole number")
    if field_type == "decimal":
        try:
            return float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"Field '{label}' must be a number")
    # text
    return str(raw)


def validate_activity_values(db: Session, activity: models.Activity, values: dict) -> dict:
    """Validate a values dict against the activity's category fields.
    Active fields drive required-field checks; values for soft-deleted
    (inactive) fields are still accepted and preserved so historical logs
    remain editable. Unknown keys (no matching field at all) are rejected.
    Returns a cleaned dict of present, coerced values. Raises ValueError."""
    values = values or {}
    all_fields = (
        db.query(models.CategoryField)
        .filter(models.CategoryField.category_id == activity.category_id)
        .all()
    )
    field_by_key = {f.key: f for f in all_fields}

    for k in values:
        if k not in field_by_key:
            raise ValueError(f"Unknown field '{k}' for this activity")

    cleaned = {}
    for k, raw in values.items():
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            continue
        f = field_by_key[k]
        cleaned[k] = _coerce(f.field_type, f.label, raw)

    for f in all_fields:
        if f.is_active and f.is_required and f.key not in cleaned:
            raise ValueError(f"Field '{f.label}' is required")
    return cleaned


# --------------------
# Category reads
# --------------------

def list_categories(db: Session):
    return (
        db.query(models.ActivityCategory)
        .filter(models.ActivityCategory.is_active == True)  # noqa: E712
        .order_by(models.ActivityCategory.sort_order, models.ActivityCategory.name)
        .all()
    )


def list_activities(db: Session, *, category_id: Optional[int] = None):
    q = db.query(models.Activity).filter(models.Activity.is_active == True)  # noqa: E712
    if category_id is not None:
        q = q.filter(models.Activity.category_id == category_id)
    return q.order_by(models.Activity.name).all()


# --------------------
# Activity create (dedup, global)
# --------------------

def create_activity(db: Session, activity_in: schemas.ActivityCreate, *, created_by_user_id=None):
    category = db.get(models.ActivityCategory, activity_in.category_id)
    if not category:
        raise ValueError("Unknown category")

    name = activity_in.name.strip()
    existing = (
        db.query(models.Activity)
        .filter(
            models.Activity.category_id == activity_in.category_id,
            func.lower(models.Activity.name) == name.lower(),
        )
        .first()
    )
    if existing:
        if not existing.is_active:
            existing.is_active = True
            db.commit()
            db.refresh(existing)
        return existing

    activity = models.Activity(
        category_id=activity_in.category_id,
        name=name,
        is_active=True,
        created_by_user_id=created_by_user_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity


def update_activity(db: Session, activity_id: int, update: "schemas.ActivityUpdate"):
    activity = db.get(models.Activity, activity_id)
    if not activity:
        return None
    data = update.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(activity, k, v)
    db.commit()
    db.refresh(activity)
    return activity


# --------------------
# Category admin CRUD
# --------------------

def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "category"


def create_category(db: Session, category_in: "schemas.CategoryCreate"):
    name = category_in.name.strip()
    slug = _slugify(name)
    max_order = db.query(func.max(models.ActivityCategory.sort_order)).scalar() or 0
    cat = models.ActivityCategory(
        name=name, slug=slug, is_active=True, sort_order=max_order + 1,
        created_at=datetime.now(timezone.utc),
    )
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


def update_category(db: Session, category_id: int, update: "schemas.CategoryUpdate"):
    cat = db.get(models.ActivityCategory, category_id)
    if not cat:
        return None
    for k, v in update.model_dump(exclude_unset=True).items():
        setattr(cat, k, v)
    db.commit()
    db.refresh(cat)
    return cat


# --------------------
# Field admin CRUD
# --------------------

def create_field(db: Session, category_id: int, field_in: "schemas.CategoryFieldCreate"):
    cat = db.get(models.ActivityCategory, category_id)
    if not cat:
        raise ValueError("Unknown category")
    dup = (
        db.query(models.CategoryField)
        .filter(models.CategoryField.category_id == category_id,
                models.CategoryField.key == field_in.key)
        .first()
    )
    if dup:
        raise ValueError(f"Field key '{field_in.key}' already exists in this category")
    field = models.CategoryField(
        category_id=category_id,
        key=field_in.key,
        label=field_in.label.strip(),
        field_type=field_in.field_type,
        unit=(field_in.unit or None),
        is_required=field_in.is_required,
        is_active=True,
        sort_order=field_in.sort_order,
        created_at=datetime.now(timezone.utc),
    )
    db.add(field)
    db.commit()
    db.refresh(field)
    return field


def update_field(db: Session, field_id: int, update: "schemas.CategoryFieldUpdate"):
    field = db.get(models.CategoryField, field_id)
    if not field:
        return None
    for k, v in update.model_dump(exclude_unset=True).items():
        setattr(field, k, v)
    db.commit()
    db.refresh(field)
    return field


def soft_delete_field(db: Session, field_id: int):
    field = db.get(models.CategoryField, field_id)
    if not field:
        return None
    field.is_active = False
    db.commit()
    return field


# --------------------
# Session-activity reconciliation
# --------------------

def _resolve_person_slot(session, purchase, raw_slot):
    """Normalize/validate a desired person_slot for one activity row.
    Returns the slot to store (None/1/2). Raises ValueError on bad input.

    Partner-existence priority: session.partner_user_id -> purchase.partner_user_id
    -> purchase.partner_email. Mirrors _person_name_for_slot in crud.py; update both
    together when the priority chain changes."""
    if raw_slot is None:
        return None
    # A session with no resolvable purchase is treated as solo; any explicit slot is ignored.
    if purchase is None:
        return None
    if raw_slot not in (1, 2):
        raise ValueError("person_slot must be null, 1, or 2")
    if purchase.num_people <= 1:
        return None  # single-person sessions are never per-person
    if raw_slot == 2:
        has_partner = bool(
            session.partner_user_id
            or purchase.partner_user_id
            or purchase.partner_email
        )
        if not has_partner:
            raise ValueError("Cannot assign activity to Person B: session has no partner")
    return raw_slot


def reconcile_session_activities(db: Session, session: models.Session, items, *, created_by_user_id=None):
    """Reconcile a session's activities to the desired set `items`
    (list of schemas.SessionActivityInput). Upsert by id, delete omitted.
    Validates each row's values and person_slot; raises ValueError on any
    problem. Does NOT commit — caller commits so the edit is atomic."""
    purchase = db.get(models.Purchase, session.purchase_id)
    existing = {sa.id: sa for sa in session.activities}
    seen_ids = set()

    for idx, item in enumerate(items):
        activity = db.get(models.Activity, item.activity_id)
        if not activity or not activity.is_active:
            raise ValueError("Selected activity is not available (inactive or unknown)")
        cleaned = validate_activity_values(db, activity, item.values)
        slot = _resolve_person_slot(session, purchase, item.person_slot)

        if item.id is not None:
            sa = existing.get(item.id)
            if sa is None:
                raise ValueError("Activity row is not on this session")
            sa.activity_id = activity.id
            sa.values = cleaned
            sa.notes = item.notes
            sa.sort_order = idx
            sa.person_slot = slot
            seen_ids.add(item.id)
        else:
            sa = models.SessionActivity(
                session_id=session.id,
                activity_id=activity.id,
                values=cleaned,
                notes=item.notes,
                sort_order=idx,
                person_slot=slot,
                created_at=datetime.now(timezone.utc),
            )
            db.add(sa)

    for sid, sa in existing.items():
        if sid not in seen_ids:
            db.delete(sa)
