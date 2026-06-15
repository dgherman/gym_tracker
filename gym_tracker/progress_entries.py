"""Standalone progress entries: CRUD + permission checks.

Entries record activity values for a date without a session and never touch
package/session accounting. Permission rule: a user always acts on their own
entries; acting on another user's requires role admin or trainer.

Raises: PermissionError (403 at API), LookupError (404), ValueError (400).
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import models, schemas
from .activities import validate_activity_values

PRIVILEGED_ROLES = ("admin", "trainer")


def _check_can_act_for(actor: models.User, target_user_id: int) -> None:
    if actor.id == target_user_id:
        return
    if actor.role in PRIVILEGED_ROLES:
        return
    raise PermissionError("Not allowed to act for another user")


def _serialize(db: Session, pe: models.ProgressEntry) -> dict:
    activity = pe.activity
    category = db.get(models.ActivityCategory, activity.category_id) if activity else None
    return {
        "id": pe.id,
        "user_id": pe.user_id,
        "activity_id": pe.activity_id,
        "activity_name": activity.name if activity else "(unknown)",
        "category_id": category.id if category else 0,
        "category_name": category.name if category else "(unknown)",
        "entry_date": pe.entry_date,
        "values": pe.values or {},
        "notes": pe.notes,
        "created_at": pe.created_at,
    }


def create_entry(db: Session, *, actor: models.User, data: schemas.ProgressEntryCreate) -> dict:
    target_user_id = data.user_id if data.user_id is not None else actor.id
    _check_can_act_for(actor, target_user_id)
    activity = db.get(models.Activity, data.activity_id)
    if not activity:
        raise LookupError("Activity not found")
    cleaned = validate_activity_values(db, activity, data.values)
    pe = models.ProgressEntry(
        user_id=target_user_id,
        activity_id=activity.id,
        entry_date=data.entry_date,
        values=cleaned,
        notes=(data.notes or None),
        created_by_user_id=actor.id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(pe)
    db.commit()
    db.refresh(pe)
    return _serialize(db, pe)


def list_entries(db: Session, *, actor: models.User, user_id: int, limit: int = 100) -> list[dict]:
    _check_can_act_for(actor, user_id)
    q = (
        db.query(models.ProgressEntry)
        .filter(models.ProgressEntry.user_id == user_id)
        .order_by(models.ProgressEntry.entry_date.desc(), models.ProgressEntry.id.desc())
        .limit(limit)
    )
    return [_serialize(db, pe) for pe in q.all()]


def _get_owned(db: Session, *, actor: models.User, entry_id: int) -> models.ProgressEntry:
    pe = db.get(models.ProgressEntry, entry_id)
    if not pe:
        raise LookupError("Entry not found")
    _check_can_act_for(actor, pe.user_id)
    return pe


def update_entry(db: Session, *, actor: models.User, entry_id: int,
                 data: schemas.ProgressEntryUpdate) -> dict:
    pe = _get_owned(db, actor=actor, entry_id=entry_id)
    patch = data.model_dump(exclude_unset=True)
    new_activity_id = pe.activity_id
    activity_changed = False
    if "activity_id" in patch:
        activity = db.get(models.Activity, patch["activity_id"])
        if not activity:
            raise LookupError("Activity not found")
        activity_changed = activity.id != pe.activity_id
        new_activity_id = activity.id
    if "values" in patch or activity_changed:
        activity = db.get(models.Activity, new_activity_id)
        # Validate BEFORE mutating pe so a ValueError leaves the row unchanged
        new_values = validate_activity_values(db, activity, patch.get("values", pe.values))
        pe.activity_id = new_activity_id
        pe.values = new_values
    elif "activity_id" in patch:
        pe.activity_id = new_activity_id
    if "entry_date" in patch:
        pe.entry_date = patch["entry_date"]
    if "notes" in patch:
        pe.notes = patch["notes"] or None
    db.commit()
    db.refresh(pe)
    return _serialize(db, pe)


def delete_entry(db: Session, *, actor: models.User, entry_id: int) -> None:
    pe = _get_owned(db, actor=actor, entry_id=entry_id)
    db.delete(pe)
    db.commit()
