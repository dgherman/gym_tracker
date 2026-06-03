import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from gym_tracker.database import Base
from gym_tracker import models

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture
def couples(client_factory):
    return client_factory(num_people=2, with_partner=True)


@pytest.fixture
def client_factory():
    created = {}

    def _make(num_people=2, with_partner=True):
        Base.metadata.create_all(bind=test_engine)
        db = TestSessionLocal()
        cat = models.ActivityCategory(name="Strength", slug="strength", sort_order=1)
        db.add(cat); db.flush()
        db.add(models.CategoryField(category_id=cat.id, key="reps", label="Reps",
                                    field_type="integer", is_required=True, sort_order=1))
        act = models.Activity(category_id=cat.id, name="Bench Press"); db.add(act)
        owner = models.User(google_sub="owner-sub", email="owner@x.com", role="client")
        partner = models.User(google_sub="partner-sub", email="partner@x.com", role="client")
        outsider = models.User(google_sub="out-sub", email="out@x.com", role="client")
        db.add_all([owner, partner, outsider]); db.flush()
        pur = models.Purchase(duration_minutes=30, total_sessions=10, sessions_remaining=10,
                              num_people=num_people, logged_by_user_id=owner.id,
                              partner_user_id=partner.id if with_partner else None)
        db.add(pur); db.flush()
        sess = models.Session(purchase_id=pur.id, duration_minutes=30, trainer="Alex",
                              session_date=__import__("datetime").datetime(2026, 5, 1, 10, 0),
                              created_by_user_id=owner.id)
        db.add(sess); db.commit()
        ids = {"cat": cat.id, "act": act.id, "owner": owner.id, "partner": partner.id,
               "outsider": outsider.id, "purchase": pur.id, "session": sess.id}
        db.close()

        def override_get_db():
            d = TestSessionLocal()
            try:
                yield d
            finally:
                d.close()

        main.app.dependency_overrides[main.get_db] = override_get_db
        c = TestClient(main.app)
        c._ids = ids
        created["c"] = c
        return c

    yield _make
    main.app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=test_engine)


def test_session_activity_has_person_slot_column():
    assert hasattr(models.SessionActivity, "person_slot")
    sa = models.SessionActivity(session_id=1, activity_id=1, values={}, person_slot=2)
    assert sa.person_slot == 2
