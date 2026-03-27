# Calendar Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a calendar page to gym_tracker that lets trainers schedule sessions (with recurring support and credit reservation) and lets clients view their own schedule.

**Architecture:** Extend the existing `sessions` table with `status`, `client_user_id`, and `recurrence_group_id` columns. Add a new `calendar_crud.py` module for calendar-specific operations and a new `invite_crud.py` module for invitation management. All routes continue to live in `main.py`. FullCalendar Standard (MIT) is loaded from CDN.

**Tech Stack:** FastAPI, SQLAlchemy 1.4+, Alembic, MySQL, FullCalendar v6 (CDN), python-dateutil (new dependency), Pydantic v2, Jinja2/Bootstrap 5.3.

---

## File Map

**Modified:**
- `requirements.txt` — add `python-dateutil>=2.8`
- `gym_tracker/models.py` — add `RecurrenceGroup`, `UserInvite`; modify `Session`, `Trainer`
- `gym_tracker/schemas.py` — add calendar/invite schemas; fix `Session.purchase_id` nullable
- `gym_tracker/crud.py` — fix `get_sessions` for nullable purchase; set `status`/`client_user_id` in `create_session`
- `gym_tracker/auth.py` — replace `ALLOWED_EMAILS` check with invite lookup; add trainer auto-link
- `gym_tracker/config.py` — remove `ALLOWED_EMAILS` and `allowed_emails_set`
- `main.py` — add `require_trainer`; add calendar, scheduling, and invite routes
- `templates/_nav.html` — add Calendar link

**New:**
- `gym_tracker/invite_crud.py` — invite CRUD operations
- `gym_tracker/calendar_crud.py` — calendar event queries, scheduling, horizon extension
- `templates/calendar.html` — FullCalendar page
- `templates/admin/invites.html` — invite management admin page
- `alembic/versions/XXXXXX_calendar_feature.py` — generated + edited migration
- `gym_tracker/tests/test_invite_crud.py`
- `gym_tracker/tests/test_calendar_crud.py`

---

## Task 1: Add python-dateutil dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add the dependency**

Open `requirements.txt` and add after the `pydantic` line:
```
python-dateutil>=2.8
```

- [ ] **Step 2: Install in the venv**

```bash
cd ~/Documents/projects/personal/gym_tracker
source .venv/bin/activate
pip install python-dateutil>=2.8
```

Expected: `Successfully installed python-dateutil-2.x.x`

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "feat: add python-dateutil for recurring session date math"
```

---

## Task 2: Update ORM models

**Files:**
- Modify: `gym_tracker/models.py`

- [ ] **Step 1: Add Time import and update models**

Replace the full contents of `gym_tracker/models.py` with:

```python
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
)
from sqlalchemy.orm import relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    google_sub = Column(String(255), unique=True, index=True, nullable=False)
    email = Column(String(255), index=True, nullable=True)
    email_verified = Column(Boolean, nullable=False, default=False)
    full_name = Column(String(255), nullable=True)
    avatar_url = Column(String(512), nullable=True)
    role = Column(String(50), nullable=False, default="client")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    logged_purchases = relationship("Purchase", foreign_keys="[Purchase.logged_by_user_id]", back_populates="logged_by_user")
    created_sessions = relationship("Session", foreign_keys="[Session.created_by_user_id]", back_populates="created_by_user")

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} sub={self.google_sub!r}>"


class Purchase(Base):
    __tablename__ = "purchases"

    id = Column(Integer, primary_key=True, index=True)
    duration_minutes = Column(Integer, index=True)
    total_sessions = Column(Integer)
    sessions_remaining = Column(Integer)
    purchase_date = Column(DateTime, index=True)
    cost = Column(Float, default=0.0)
    num_people = Column(Integer, nullable=False, default=1)
    logged_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    logged_by_user = relationship("User", foreign_keys=[logged_by_user_id], back_populates="logged_purchases")
    partner_email = Column(String(255), nullable=True)
    partner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    partner_user = relationship("User", foreign_keys=[partner_user_id])
    sessions = relationship("Session", back_populates="purchase")


class Trainer(Base):
    __tablename__ = "trainers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    # New: for trainer user account linking
    email = Column(String(255), nullable=True, unique=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    sessions = relationship("Session", back_populates="trainer_rel")
    user = relationship("User", foreign_keys=[user_id])

    def __repr__(self) -> str:
        return f"<Trainer id={self.id} name={self.name!r} active={self.is_active}>"


class RecurrenceGroup(Base):
    __tablename__ = "recurrence_groups"

    id = Column(Integer, primary_key=True, index=True)
    frequency = Column(String(20), nullable=False)   # "weekly" | "biweekly" | "monthly"
    day_of_week = Column(Integer, nullable=False)    # 0=Mon ... 6=Sun
    time_of_day = Column(Time, nullable=False)
    duration_minutes = Column(Integer, nullable=False)
    trainer_id = Column(Integer, ForeignKey("trainers.id"), nullable=False)
    client_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    horizon_through = Column(DateTime, nullable=False)

    sessions = relationship("Session", back_populates="recurrence_group")
    trainer = relationship("Trainer")
    client = relationship("User", foreign_keys=[client_user_id])
    purchase = relationship("Purchase")


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=True)  # nullable for package-less sessions
    session_date = Column(DateTime, index=True)
    duration_minutes = Column(Integer)
    trainer = Column(String(255), index=True)         # legacy string field
    trainer_id = Column(Integer, ForeignKey("trainers.id"), nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_by_user = relationship("User", foreign_keys=[created_by_user_id], back_populates="created_sessions")
    partner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    partner_user = relationship("User", foreign_keys=[partner_user_id])

    # New calendar columns
    status = Column(String(20), nullable=False, default="completed")
    client_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    scheduled_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    recurrence_group_id = Column(Integer, ForeignKey("recurrence_groups.id"), nullable=True)
    notes = Column(Text, nullable=True)

    purchase = relationship("Purchase", back_populates="sessions")
    trainer_rel = relationship("Trainer", back_populates="sessions")
    client_user = relationship("User", foreign_keys=[client_user_id])
    scheduled_by = relationship("User", foreign_keys=[scheduled_by_user_id])
    recurrence_group = relationship("RecurrenceGroup", back_populates="sessions")


class Package(Base):
    __tablename__ = "packages"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    duration_minutes = Column(Integer, nullable=False, index=True)
    num_people = Column(Integer, nullable=False, default=1)
    total_sessions = Column(Integer, nullable=False)
    price_per_session = Column(Float, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<Package id={self.id} name={self.name!r} duration={self.duration_minutes} people={self.num_people}>"


class UserInvite(Base):
    __tablename__ = "user_invites"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    role = Column(String(50), nullable=False)
    trainer_id = Column(Integer, ForeignKey("trainers.id"), nullable=True)
    invited_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    accepted_at = Column(DateTime, nullable=True)

    trainer = relationship("Trainer")
    invited_by = relationship("User", foreign_keys=[invited_by_user_id])
```

- [ ] **Step 2: Verify existing tests still import cleanly**

```bash
cd ~/Documents/projects/personal/gym_tracker
source .venv/bin/activate
python -c "from gym_tracker import models; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add gym_tracker/models.py
git commit -m "feat: add RecurrenceGroup, UserInvite models; extend Session and Trainer"
```

---

## Task 3: Alembic migration

**Files:**
- Create: `alembic/versions/XXXXXX_calendar_feature.py` (autogenerated then edited)

- [ ] **Step 1: Generate migration skeleton**

```bash
cd ~/Documents/projects/personal/gym_tracker
source .venv/bin/activate
alembic revision --autogenerate -m "calendar_feature"
```

Note the generated filename (e.g., `alembic/versions/abc123_calendar_feature.py`). Open it.

- [ ] **Step 2: Replace the upgrade() and downgrade() functions**

The autogenerated file will have an `upgrade()` function. Replace it entirely with the following (keep the file header/imports that alembic generated):

```python
def upgrade() -> None:
    # 1. Add email and user_id to trainers
    op.add_column("trainers", sa.Column("email", sa.String(255), nullable=True))
    op.create_unique_constraint("uq_trainers_email", "trainers", ["email"])
    op.add_column("trainers", sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True))

    # 2. Create recurrence_groups table
    op.create_table(
        "recurrence_groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("frequency", sa.String(20), nullable=False),
        sa.Column("day_of_week", sa.Integer(), nullable=False),
        sa.Column("time_of_day", sa.Time(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("trainer_id", sa.Integer(), sa.ForeignKey("trainers.id"), nullable=False),
        sa.Column("client_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("purchase_id", sa.Integer(), sa.ForeignKey("purchases.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("horizon_through", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_recurrence_groups_id", "recurrence_groups", ["id"])

    # 3. Create user_invites table
    op.create_table(
        "user_invites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("trainer_id", sa.Integer(), sa.ForeignKey("trainers.id"), nullable=True),
        sa.Column("invited_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_user_invites_id", "user_invites", ["id"])
    op.create_index("ix_user_invites_email", "user_invites", ["email"])

    # 4. Add new columns to sessions
    op.add_column("sessions", sa.Column("status", sa.String(20), nullable=True))
    op.add_column("sessions", sa.Column("client_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True))
    op.add_column("sessions", sa.Column("scheduled_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True))
    op.add_column("sessions", sa.Column("recurrence_group_id", sa.Integer(), sa.ForeignKey("recurrence_groups.id"), nullable=True))
    op.add_column("sessions", sa.Column("notes", sa.Text(), nullable=True))

    # Make purchase_id nullable
    op.alter_column("sessions", "purchase_id", nullable=True)

    # Backfill status = "completed" for all existing sessions
    op.execute("UPDATE sessions SET status = 'completed' WHERE status IS NULL")
    op.alter_column("sessions", "status", nullable=False)

    # Backfill client_user_id from purchase owner where available
    op.execute(
        "UPDATE sessions s "
        "JOIN purchases p ON s.purchase_id = p.id "
        "SET s.client_user_id = p.logged_by_user_id "
        "WHERE s.client_user_id IS NULL AND p.logged_by_user_id IS NOT NULL"
    )

    # Backfill user_invites from all existing users (so no one is locked out)
    op.execute(
        "INSERT INTO user_invites (email, role, created_at, accepted_at) "
        "SELECT email, role, created_at, created_at "
        "FROM users "
        "WHERE email IS NOT NULL AND email != '' "
        "ON DUPLICATE KEY UPDATE email=email"
    )


def downgrade() -> None:
    op.drop_index("ix_user_invites_email", table_name="user_invites")
    op.drop_index("ix_user_invites_id", table_name="user_invites")
    op.drop_table("user_invites")
    op.drop_column("sessions", "notes")
    op.drop_column("sessions", "recurrence_group_id")
    op.drop_column("sessions", "scheduled_by_user_id")
    op.drop_column("sessions", "client_user_id")
    op.drop_column("sessions", "status")
    op.alter_column("sessions", "purchase_id", nullable=False)
    op.drop_index("ix_recurrence_groups_id", table_name="recurrence_groups")
    op.drop_table("recurrence_groups")
    op.drop_column("trainers", "user_id")
    op.drop_constraint("uq_trainers_email", "trainers", type_="unique")
    op.drop_column("trainers", "email")
```

- [ ] **Step 3: Verify migration runs against the dev DB**

```bash
alembic upgrade head
```

Expected: Migration applies cleanly with no errors.

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/
git commit -m "feat: calendar feature migration — new tables, session columns, backfills"
```

---

## Task 4: Invite CRUD + tests

**Files:**
- Create: `gym_tracker/invite_crud.py`
- Create: `gym_tracker/tests/test_invite_crud.py`

- [ ] **Step 1: Write the failing tests**

Create `gym_tracker/tests/test_invite_crud.py`:

```python
import pytest
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from gym_tracker.database import Base
from gym_tracker import models
from gym_tracker import invite_crud

test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="module")
def db():
    Base.metadata.create_all(bind=test_engine)
    session = TestSessionLocal()
    # Seed a user to act as inviter
    admin = models.User(
        google_sub="sub-admin",
        email="admin@gym.com",
        email_verified=True,
        role="admin",
        is_active=True,
        created_at=datetime.utcnow(),
        last_login_at=datetime.utcnow(),
    )
    session.add(admin)
    session.commit()
    session.refresh(admin)
    session._test_admin_id = admin.id
    try:
        yield session
    finally:
        session.close()


def test_create_invite(db):
    invite = invite_crud.create_invite(db, email="alice@gym.com", role="client", invited_by_user_id=db._test_admin_id)
    assert invite.id is not None
    assert invite.email == "alice@gym.com"
    assert invite.role == "client"
    assert invite.accepted_at is None


def test_get_invite_by_email_found(db):
    result = invite_crud.get_invite_by_email(db, "alice@gym.com")
    assert result is not None
    assert result.email == "alice@gym.com"


def test_get_invite_by_email_case_insensitive(db):
    result = invite_crud.get_invite_by_email(db, "ALICE@GYM.COM")
    assert result is not None


def test_get_invite_by_email_not_found(db):
    result = invite_crud.get_invite_by_email(db, "nobody@gym.com")
    assert result is None


def test_list_invites(db):
    invites = invite_crud.list_invites(db)
    assert len(invites) >= 1


def test_accept_invite(db):
    invite = invite_crud.get_invite_by_email(db, "alice@gym.com")
    invite_crud.accept_invite(db, invite)
    assert invite.accepted_at is not None


def test_delete_invite(db):
    invite = invite_crud.create_invite(db, email="bob@gym.com", role="client", invited_by_user_id=db._test_admin_id)
    invite_crud.delete_invite(db, invite.id)
    assert invite_crud.get_invite_by_email(db, "bob@gym.com") is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd ~/Documents/projects/personal/gym_tracker
source .venv/bin/activate
pytest gym_tracker/tests/test_invite_crud.py -v
```

Expected: `ModuleNotFoundError: No module named 'gym_tracker.invite_crud'`

- [ ] **Step 3: Create invite_crud.py**

Create `gym_tracker/invite_crud.py`:

```python
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
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest gym_tracker/tests/test_invite_crud.py -v
```

Expected: All 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add gym_tracker/invite_crud.py gym_tracker/tests/test_invite_crud.py
git commit -m "feat: add invite CRUD with tests"
```

---

## Task 5: Update auth.py — invite-only signup + trainer auto-link

**Files:**
- Modify: `gym_tracker/auth.py`
- Modify: `gym_tracker/config.py`

- [ ] **Step 1: Remove ALLOWED_EMAILS from config.py**

In `gym_tracker/config.py`, remove the `ALLOWED_EMAILS` line and the `allowed_emails_set` property:

```python
# Remove these two lines/blocks:
ALLOWED_EMAILS: str = os.getenv("ALLOWED_EMAILS", "")

@property
def allowed_emails_set(self) -> set[str]:
    return {e.strip().lower() for e in self.ALLOWED_EMAILS.split(",") if e.strip()}
```

The resulting `Settings` class should not have `ALLOWED_EMAILS` or `allowed_emails_set`.

- [ ] **Step 2: Update auth_callback in auth.py**

Replace the import block at the top of `gym_tracker/auth.py` to add `invite_crud`:

```python
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.responses import RedirectResponse
from authlib.integrations.starlette_client import OAuth
from sqlalchemy.orm import Session

from gym_tracker.config import get_settings
from gym_tracker.database import SessionLocal
from gym_tracker import models, invite_crud
```

- [ ] **Step 3: Replace the auth_callback body**

Replace the full `auth_callback` function (keeping the `@router.get` decorator):

```python
@router.get("/auth/callback")
async def auth_callback(request: Request, db: Session = Depends(get_db)):
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"OAuth exchange failed: {e}")

    userinfo = token.get("userinfo") or {}
    claims = {**token.get("claims", {}), **userinfo}

    google_sub: Optional[str] = claims.get("sub")
    email: str = (claims.get("email") or "").lower()
    email_verified: bool = bool(claims.get("email_verified"))
    full_name: str = claims.get("name") or ""
    avatar_url: str = claims.get("picture") or ""

    if not google_sub:
        raise HTTPException(status_code=401, detail="Missing Google subject (sub)")

    # Invite-only: reject anyone not in user_invites
    invite = invite_crud.get_invite_by_email(db, email) if email else None
    if not invite:
        raise HTTPException(status_code=403, detail="Access denied: you have not been invited.")

    # Upsert user by google_sub
    now = datetime.utcnow()
    user = db.query(models.User).filter(models.User.google_sub == google_sub).one_or_none()
    if user:
        user.email = email or user.email
        user.email_verified = email_verified
        user.full_name = full_name or user.full_name
        user.avatar_url = avatar_url or user.avatar_url
        user.last_login_at = now
    else:
        user = models.User(
            google_sub=google_sub,
            email=email,
            email_verified=email_verified,
            full_name=full_name,
            avatar_url=avatar_url,
            role=invite.role,   # role comes from invite
            is_active=True,
            created_at=now,
            last_login_at=now,
        )
        db.add(user)
        db.flush()  # get user.id before linking

    # Accept invite on first login
    if invite.accepted_at is None:
        invite_crud.accept_invite(db, invite)

    # Auto-link trainer record: invite.trainer_id takes priority
    trainer = None
    if invite.trainer_id:
        trainer = db.get(models.Trainer, invite.trainer_id)
    elif email:
        trainer = (
            db.query(models.Trainer)
            .filter(models.Trainer.email == email, models.Trainer.user_id.is_(None))
            .first()
        )
    if trainer and not trainer.user_id:
        trainer.user_id = user.id

    db.commit()
    db.refresh(user)

    # Auto-link partner purchases (existing behavior)
    if email:
        unlinked = db.query(models.Purchase).filter(
            models.Purchase.partner_email == email,
            models.Purchase.partner_user_id.is_(None),
        ).all()
        for purchase in unlinked:
            purchase.partner_user_id = user.id
        if unlinked:
            db.commit()

    request.session["user_id"] = user.id
    return RedirectResponse(url=settings.BASE_URL)
```

- [ ] **Step 4: Verify auth module loads**

```bash
python -c "from gym_tracker.auth import router; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add gym_tracker/auth.py gym_tracker/config.py
git commit -m "feat: replace ALLOWED_EMAILS with invite-only signup; auto-link trainer on OAuth"
```

---

## Task 6: Fix crud.py for nullable purchase + set status on create_session

**Files:**
- Modify: `gym_tracker/crud.py`

The existing `get_sessions` does `purchase = db.get(models.Purchase, sess.purchase_id)` then immediately accesses `purchase.sessions_remaining`, which will crash with `AttributeError` when `purchase_id` is `None`. Fix it. Also update `create_session` to set `status="completed"` and `client_user_id`.

- [ ] **Step 1: Update get_sessions for nullable purchase**

Find this block in `crud.py` (inside `get_sessions`):

```python
    for sess in sessions:
        purchase = db.get(models.Purchase, sess.purchase_id)
        sess.purchase_exhausted = (purchase.sessions_remaining == 0)
        if user_id is not None:
            _annotate_session(sess, purchase, user_id)
```

Replace with:

```python
    for sess in sessions:
        purchase = db.get(models.Purchase, sess.purchase_id) if sess.purchase_id else None
        sess.purchase_exhausted = (purchase.sessions_remaining == 0) if purchase else False
        if user_id is not None:
            _annotate_session(sess, purchase, user_id)
```

- [ ] **Step 2: Update create_session to set status and client_user_id**

In `create_session`, find the `db_session = models.Session(...)` block and add two fields:

```python
    db_session = models.Session(
        purchase_id=purchase.id,
        duration_minutes=duration_minutes,
        trainer=trainer,
        session_date=datetime.now(timezone.utc),
        created_by_user_id=created_by_user_id,
        partner_user_id=session_partner_id,
        status="completed",                      # add this
        client_user_id=created_by_user_id,       # add this
    )
```

- [ ] **Step 3: Run existing tests to confirm nothing is broken**

```bash
pytest gym_tracker/tests/test_crud.py -v
```

Expected: All existing tests PASS.

- [ ] **Step 4: Commit**

```bash
git add gym_tracker/crud.py
git commit -m "fix: handle nullable purchase_id in get_sessions; set status/client_user_id in create_session"
```

---

## Task 7: Calendar CRUD — single session scheduling

**Files:**
- Create: `gym_tracker/calendar_crud.py`
- Create: `gym_tracker/tests/test_calendar_crud.py` (partial — extended in Tasks 8 and 9)

- [ ] **Step 1: Write failing tests for single session scheduling**

Create `gym_tracker/tests/test_calendar_crud.py`:

```python
import pytest
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from gym_tracker.database import Base
from gym_tracker import models, calendar_crud

test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="module")
def db():
    Base.metadata.create_all(bind=test_engine)
    session = TestSessionLocal()

    trainer_user = models.User(
        google_sub="sub-trainer", email="trainer@gym.com", email_verified=True,
        role="trainer", is_active=True, created_at=datetime.utcnow(), last_login_at=datetime.utcnow(),
    )
    client_user = models.User(
        google_sub="sub-client", email="client@gym.com", email_verified=True,
        role="client", is_active=True, created_at=datetime.utcnow(), last_login_at=datetime.utcnow(),
    )
    session.add_all([trainer_user, client_user])
    session.flush()

    trainer = models.Trainer(
        name="Coach Ana", is_active=True, created_at=datetime.utcnow(),
        email="trainer@gym.com", user_id=trainer_user.id,
    )
    session.add(trainer)
    session.flush()

    purchase = models.Purchase(
        duration_minutes=60, total_sessions=10, sessions_remaining=10,
        purchase_date=datetime.utcnow(), cost=500.0,
        logged_by_user_id=client_user.id, num_people=1,
    )
    session.add(purchase)
    session.commit()

    session._trainer_id = trainer.id
    session._trainer_user_id = trainer_user.id
    session._client_user_id = client_user.id
    session._purchase_id = purchase.id
    try:
        yield session
    finally:
        session.close()


def test_schedule_single_session_reserves_credit(db):
    before = db.get(models.Purchase, db._purchase_id).sessions_remaining
    future = datetime.utcnow() + timedelta(days=3)
    sess = calendar_crud.schedule_session(
        db,
        trainer_id=db._trainer_id,
        client_user_id=db._client_user_id,
        session_date=future,
        duration_minutes=60,
        purchase_id=db._purchase_id,
        scheduled_by_user_id=db._trainer_user_id,
        notes="First session",
    )
    db.refresh(db.get(models.Purchase, db._purchase_id))
    after = db.get(models.Purchase, db._purchase_id).sessions_remaining
    assert sess.status == "scheduled"
    assert sess.client_user_id == db._client_user_id
    assert after == before - 1


def test_schedule_session_without_package(db):
    future = datetime.utcnow() + timedelta(days=5)
    sess = calendar_crud.schedule_session(
        db,
        trainer_id=db._trainer_id,
        client_user_id=db._client_user_id,
        session_date=future,
        duration_minutes=60,
        purchase_id=None,
        scheduled_by_user_id=db._trainer_user_id,
    )
    assert sess.status == "scheduled"
    assert sess.purchase_id is None


def test_schedule_session_no_credit_deducted_for_no_package(db):
    before = db.get(models.Purchase, db._purchase_id).sessions_remaining
    future = datetime.utcnow() + timedelta(days=7)
    calendar_crud.schedule_session(
        db,
        trainer_id=db._trainer_id,
        client_user_id=db._client_user_id,
        session_date=future,
        duration_minutes=60,
        purchase_id=None,
        scheduled_by_user_id=db._trainer_user_id,
    )
    after = db.get(models.Purchase, db._purchase_id).sessions_remaining
    assert after == before  # no change
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest gym_tracker/tests/test_calendar_crud.py -v
```

Expected: `ModuleNotFoundError: No module named 'gym_tracker.calendar_crud'`

- [ ] **Step 3: Create calendar_crud.py with schedule_session**

Create `gym_tracker/calendar_crud.py`:

```python
from datetime import date, datetime, time, timedelta
from typing import Optional

from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import Session

from gym_tracker import models

# Horizon: generate sessions 3 months ahead
HORIZON_MONTHS = 3

STATUS_COLORS = {
    "scheduled": "#0d6efd",   # blue
    "completed": "#198754",   # green
    "cancelled": "#6c757d",   # grey
}
NO_PACKAGE_COLOR = "#fd7e14"  # orange


def schedule_session(
    db: Session,
    *,
    trainer_id: int,
    client_user_id: int,
    session_date: datetime,
    duration_minutes: int,
    purchase_id: Optional[int],
    scheduled_by_user_id: int,
    notes: Optional[str] = None,
    recurrence_group_id: Optional[int] = None,
) -> models.Session:
    """
    Create one scheduled session. Decrements sessions_remaining on the purchase
    if purchase_id is set. Raises ValueError if purchase has no remaining sessions.
    """
    if purchase_id is not None:
        purchase = db.get(models.Purchase, purchase_id)
        if not purchase:
            raise ValueError("Purchase not found")
        if purchase.sessions_remaining <= 0:
            raise ValueError("No sessions remaining in this package")
        purchase.sessions_remaining -= 1

    sess = models.Session(
        trainer_id=trainer_id,
        client_user_id=client_user_id,
        session_date=session_date,
        duration_minutes=duration_minutes,
        purchase_id=purchase_id,
        scheduled_by_user_id=scheduled_by_user_id,
        notes=notes,
        status="scheduled",
        recurrence_group_id=recurrence_group_id,
        trainer=None,   # legacy field unused for scheduled sessions
    )
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return sess
```

- [ ] **Step 4: Run the single-session tests**

```bash
pytest gym_tracker/tests/test_calendar_crud.py::test_schedule_single_session_reserves_credit gym_tracker/tests/test_calendar_crud.py::test_schedule_session_without_package gym_tracker/tests/test_calendar_crud.py::test_schedule_session_no_credit_deducted_for_no_package -v
```

Expected: All 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add gym_tracker/calendar_crud.py gym_tracker/tests/test_calendar_crud.py
git commit -m "feat: add schedule_session with credit reservation"
```

---

## Task 8: Calendar CRUD — recurring sessions + horizon extension

**Files:**
- Modify: `gym_tracker/calendar_crud.py`
- Modify: `gym_tracker/tests/test_calendar_crud.py`

- [ ] **Step 1: Add failing tests for recurring sessions**

Append to `gym_tracker/tests/test_calendar_crud.py`:

```python
def test_schedule_recurring_generates_sessions_in_horizon(db):
    # day_of_week=0 (Monday), weekly, starting from next Monday
    today = datetime.utcnow().date()
    days_until_monday = (0 - today.weekday()) % 7 or 7
    start_date = today + timedelta(days=days_until_monday)

    sessions, group = calendar_crud.schedule_recurring(
        db,
        trainer_id=db._trainer_id,
        client_user_id=db._client_user_id,
        start_date=start_date,
        session_time=time(10, 0),
        duration_minutes=60,
        frequency="weekly",
        purchase_id=None,
        scheduled_by_user_id=db._trainer_user_id,
    )
    assert group.id is not None
    assert group.frequency == "weekly"
    assert len(sessions) >= 1
    # All sessions should be on Mondays
    for s in sessions:
        assert s.session_date.weekday() == 0


def test_extend_horizon_generates_new_sessions(db):
    group = db.query(models.RecurrenceGroup).first()
    original_count = len(group.sessions)
    # Force horizon back by 2 months so extension is triggered
    from datetime import datetime
    group.horizon_through = datetime.utcnow() - timedelta(days=30)
    db.commit()

    calendar_crud.extend_horizon(db, group)
    db.refresh(group)
    assert len(group.sessions) > original_count
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest gym_tracker/tests/test_calendar_crud.py::test_schedule_recurring_generates_sessions_in_horizon gym_tracker/tests/test_calendar_crud.py::test_extend_horizon_generates_new_sessions -v
```

Expected: `AttributeError: module 'gym_tracker.calendar_crud' has no attribute 'schedule_recurring'`

- [ ] **Step 3: Add schedule_recurring and extend_horizon to calendar_crud.py**

Append to `gym_tracker/calendar_crud.py` (after the `schedule_session` function):

```python
def _generate_dates(
    start_date: date,
    day_of_week: int,
    frequency: str,
    through: date,
) -> list[date]:
    """
    Return all dates from start_date through 'through' that fall on day_of_week
    with the given frequency (weekly/biweekly/monthly).
    """
    days_ahead = (day_of_week - start_date.weekday()) % 7
    current = start_date + timedelta(days=days_ahead)

    result = []
    while current <= through:
        result.append(current)
        if frequency == "weekly":
            current += timedelta(weeks=1)
        elif frequency == "biweekly":
            current += timedelta(weeks=2)
        elif frequency == "monthly":
            current += relativedelta(months=1)
        else:
            raise ValueError(f"Unknown frequency: {frequency}")
    return result


def schedule_recurring(
    db: Session,
    *,
    trainer_id: int,
    client_user_id: int,
    start_date: date,
    session_time: time,
    duration_minutes: int,
    frequency: str,
    purchase_id: Optional[int],
    scheduled_by_user_id: int,
    notes: Optional[str] = None,
) -> tuple[list[models.Session], models.RecurrenceGroup]:
    """
    Create a RecurrenceGroup and generate all sessions through now+3 months.
    Credits are reserved immediately for each generated session.
    Returns (sessions_list, group).
    """
    now = datetime.utcnow()
    horizon_date = (now + relativedelta(months=HORIZON_MONTHS)).date()

    group = models.RecurrenceGroup(
        frequency=frequency,
        day_of_week=start_date.weekday(),
        time_of_day=session_time,
        duration_minutes=duration_minutes,
        trainer_id=trainer_id,
        client_user_id=client_user_id,
        purchase_id=purchase_id,
        created_at=now,
        horizon_through=datetime.combine(horizon_date, session_time),
    )
    db.add(group)
    db.flush()   # get group.id before creating sessions

    dates = _generate_dates(start_date, start_date.weekday(), frequency, horizon_date)
    sessions = []
    for d in dates:
        sess = schedule_session(
            db,
            trainer_id=trainer_id,
            client_user_id=client_user_id,
            session_date=datetime.combine(d, session_time),
            duration_minutes=duration_minutes,
            purchase_id=purchase_id,
            scheduled_by_user_id=scheduled_by_user_id,
            notes=notes,
            recurrence_group_id=group.id,
        )
        sessions.append(sess)

    db.commit()
    db.refresh(group)
    return sessions, group


def extend_horizon(db: Session, group: models.RecurrenceGroup) -> list[models.Session]:
    """
    Extend a recurrence group's sessions through now+3 months if needed.
    Returns newly created sessions (empty list if nothing to do).
    """
    now = datetime.utcnow()
    target_horizon = (now + relativedelta(months=HORIZON_MONTHS)).date()
    current_horizon = group.horizon_through.date()

    if current_horizon >= target_horizon:
        return []

    new_start = current_horizon + timedelta(days=1)
    new_dates = _generate_dates(
        new_start,
        group.day_of_week,
        group.frequency,
        target_horizon,
    )

    new_sessions = []
    for d in new_dates:
        sess = schedule_session(
            db,
            trainer_id=group.trainer_id,
            client_user_id=group.client_user_id,
            session_date=datetime.combine(d, group.time_of_day),
            duration_minutes=group.duration_minutes,
            purchase_id=group.purchase_id,
            scheduled_by_user_id=group.trainer_id,   # system-generated extension
            recurrence_group_id=group.id,
        )
        new_sessions.append(sess)

    group.horizon_through = datetime.combine(target_horizon, group.time_of_day)
    db.commit()
    db.refresh(group)
    return new_sessions
```

- [ ] **Step 4: Run recurring tests**

```bash
pytest gym_tracker/tests/test_calendar_crud.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add gym_tracker/calendar_crud.py gym_tracker/tests/test_calendar_crud.py
git commit -m "feat: add schedule_recurring and extend_horizon"
```

---

## Task 9: Calendar CRUD — complete, cancel, reschedule

**Files:**
- Modify: `gym_tracker/calendar_crud.py`
- Modify: `gym_tracker/tests/test_calendar_crud.py`

- [ ] **Step 1: Add failing tests**

Append to `gym_tracker/tests/test_calendar_crud.py`:

```python
def test_complete_session_no_credit_change(db):
    future = datetime.utcnow() + timedelta(days=10)
    sess = calendar_crud.schedule_session(
        db, trainer_id=db._trainer_id, client_user_id=db._client_user_id,
        session_date=future, duration_minutes=60,
        purchase_id=db._purchase_id, scheduled_by_user_id=db._trainer_user_id,
    )
    before = db.get(models.Purchase, db._purchase_id).sessions_remaining
    calendar_crud.complete_session(db, sess)
    after = db.get(models.Purchase, db._purchase_id).sessions_remaining
    assert sess.status == "completed"
    assert after == before  # no change on completion


def test_cancel_session_refunds_credit(db):
    future = datetime.utcnow() + timedelta(days=11)
    sess = calendar_crud.schedule_session(
        db, trainer_id=db._trainer_id, client_user_id=db._client_user_id,
        session_date=future, duration_minutes=60,
        purchase_id=db._purchase_id, scheduled_by_user_id=db._trainer_user_id,
    )
    before = db.get(models.Purchase, db._purchase_id).sessions_remaining
    calendar_crud.cancel_session(db, sess, scope="this")
    after = db.get(models.Purchase, db._purchase_id).sessions_remaining
    assert sess.status == "cancelled"
    assert after == before + 1


def test_reschedule_single_session(db):
    future = datetime.utcnow() + timedelta(days=12)
    sess = calendar_crud.schedule_session(
        db, trainer_id=db._trainer_id, client_user_id=db._client_user_id,
        session_date=future, duration_minutes=60,
        purchase_id=None, scheduled_by_user_id=db._trainer_user_id,
    )
    new_date = future + timedelta(days=1)
    calendar_crud.reschedule_session(db, sess, new_date=new_date, scope="this")
    assert sess.session_date == new_date
    assert sess.recurrence_group_id is None   # detached from group (was None anyway)


def test_cancel_future_sessions(db):
    """Cancel a recurring session with scope='future' cancels all future sessions in the group."""
    today = datetime.utcnow().date()
    days_until_wednesday = (2 - today.weekday()) % 7 or 7
    start = today + timedelta(days=days_until_wednesday)

    sessions, group = calendar_crud.schedule_recurring(
        db, trainer_id=db._trainer_id, client_user_id=db._client_user_id,
        start_date=start, session_time=time(9, 0), duration_minutes=60,
        frequency="weekly", purchase_id=None,
        scheduled_by_user_id=db._trainer_user_id,
    )
    # Cancel from second session onward
    assert len(sessions) >= 2
    pivot = sessions[1]
    calendar_crud.cancel_session(db, pivot, scope="future")

    for s in sessions[1:]:
        db.refresh(s)
        assert s.status == "cancelled"
    db.refresh(sessions[0])
    assert sessions[0].status == "scheduled"  # first session untouched
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest gym_tracker/tests/test_calendar_crud.py::test_complete_session_no_credit_change -v
```

Expected: `AttributeError: module ... has no attribute 'complete_session'`

- [ ] **Step 3: Add complete_session, cancel_session, reschedule_session to calendar_crud.py**

Append to `gym_tracker/calendar_crud.py`:

```python
def complete_session(db: Session, sess: models.Session) -> models.Session:
    """Mark a session as completed. No credit change (already reserved on schedule)."""
    if sess.status != "scheduled":
        raise ValueError(f"Cannot complete session with status '{sess.status}'")
    sess.status = "completed"
    db.commit()
    return sess


def cancel_session(
    db: Session,
    sess: models.Session,
    scope: str,  # "this" or "future"
) -> list[models.Session]:
    """
    Cancel a session. scope='this' cancels only this session.
    scope='future' cancels this and all future sessions in the same recurrence group.
    Credits are refunded for each cancelled session that has a purchase_id.
    Returns list of cancelled sessions.
    """
    if scope not in ("this", "future"):
        raise ValueError("scope must be 'this' or 'future'")

    to_cancel = [sess]

    if scope == "future" and sess.recurrence_group_id:
        group_id = sess.recurrence_group_id
        pivot_date = sess.session_date
        future_siblings = (
            db.query(models.Session)
            .filter(
                models.Session.recurrence_group_id == group_id,
                models.Session.session_date >= pivot_date,
                models.Session.status == "scheduled",
                models.Session.id != sess.id,
            )
            .all()
        )
        to_cancel.extend(future_siblings)

        # Stop horizon extension beyond pivot
        group = db.get(models.RecurrenceGroup, group_id)
        if group and group.horizon_through > pivot_date:
            group.horizon_through = pivot_date

    cancelled = []
    for s in to_cancel:
        if s.status == "scheduled":
            s.status = "cancelled"
            if s.purchase_id:
                purchase = db.get(models.Purchase, s.purchase_id)
                if purchase:
                    purchase.sessions_remaining += 1
            cancelled.append(s)

    db.commit()
    return cancelled


def reschedule_session(
    db: Session,
    sess: models.Session,
    new_date: datetime,
    scope: str,  # "this" or "future"
) -> list[models.Session]:
    """
    Reschedule a session to new_date.
    scope='this': update only this session; detach from recurrence group.
    scope='future': apply the same delta to this and all future sessions in the group.
    Returns list of rescheduled sessions.
    """
    if scope not in ("this", "future"):
        raise ValueError("scope must be 'this' or 'future'")

    if scope == "this":
        sess.session_date = new_date
        sess.recurrence_group_id = None   # detach from group
        db.commit()
        return [sess]

    # scope == "future"
    delta = new_date - sess.session_date
    rescheduled = [sess]
    sess.session_date = new_date

    if sess.recurrence_group_id:
        group_id = sess.recurrence_group_id
        pivot_date = sess.session_date - delta  # original date before update
        future_siblings = (
            db.query(models.Session)
            .filter(
                models.Session.recurrence_group_id == group_id,
                models.Session.session_date >= pivot_date,
                models.Session.id != sess.id,
            )
            .all()
        )
        for s in future_siblings:
            s.session_date = s.session_date + delta
            rescheduled.append(s)

        # Shift horizon too
        group = db.get(models.RecurrenceGroup, group_id)
        if group:
            group.horizon_through = group.horizon_through + delta

    db.commit()
    return rescheduled
```

- [ ] **Step 4: Run all calendar CRUD tests**

```bash
pytest gym_tracker/tests/test_calendar_crud.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add gym_tracker/calendar_crud.py gym_tracker/tests/test_calendar_crud.py
git commit -m "feat: add complete_session, cancel_session, reschedule_session to calendar_crud"
```

---

## Task 10: Calendar CRUD — get_calendar_events

**Files:**
- Modify: `gym_tracker/calendar_crud.py`
- Modify: `gym_tracker/tests/test_calendar_crud.py`

- [ ] **Step 1: Add failing test**

Append to `gym_tracker/tests/test_calendar_crud.py`:

```python
def test_get_calendar_events_trainer_sees_all(db):
    start = datetime.utcnow() - timedelta(days=1)
    end = datetime.utcnow() + timedelta(days=30)
    events = calendar_crud.get_calendar_events(
        db, start=start, end=end, viewer_role="trainer", viewer_user_id=db._trainer_user_id
    )
    assert len(events) >= 1
    # Trainer view titles include client name context
    for e in events:
        assert "Session #" in e["title"]


def test_get_calendar_events_client_sees_own_only(db):
    start = datetime.utcnow() - timedelta(days=1)
    end = datetime.utcnow() + timedelta(days=30)
    events = calendar_crud.get_calendar_events(
        db, start=start, end=end, viewer_role="client", viewer_user_id=db._client_user_id
    )
    for e in events:
        assert e["extendedProps"]["client_user_id"] == db._client_user_id
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest gym_tracker/tests/test_calendar_crud.py::test_get_calendar_events_trainer_sees_all -v
```

Expected: `AttributeError: ... has no attribute 'get_calendar_events'`

- [ ] **Step 3: Add get_calendar_events to calendar_crud.py**

Append to `gym_tracker/calendar_crud.py`:

```python
def _session_color(sess: models.Session) -> str:
    if sess.status == "scheduled" and sess.purchase_id is None:
        return NO_PACKAGE_COLOR
    return STATUS_COLORS.get(sess.status, "#0d6efd")


def _session_to_event(sess: models.Session, viewer_role: str) -> dict:
    trainer_name = (
        sess.trainer_rel.name if sess.trainer_rel else (sess.trainer or "Unknown")
    )
    client_name = (
        (sess.client_user.full_name or sess.client_user.email)
        if sess.client_user
        else "Unknown"
    )

    if viewer_role in ("trainer", "admin"):
        title = f"Session #{sess.id} \u00b7 {client_name}"
    else:
        title = f"Session #{sess.id} \u00b7 {trainer_name}"

    return {
        "id": str(sess.id),
        "title": title,
        "start": sess.session_date.isoformat(),
        "end": (sess.session_date + timedelta(minutes=sess.duration_minutes)).isoformat(),
        "color": _session_color(sess),
        "extendedProps": {
            "status": sess.status,
            "purchase_id": sess.purchase_id,
            "recurrence_group_id": sess.recurrence_group_id,
            "notes": sess.notes,
            "client_name": client_name,
            "trainer_name": trainer_name,
            "client_user_id": sess.client_user_id,
        },
    }


def get_calendar_events(
    db: Session,
    *,
    start: datetime,
    end: datetime,
    viewer_role: str,
    viewer_user_id: int,
) -> list[dict]:
    """
    Return FullCalendar-compatible event dicts for the given date range.
    Trainers/admins see all sessions. Clients see only their own.
    Also triggers horizon extension for any recurrence groups needing it.
    """
    # Extend horizons lazily
    groups_needing_extension = (
        db.query(models.RecurrenceGroup)
        .filter(
            models.RecurrenceGroup.horizon_through
            < datetime.utcnow() + relativedelta(months=HORIZON_MONTHS)
        )
        .all()
    )
    for group in groups_needing_extension:
        extend_horizon(db, group)

    q = db.query(models.Session).filter(
        models.Session.session_date >= start,
        models.Session.session_date <= end,
    )
    if viewer_role not in ("trainer", "admin"):
        q = q.filter(models.Session.client_user_id == viewer_user_id)

    sessions = q.order_by(models.Session.session_date).all()
    return [_session_to_event(s, viewer_role) for s in sessions]
```

- [ ] **Step 4: Run all calendar CRUD tests**

```bash
pytest gym_tracker/tests/test_calendar_crud.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add gym_tracker/calendar_crud.py gym_tracker/tests/test_calendar_crud.py
git commit -m "feat: add get_calendar_events with role-based filtering and lazy horizon extension"
```

---

## Task 11: Add new schemas

**Files:**
- Modify: `gym_tracker/schemas.py`

- [ ] **Step 1: Update Session schema and add new schemas**

Open `gym_tracker/schemas.py`. Make two changes:

**Change 1:** Update the `Session` response schema — `purchase_id` is now nullable:

```python
class Session(SessionBase):
    id: int
    purchase_id: int | None = None   # was: int
    purchase_exhausted: bool = False
    partner_email: str | None = None
    partner_name: str | None = None
    num_people: int = 1
    is_owner: bool = True
    model_config = {"from_attributes": True}
```

**Change 2:** Append these new schemas at the end of the file:

```python
# --------------------
# Calendar / Scheduling Schemas
# --------------------

class ScheduleSessionRequest(BaseModel):
    trainer_id: int
    client_user_id: int
    session_date: datetime
    duration_minutes: int
    purchase_id: int | None = None
    recurring: bool = False
    frequency: str | None = None   # "weekly" | "biweekly" | "monthly"
    notes: str | None = None

    @field_validator("frequency")
    @classmethod
    def validate_frequency(cls, v):
        if v is not None and v not in ("weekly", "biweekly", "monthly"):
            raise ValueError("frequency must be weekly, biweekly, or monthly")
        return v


class RescheduleRequest(BaseModel):
    new_date: datetime
    scope: str  # "this" | "future"

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, v):
        if v not in ("this", "future"):
            raise ValueError("scope must be 'this' or 'future'")
        return v


class CancelRequest(BaseModel):
    scope: str  # "this" | "future"

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, v):
        if v not in ("this", "future"):
            raise ValueError("scope must be 'this' or 'future'")
        return v


# --------------------
# Invite Schemas
# --------------------

class InviteCreate(BaseModel):
    email: str
    role: str
    trainer_id: int | None = None

    @field_validator("role")
    @classmethod
    def validate_role(cls, v):
        if v not in ("client", "trainer", "admin"):
            raise ValueError("role must be client, trainer, or admin")
        return v

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v):
        return v.strip().lower()


class InviteOut(BaseModel):
    id: int
    email: str
    role: str
    trainer_id: int | None = None
    accepted_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
```

- [ ] **Step 2: Verify schemas import cleanly**

```bash
python -c "from gym_tracker import schemas; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add gym_tracker/schemas.py
git commit -m "feat: add ScheduleSessionRequest, RescheduleRequest, CancelRequest, InviteCreate, InviteOut schemas"
```

---

## Task 12: Add routes to main.py

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Add imports at the top of main.py**

Add to the imports block in `main.py`:

```python
from datetime import time as time_type   # avoid collision with datetime.time

from gym_tracker import calendar_crud, invite_crud
```

- [ ] **Step 2: Add require_trainer dependency**

Add this function directly after `require_admin` in `main.py`:

```python
def require_trainer(request: Request, db: Session = Depends(get_db)) -> models.User:
    """Dependency that requires trainer or admin role."""
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if user.role not in ("trainer", "admin"):
        raise HTTPException(status_code=403, detail="Trainer access required")
    return user
```

- [ ] **Step 3: Add calendar page route**

Append to `main.py`:

```python
# -------------------------------------------------------------
# Calendar
# -------------------------------------------------------------
@app.get("/calendar", response_class=HTMLResponse)
def calendar_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    trainers = crud.get_trainers(db, active_only=True)
    # Clients: only their own purchases. Trainers/admins: need client list too.
    clients = []
    if user and user.role in ("trainer", "admin"):
        clients = db.query(models.User).filter(
            models.User.role == "client",
            models.User.is_active == True,
        ).order_by(models.User.full_name).all()
    return templates.TemplateResponse("calendar.html", {
        "request": request,
        "current_user": user,
        "trainers": trainers,
        "clients": clients,
        "is_trainer": user and user.role in ("trainer", "admin"),
    })


@app.get("/api/calendar/events")
def calendar_events(
    request: Request,
    start: str = Query(...),
    end: str = Query(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    start_dt = datetime.fromisoformat(start.replace("Z", "+00:00")).replace(tzinfo=None)
    end_dt = datetime.fromisoformat(end.replace("Z", "+00:00")).replace(tzinfo=None)
    events = calendar_crud.get_calendar_events(
        db,
        start=start_dt,
        end=end_dt,
        viewer_role=user.role,
        viewer_user_id=user.id,
    )
    return events
```

- [ ] **Step 4: Add session scheduling routes**

Append to `main.py`:

```python
# -------------------------------------------------------------
# Session Scheduling (trainer/admin only)
# -------------------------------------------------------------
@app.post("/api/sessions/schedule")
def schedule_session(
    body: schemas.ScheduleSessionRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_trainer),
):
    try:
        if body.recurring:
            if not body.frequency:
                raise HTTPException(status_code=422, detail="frequency required for recurring sessions")
            session_time = body.session_date.time()
            sessions, group = calendar_crud.schedule_recurring(
                db,
                trainer_id=body.trainer_id,
                client_user_id=body.client_user_id,
                start_date=body.session_date.date(),
                session_time=session_time,
                duration_minutes=body.duration_minutes,
                frequency=body.frequency,
                purchase_id=body.purchase_id,
                scheduled_by_user_id=current_user.id,
                notes=body.notes,
            )
            return {"created": len(sessions), "recurrence_group_id": group.id}
        else:
            sess = calendar_crud.schedule_session(
                db,
                trainer_id=body.trainer_id,
                client_user_id=body.client_user_id,
                session_date=body.session_date,
                duration_minutes=body.duration_minutes,
                purchase_id=body.purchase_id,
                scheduled_by_user_id=current_user.id,
                notes=body.notes,
            )
            return {"id": sess.id}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/sessions/{session_id}/complete")
def complete_session(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_trainer),
):
    sess = db.get(models.Session, session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        calendar_crud.complete_session(db, sess)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True}


@app.post("/api/sessions/{session_id}/reschedule")
def reschedule_session(
    session_id: int,
    body: schemas.RescheduleRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_trainer),
):
    sess = db.get(models.Session, session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    calendar_crud.reschedule_session(db, sess, new_date=body.new_date, scope=body.scope)
    return {"ok": True}


@app.post("/api/sessions/{session_id}/cancel")
def cancel_session(
    session_id: int,
    body: schemas.CancelRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_trainer),
):
    sess = db.get(models.Session, session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    cancelled = calendar_crud.cancel_session(db, sess, scope=body.scope)
    return {"cancelled": len(cancelled)}
```

- [ ] **Step 5: Add invite management routes**

Append to `main.py`:

```python
# -------------------------------------------------------------
# Invite Management (admin only)
# -------------------------------------------------------------
@app.get("/admin/invites", response_class=HTMLResponse)
def admin_invites_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin),
):
    invites = invite_crud.list_invites(db)
    trainers = crud.get_trainers(db, active_only=False)
    return templates.TemplateResponse("admin/invites.html", {
        "request": request,
        "current_user": current_user,
        "invites": invites,
        "trainers": trainers,
    })


@app.post("/api/invites", response_model=schemas.InviteOut)
def create_invite(
    body: schemas.InviteCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin),
):
    try:
        invite = invite_crud.create_invite(
            db,
            email=body.email,
            role=body.role,
            invited_by_user_id=current_user.id,
            trainer_id=body.trainer_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return invite


@app.delete("/api/invites/{invite_id}")
def delete_invite(
    invite_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin),
):
    deleted = invite_crud.delete_invite(db, invite_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Invite not found")
    return {"ok": True}
```

- [ ] **Step 6: Verify the app loads**

```bash
python -c "import main; print('OK')"
```

Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add main.py
git commit -m "feat: add calendar, scheduling, and invite management routes"
```

---

## Task 13: calendar.html template

**Files:**
- Create: `templates/calendar.html`

- [ ] **Step 1: Create the template**

Create `templates/calendar.html`:

```html
{% extends "_nav.html" %}

{% block title %}Calendar{% endblock %}

{% block head_extra %}
<link href="https://cdn.jsdelivr.net/npm/fullcalendar@6.1.11/index.global.min.css" rel="stylesheet" />
<style>
  #calendar { max-width: 1100px; margin: 0 auto; }
  .fc-event { cursor: pointer; }
</style>
{% endblock %}

{% block content %}
<div class="container-fluid py-3">
  <h2 class="mb-3">Calendar</h2>
  <div id="calendar"></div>
</div>

<!-- Event Detail Modal -->
<div class="modal fade" id="eventModal" tabindex="-1">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title" id="eventModalTitle">Session Detail</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body" id="eventModalBody"></div>
      <div class="modal-footer" id="eventModalFooter"></div>
    </div>
  </div>
</div>

<!-- Schedule Modal (trainer only) -->
{% if is_trainer %}
<div class="modal fade" id="scheduleModal" tabindex="-1">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title">Schedule Session</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <form id="scheduleForm">
          <div class="mb-3">
            <label class="form-label">Client</label>
            <select class="form-select" name="client_user_id" required>
              <option value="">— select client —</option>
              {% for c in clients %}
              <option value="{{ c.id }}">{{ c.full_name or c.email }}</option>
              {% endfor %}
            </select>
          </div>
          <div class="mb-3">
            <label class="form-label">Trainer</label>
            <select class="form-select" name="trainer_id" required>
              <option value="">— select trainer —</option>
              {% for t in trainers %}
              <option value="{{ t.id }}">{{ t.name }}</option>
              {% endfor %}
            </select>
          </div>
          <div class="mb-3">
            <label class="form-label">Date & Time</label>
            <input type="datetime-local" class="form-control" name="session_date" id="scheduleDateInput" required />
          </div>
          <div class="mb-3">
            <label class="form-label">Duration</label>
            <select class="form-select" name="duration_minutes" required>
              <option value="30">30 min</option>
              <option value="45">45 min</option>
              <option value="60" selected>60 min</option>
              <option value="90">90 min</option>
            </select>
          </div>
          <div class="mb-3">
            <label class="form-label">Package</label>
            <select class="form-select" name="purchase_id">
              <option value="">No package</option>
            </select>
            <small class="text-muted" id="packageHint">Select a client first to load packages.</small>
          </div>
          <div class="mb-3 form-check">
            <input type="checkbox" class="form-check-input" id="recurringCheck" name="recurring" />
            <label class="form-check-label" for="recurringCheck">Recurring</label>
          </div>
          <div class="mb-3 d-none" id="frequencyGroup">
            <label class="form-label">Frequency</label>
            <select class="form-select" name="frequency">
              <option value="weekly">Weekly</option>
              <option value="biweekly">Bi-weekly</option>
              <option value="monthly">Monthly</option>
            </select>
          </div>
          <div class="mb-3">
            <label class="form-label">Notes (optional)</label>
            <textarea class="form-control" name="notes" rows="2"></textarea>
          </div>
        </form>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
        <button type="button" class="btn btn-primary" id="scheduleSubmit">Schedule</button>
      </div>
    </div>
  </div>
</div>

<!-- Recurrence scope modal (reschedule / cancel) -->
<div class="modal fade" id="scopeModal" tabindex="-1">
  <div class="modal-dialog modal-sm">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title" id="scopeModalTitle">Recurring Session</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <p id="scopeModalMessage"></p>
      </div>
      <div class="modal-footer d-flex flex-column gap-2">
        <button type="button" class="btn btn-outline-primary w-100" id="scopeThis">Just this session</button>
        <button type="button" class="btn btn-primary w-100" id="scopeFuture">This and all future sessions</button>
      </div>
    </div>
  </div>
</div>
{% endif %}

<script src="https://cdn.jsdelivr.net/npm/fullcalendar@6.1.11/index.global.min.js"></script>
<script>
const IS_TRAINER = {{ 'true' if is_trainer else 'false' }};

document.addEventListener('DOMContentLoaded', function () {
  const calEl = document.getElementById('calendar');
  const calendar = new FullCalendar.Calendar(calEl, {
    initialView: 'timeGridWeek',
    headerToolbar: {
      left: 'prev,next today',
      center: 'title',
      right: 'dayGridMonth,timeGridWeek,timeGridDay,listWeek'
    },
    editable: IS_TRAINER,
    events: { url: '/api/calendar/events', method: 'GET' },
    dateClick: function(info) {
      if (IS_TRAINER) openScheduleModal(info.dateStr);
    },
    eventClick: function(info) {
      openEventModal(info.event);
    },
    eventDrop: function(info) {
      if (IS_TRAINER) handleDrop(info);
    }
  });
  calendar.render();

  // ---- Schedule modal ----
  const scheduleModal = IS_TRAINER ? new bootstrap.Modal(document.getElementById('scheduleModal')) : null;
  const recurringCheck = IS_TRAINER ? document.getElementById('recurringCheck') : null;
  if (recurringCheck) {
    recurringCheck.addEventListener('change', function() {
      document.getElementById('frequencyGroup').classList.toggle('d-none', !this.checked);
    });
  }

  function openScheduleModal(dateStr) {
    const input = document.getElementById('scheduleDateInput');
    input.value = dateStr.length > 10 ? dateStr.substring(0, 16) : dateStr + 'T09:00';
    scheduleModal.show();
  }

  if (IS_TRAINER) {
    document.getElementById('scheduleSubmit').addEventListener('click', async function() {
      const form = document.getElementById('scheduleForm');
      const data = Object.fromEntries(new FormData(form));
      data.duration_minutes = parseInt(data.duration_minutes);
      data.client_user_id = parseInt(data.client_user_id);
      data.trainer_id = parseInt(data.trainer_id);
      data.purchase_id = data.purchase_id ? parseInt(data.purchase_id) : null;
      data.recurring = !!data.recurring;
      if (!data.recurring) delete data.frequency;

      const res = await fetch('/api/sessions/schedule', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (res.ok) {
        scheduleModal.hide();
        calendar.refetchEvents();
      } else {
        const err = await res.json();
        alert('Error: ' + (err.detail || 'Unknown error'));
      }
    });
  }

  // ---- Event detail modal ----
  const eventModal = new bootstrap.Modal(document.getElementById('eventModal'));

  function openEventModal(event) {
    const p = event.extendedProps;
    document.getElementById('eventModalTitle').textContent = event.title;
    document.getElementById('eventModalBody').innerHTML = `
      <p><strong>Status:</strong> ${p.status}</p>
      <p><strong>Client:</strong> ${p.client_name}</p>
      <p><strong>Trainer:</strong> ${p.trainer_name}</p>
      <p><strong>Start:</strong> ${event.start.toLocaleString()}</p>
      ${p.notes ? '<p><strong>Notes:</strong> ' + p.notes + '</p>' : ''}
      ${!p.purchase_id ? '<p class="text-warning">No package linked</p>' : ''}
    `;
    const footer = document.getElementById('eventModalFooter');
    footer.innerHTML = '';
    if (IS_TRAINER && p.status === 'scheduled') {
      footer.innerHTML += `<button class="btn btn-success" onclick="doComplete(${event.id})">Mark Complete</button> `;
      footer.innerHTML += `<button class="btn btn-warning" onclick="doReschedule(${event.id}, ${JSON.stringify(p.recurrence_group_id)})">Reschedule</button> `;
      footer.innerHTML += `<button class="btn btn-danger" onclick="doCancel(${event.id}, ${JSON.stringify(p.recurrence_group_id)})">Cancel</button>`;
    }
    eventModal.show();
  }

  // ---- Complete ----
  window.doComplete = async function(sessionId) {
    const res = await fetch(`/api/sessions/${sessionId}/complete`, { method: 'POST' });
    if (res.ok) { eventModal.hide(); calendar.refetchEvents(); }
    else { const e = await res.json(); alert(e.detail); }
  };

  // ---- Scope modal helper ----
  const scopeModal = IS_TRAINER ? new bootstrap.Modal(document.getElementById('scopeModal')) : null;
  function withScope(title, message, callback) {
    document.getElementById('scopeModalTitle').textContent = title;
    document.getElementById('scopeModalMessage').textContent = message;
    document.getElementById('scopeThis').onclick = () => { scopeModal.hide(); callback('this'); };
    document.getElementById('scopeFuture').onclick = () => { scopeModal.hide(); callback('future'); };
    scopeModal.show();
  }

  // ---- Cancel ----
  window.doCancel = function(sessionId, groupId) {
    eventModal.hide();
    if (groupId) {
      withScope('Cancel Recurring Session', 'Cancel just this session, or this and all future sessions?', async (scope) => {
        await fetch(`/api/sessions/${sessionId}/cancel`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ scope }),
        });
        calendar.refetchEvents();
      });
    } else {
      fetch(`/api/sessions/${sessionId}/cancel`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scope: 'this' }),
      }).then(() => calendar.refetchEvents());
    }
  };

  // ---- Reschedule (drag or button) ----
  window.doReschedule = function(sessionId, groupId) {
    eventModal.hide();
    const newDateStr = prompt('New date/time (YYYY-MM-DDTHH:MM):');
    if (!newDateStr) return;
    const performReschedule = async (scope) => {
      await fetch(`/api/sessions/${sessionId}/reschedule`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_date: newDateStr, scope }),
      });
      calendar.refetchEvents();
    };
    if (groupId) {
      withScope('Reschedule Recurring Session', 'Move just this session, or this and all future sessions?', performReschedule);
    } else {
      performReschedule('this');
    }
  };

  async function handleDrop(info) {
    const sessionId = info.event.id;
    const groupId = info.event.extendedProps.recurrence_group_id;
    const newDate = info.event.start.toISOString();
    const performReschedule = async (scope) => {
      const res = await fetch(`/api/sessions/${sessionId}/reschedule`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_date: newDate, scope }),
      });
      if (!res.ok) { info.revert(); }
      else { calendar.refetchEvents(); }
    };
    if (groupId) {
      withScope('Reschedule Recurring Session', 'Move just this session, or this and all future sessions?', performReschedule);
    } else {
      performReschedule('this');
    }
  }
});
</script>
{% endblock %}
```

Note: This template assumes `_nav.html` uses `{% block title %}`, `{% block head_extra %}`, and `{% block content %}`. Check `templates/_nav.html` for the actual block names and adjust if they differ.

- [ ] **Step 2: Commit**

```bash
git add templates/calendar.html
git commit -m "feat: add calendar.html with FullCalendar Standard, schedule/cancel/reschedule modals"
```

---

## Task 14: admin/invites.html template

**Files:**
- Create: `templates/admin/invites.html`

- [ ] **Step 1: Create the template**

Create `templates/admin/invites.html`:

```html
{% extends "_nav.html" %}

{% block title %}Manage Invites{% endblock %}

{% block content %}
<div class="container py-4">
  <div class="d-flex justify-content-between align-items-center mb-3">
    <h2>Invites</h2>
    <button class="btn btn-primary" data-bs-toggle="modal" data-bs-target="#addInviteModal">
      + Add Invite
    </button>
  </div>

  <table class="table table-striped">
    <thead>
      <tr>
        <th>Email</th>
        <th>Role</th>
        <th>Trainer</th>
        <th>Accepted</th>
        <th>Invited</th>
        <th></th>
      </tr>
    </thead>
    <tbody>
      {% for invite in invites %}
      <tr>
        <td>{{ invite.email }}</td>
        <td><span class="badge bg-secondary">{{ invite.role }}</span></td>
        <td>{{ invite.trainer.name if invite.trainer else '—' }}</td>
        <td>
          {% if invite.accepted_at %}
          <span class="text-success">✓ {{ invite.accepted_at.strftime('%Y-%m-%d') }}</span>
          {% else %}
          <span class="text-muted">Pending</span>
          {% endif %}
        </td>
        <td>{{ invite.created_at.strftime('%Y-%m-%d') }}</td>
        <td>
          <button class="btn btn-sm btn-outline-danger"
            onclick="deleteInvite({{ invite.id }}, '{{ invite.email }}')">
            Revoke
          </button>
        </td>
      </tr>
      {% else %}
      <tr><td colspan="6" class="text-center text-muted">No invites yet.</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>

<!-- Add Invite Modal -->
<div class="modal fade" id="addInviteModal" tabindex="-1">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title">Add Invite</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <form id="inviteForm">
          <div class="mb-3">
            <label class="form-label">Email</label>
            <input type="email" class="form-control" name="email" required />
          </div>
          <div class="mb-3">
            <label class="form-label">Role</label>
            <select class="form-select" name="role" required>
              <option value="client">Client</option>
              <option value="trainer">Trainer</option>
              <option value="admin">Admin</option>
            </select>
          </div>
          <div class="mb-3">
            <label class="form-label">Link to Trainer (optional)</label>
            <select class="form-select" name="trainer_id">
              <option value="">— none —</option>
              {% for t in trainers %}
              <option value="{{ t.id }}">{{ t.name }}</option>
              {% endfor %}
            </select>
          </div>
        </form>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
        <button type="button" class="btn btn-primary" id="inviteSubmit">Send Invite</button>
      </div>
    </div>
  </div>
</div>

<script>
document.getElementById('inviteSubmit').addEventListener('click', async function() {
  const form = document.getElementById('inviteForm');
  const data = Object.fromEntries(new FormData(form));
  data.trainer_id = data.trainer_id ? parseInt(data.trainer_id) : null;

  const res = await fetch('/api/invites', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (res.ok) {
    window.location.reload();
  } else {
    const err = await res.json();
    alert('Error: ' + (err.detail || 'Unknown'));
  }
});

async function deleteInvite(id, email) {
  if (!confirm(`Revoke invite for ${email}?`)) return;
  const res = await fetch(`/api/invites/${id}`, { method: 'DELETE' });
  if (res.ok) window.location.reload();
}
</script>
{% endblock %}
```

- [ ] **Step 2: Commit**

```bash
git add templates/admin/invites.html
git commit -m "feat: add admin/invites.html template"
```

---

## Task 15: Update _nav.html

**Files:**
- Modify: `templates/_nav.html`

- [ ] **Step 1: Read the current nav file**

Open `templates/_nav.html` and find the navigation links section (the list of `<a>` or `<li>` items pointing to `/`, `/history`, `/reports`, `/admin`, etc.).

- [ ] **Step 2: Add the Calendar link**

Add a Calendar link in the same position as the other main nav items (after Dashboard / before History, or wherever makes most sense in the existing order). For example, if the existing items look like:

```html
<a class="nav-link" href="/">Dashboard</a>
<a class="nav-link" href="/history">History</a>
```

Add after Dashboard:

```html
<a class="nav-link" href="/calendar">Calendar</a>
```

- [ ] **Step 3: Add the Invites admin link**

In the admin-only section of the nav (look for existing `/admin` links), add:

```html
<a class="nav-link" href="/admin/invites">Invites</a>
```

Show it only when `current_user.role == 'admin'` — follow the pattern of the existing admin guard in the nav template.

- [ ] **Step 4: Run the full test suite**

```bash
pytest gym_tracker/tests/ -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/_nav.html
git commit -m "feat: add Calendar and Invites nav links"
```

---

## Self-Review Notes

### Spec coverage check

| Spec requirement | Covered by |
|---|---|
| Calendar page with FullCalendar Standard (MIT) | Task 13 |
| Session status lifecycle (scheduled/completed/cancelled) | Tasks 7, 9 |
| Credit reservation on schedule | Task 7 |
| Credit refund on cancel | Task 9 |
| Recurring sessions + 3-month rolling horizon | Task 8 |
| Reschedule with this/future scope | Task 9 |
| Cancel with this/future scope | Task 9 |
| Trainer role + auto-linking | Task 5 |
| Invitation-only signup (replaces ALLOWED_EMAILS) | Tasks 4, 5 |
| Package-less session scheduling | Task 7 |
| Trainer sees all sessions; client sees own | Task 10 |
| Admin invite management page | Tasks 4, 12, 14 |
| require_trainer dependency | Task 12 |
| Alembic migration with backfills | Task 3 |
| Fix nullable purchase_id in crud.py | Task 6 |
| _nav.html Calendar + Invites links | Task 15 |

All spec sections covered.

### Type consistency check

- `schedule_session` defined in Task 7, called in Tasks 8, 9, 10 — signature consistent throughout
- `get_calendar_events` defined in Task 10, route in Task 12 — args match
- `cancel_session(db, sess, scope="this"|"future")` — consistent in tests (Task 9) and route (Task 12)
- `reschedule_session(db, sess, new_date, scope)` — consistent in tests and route
- `InviteOut` schema used in `POST /api/invites` route (Task 12) — defined in Task 11
