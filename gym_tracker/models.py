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


class AppSetting(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False, index=True)
    value = Column(String(255), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<AppSetting key={self.key!r} value={self.value!r}>"
