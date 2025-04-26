from sqlalchemy import Column, Integer, DateTime, ForeignKey, String, text
from sqlalchemy.orm import relationship
from gym_tracker.database import Base

class Purchase(Base):
    __tablename__ = "purchases"
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    duration_minutes = Column(Integer, nullable=False)
    total_sessions = Column(Integer, default=10)
    sessions_remaining = Column(Integer, nullable=False)
    purchase_date = Column(DateTime(timezone=True), server_default=text('CURRENT_TIMESTAMP'))

    sessions = relationship("Session", back_populates="purchase")

class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=False)
    session_date = Column(DateTime(timezone=True), server_default=text('CURRENT_TIMESTAMP'))
    duration_minutes = Column(Integer, nullable=False)
    trainer = Column(String, nullable=False)

    purchase = relationship("Purchase", back_populates="sessions")