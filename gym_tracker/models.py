from datetime import datetime

from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    Float,
    Boolean,
    JSON,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .database import Base


# ─────────────────────────────────────────────────────────────
# Users (for Google login identity + future roles)
# ─────────────────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    # OIDC stable subject from Google
    google_sub = Column(String(255), unique=True, index=True, nullable=False)

    # Profile
    email = Column(String(255), index=True, nullable=True)
    email_verified = Column(Boolean, nullable=False, default=False)
    full_name = Column(String(255), nullable=True)
    avatar_url = Column(String(512), nullable=True)

    # Future RBAC hook (string for now)
    role = Column(String(50), nullable=False, default="client")
    is_active = Column(Boolean, nullable=False, default=True)

    # Audit
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Backrefs for convenience (purely optional)
    logged_purchases = relationship("Purchase", foreign_keys="[Purchase.logged_by_user_id]", back_populates="logged_by_user")
    created_sessions = relationship("Session", foreign_keys="[Session.created_by_user_id]", back_populates="created_by_user")

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} sub={self.google_sub!r}>"


# ─────────────────────────────────────────────────────────────
# Your existing domain models (unchanged fields)
# Added: nullable ownership FKs to reference users
# ─────────────────────────────────────────────────────────────
class Purchase(Base):
    __tablename__ = "purchases"

    id = Column(Integer, primary_key=True, index=True)
    duration_minutes = Column(Integer, index=True)
    total_sessions = Column(Integer)
    sessions_remaining = Column(Integer)
    purchase_date = Column(DateTime, index=True)
    cost = Column(Float, default=0.0)
    num_people = Column(Integer, nullable=False, default=1)

    # NEW: who logged this purchase (nullable for legacy rows)
    logged_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    logged_by_user = relationship("User", foreign_keys=[logged_by_user_id], back_populates="logged_purchases")

    # Partner support
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

    # Relationship to sessions
    sessions = relationship("Session", back_populates="trainer_rel")

    def __repr__(self) -> str:
        return f"<Trainer id={self.id} name={self.name!r} active={self.is_active}>"


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    purchase_id = Column(Integer, ForeignKey("purchases.id"))
    session_date = Column(DateTime, index=True)
    duration_minutes = Column(Integer)
    trainer = Column(String(255), index=True)  # Keep for backward compatibility
    trainer_id = Column(Integer, ForeignKey("trainers.id"), nullable=True)  # New FK

    # NEW: who created/recorded this session (nullable for legacy rows)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_by_user = relationship("User", foreign_keys=[created_by_user_id], back_populates="created_sessions")

    # Partner for this specific session (overrides purchase default)
    partner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    partner_user = relationship("User", foreign_keys=[partner_user_id])

    purchase = relationship("Purchase", back_populates="sessions")
    trainer_rel = relationship("Trainer", back_populates="sessions")
    activities = relationship(
        "SessionActivity",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="SessionActivity.sort_order",
    )


# ─────────────────────────────────────────────────────────────
# Package (templates for purchasing session packages)
# ─────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────
# Activity tracking
# ─────────────────────────────────────────────────────────────
class ActivityCategory(Base):
    __tablename__ = "activity_categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True)
    slug = Column(String(255), nullable=False, unique=True)
    is_active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    fields = relationship(
        "CategoryField",
        back_populates="category",
        cascade="all, delete-orphan",
        order_by="CategoryField.sort_order",
    )
    activities = relationship("Activity", back_populates="category")


class CategoryField(Base):
    __tablename__ = "category_fields"
    __table_args__ = (UniqueConstraint("category_id", "key", name="uq_category_field_key"),)

    id = Column(Integer, primary_key=True, index=True)
    category_id = Column(Integer, ForeignKey("activity_categories.id"), nullable=False)
    key = Column(String(64), nullable=False)
    label = Column(String(255), nullable=False)
    field_type = Column(String(20), nullable=False)  # integer | decimal | duration | text
    unit = Column(String(32), nullable=True)
    is_required = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    category = relationship("ActivityCategory", back_populates="fields")


class Activity(Base):
    __tablename__ = "activities"
    __table_args__ = (UniqueConstraint("category_id", "name", name="uq_activity_name_per_category"),)

    id = Column(Integer, primary_key=True, index=True)
    category_id = Column(Integer, ForeignKey("activity_categories.id"), nullable=False)
    name = Column(String(255), nullable=False, index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    category = relationship("ActivityCategory", back_populates="activities")


class SessionActivity(Base):
    __tablename__ = "session_activities"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False)
    activity_id = Column(Integer, ForeignKey("activities.id"), nullable=False)
    values = Column(JSON, nullable=False, default=dict)
    notes = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    session = relationship("Session", back_populates="activities")
    activity = relationship("Activity")
