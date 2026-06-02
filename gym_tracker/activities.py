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
