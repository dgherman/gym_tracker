from datetime import datetime

from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    Float,
    Boolean,
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
    logged_purchases = relationship("Purchase", back_populates="logged_by_user")
    created_sessions = relationship("Session", back_populates="created_by_user")

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

    # NEW: who logged this purchase (nullable for legacy rows)
    logged_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    logged_by_user = relationship("User", back_populates="logged_purchases")

    sessions = relationship("Session", back_populates="purchase")


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    purchase_id = Column(Integer, ForeignKey("purchases.id"))
    session_date = Column(DateTime, index=True)
    duration_minutes = Column(Integer)
    trainer = Column(String(255), index=True)

    # NEW: who created/recorded this session (nullable for legacy rows)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_by_user = relationship("User", back_populates="created_sessions")

    purchase = relationship("Purchase", back_populates="sessions")
