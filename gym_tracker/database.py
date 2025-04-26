from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker
import os

# Use environment variable or default URL
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "mysql+pymysql://gym:abc123@localhost:3306/gym_tracker"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()