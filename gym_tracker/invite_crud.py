from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from gym_tracker import models


def get_invite_by_email(db: Session, email: str) -> Optional[models.UserInvite]:
    """Look up an invite by email (case-insensitive)."""
    return (
        db.query(models.UserInvite)
        .filter(models.UserInvite.email == email.lower().strip())
        .first()
    )


def create_invite(
    db: Session,
    *,
    email: str,
    role: str,
    invited_by_user_id: Optional[int] = None,
    trainer_id: Optional[int] = None,
) -> models.UserInvite:
    """Create a new invite. Raises ValueError if email already invited."""
    email = email.lower().strip()
    existing = get_invite_by_email(db, email)
    if existing:
        raise ValueError(f"Invite already exists for {email}")
    invite = models.UserInvite(
        email=email,
        role=role,
        invited_by_user_id=invited_by_user_id,
        trainer_id=trainer_id,
        created_at=datetime.utcnow(),
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)
    return invite


def accept_invite(db: Session, invite: models.UserInvite) -> None:
    """Mark an invite as accepted (sets accepted_at to now)."""
    invite.accepted_at = datetime.utcnow()
    db.commit()


def list_invites(db: Session) -> list[models.UserInvite]:
    """Return all invites ordered by created_at desc."""
    return db.query(models.UserInvite).order_by(models.UserInvite.created_at.desc()).all()


def delete_invite(db: Session, invite_id: int) -> bool:
    """Delete an invite by id. Returns True if deleted, False if not found."""
    invite = db.get(models.UserInvite, invite_id)
    if not invite:
        return False
    db.delete(invite)
    db.commit()
    return True
