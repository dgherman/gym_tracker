import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from gym_tracker.database import Base
from gym_tracker import crud, schemas

# Setup in-memory SQLite for testing
test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

@pytest.fixture(scope="module")
def db():
    Base.metadata.create_all(bind=test_engine)
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_create_and_get_purchases(db):
    purchase_in = schemas.PurchaseCreate(duration_minutes=30)
    purchase = crud.create_purchase(db, purchase_in)
    assert purchase.id == 1
    assert purchase.duration_minutes == 30
    assert purchase.total_sessions == 10
    assert purchase.sessions_remaining == 10

    purchases = crud.get_purchases(db)
    assert len(purchases) == 1


def test_create_and_get_sessions(db):
    session = crud.create_session(db, duration_minutes=30, trainer="Rachel")
    assert session.id == 1
    assert session.duration_minutes == 30
    assert session.purchase_id == 1
    assert session.trainer == "Rachel"
    assert session.purchase_exhausted is False

    sessions = crud.get_sessions(db)
    assert len(sessions) == 1
    assert sessions[0].trainer == "Rachel"


def test_create_with_custom_trainer(db):
    session = crud.create_session(db, duration_minutes=30, trainer="Lindsay")
    assert session.trainer == "Lindsay"


def test_exhaustion_flag(db):
    # exhaust remaining sessions with Rachel
    last = None
    for _ in range(8):
        last = crud.create_session(db, duration_minutes=30, trainer="Rachel")
    assert last.purchase_exhausted is True
    with pytest.raises(ValueError):
        crud.create_session(db, duration_minutes=30, trainer="Rachel")