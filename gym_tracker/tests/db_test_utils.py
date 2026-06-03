"""Shared test database engine and session factory.

Imported by both conftest.py (fixtures) and test modules (test-body DB access).
Using a single module avoids the double-import problem that arises when pytest
loads conftest.py as a plugin under a different sys.modules key than a direct
`from gym_tracker.tests.conftest import ...` import.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
