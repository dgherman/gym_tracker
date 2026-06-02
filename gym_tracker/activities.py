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
    """Validate a values dict against the activity's category active fields.
    Returns a cleaned dict (only present, coerced values). Raises ValueError."""
    values = values or {}
    fields = (
        db.query(models.CategoryField)
        .filter(
            models.CategoryField.category_id == activity.category_id,
            models.CategoryField.is_active == True,  # noqa: E712
        )
        .all()
    )
    by_key = {f.key: f for f in fields}

    for k in values:
        if k not in by_key:
            raise ValueError(f"Unknown field '{k}' for this activity")

    cleaned = {}
    for f in fields:
        raw = values.get(f.key)
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            if f.is_required:
                raise ValueError(f"Field '{f.label}' is required")
            continue
        cleaned[f.key] = _coerce(f.field_type, f.label, raw)
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

def reconcile_session_activities(db: Session, session: models.Session, items, *, created_by_user_id=None):
    """Reconcile a session's activities to the desired set `items`
    (list of schemas.SessionActivityInput). Upsert by id, delete omitted.
    Validates each row's values; raises ValueError on any problem.
    Does NOT commit — caller commits so the whole session edit is atomic."""
    existing = {sa.id: sa for sa in session.activities}
    seen_ids = set()

    for idx, item in enumerate(items):
        activity = db.get(models.Activity, item.activity_id)
        if not activity or not activity.is_active:
            raise ValueError("Selected activity is not available (inactive or unknown)")
        cleaned = validate_activity_values(db, activity, item.values)

        if item.id is not None:
            sa = existing.get(item.id)
            if sa is None:
                raise ValueError("Activity row is not on this session")
            sa.activity_id = activity.id
            sa.values = cleaned
            sa.notes = item.notes
            sa.sort_order = idx
            seen_ids.add(item.id)
        else:
            sa = models.SessionActivity(
                session_id=session.id,
                activity_id=activity.id,
                values=cleaned,
                notes=item.notes,
                sort_order=idx,
                created_at=datetime.now(timezone.utc),
            )
            db.add(sa)

    for sid, sa in existing.items():
        if sid not in seen_ids:
            db.delete(sa)
