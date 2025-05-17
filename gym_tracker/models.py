from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Float
from sqlalchemy.orm import relationship
from .database import Base

class Purchase(Base):
    __tablename__ = "purchases"

    id = Column(Integer, primary_key=True, index=True)
    duration_minutes = Column(Integer, index=True)
    total_sessions = Column(Integer)
    sessions_remaining = Column(Integer)
    purchase_date = Column(DateTime, index=True)
    cost = Column(Float, default=0.0)       # ← NEW field

    sessions = relationship("Session", back_populates="purchase")


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    purchase_id = Column(Integer, ForeignKey("purchases.id"))
    session_date = Column(DateTime, index=True)
    duration_minutes = Column(Integer)
    trainer = Column(String, index=True)

    purchase = relationship("Purchase", back_populates="sessions")
