# database.py (optional tidy-up)
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from gym_tracker.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.SQLALCHEMY_DATABASE_URL,
    pool_pre_ping=settings.DB_POOL_PRE_PING,
    # Optionally enable the pool knobs below for MySQL/Postgres (ignored by SQLite):
    # pool_size=settings.DB_POOL_SIZE,
    # max_overflow=settings.DB_MAX_OVERFLOW,
    # pool_timeout=settings.DB_POOL_TIMEOUT,
    # pool_recycle=settings.DB_POOL_RECYCLE,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
