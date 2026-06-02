import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from gym_tracker.database import Base
from gym_tracker import models

# StaticPool ensures all connections (create_all + sessions) share the same
# in-memory SQLite database — required for :memory: to be visible across
# separate session/connection objects.
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture
def client():
    Base.metadata.create_all(bind=test_engine)
    db = TestSessionLocal()
    # Seed category/field/activity + an admin and a client user + a purchase
    cat = models.ActivityCategory(name="Strength", slug="strength", sort_order=1)
    db.add(cat); db.flush()
    db.add(models.CategoryField(category_id=cat.id, key="reps", label="Reps",
                                field_type="integer", is_required=True, sort_order=1))
    act = models.Activity(category_id=cat.id, name="Bench Press"); db.add(act)
    admin = models.User(google_sub="admin-sub", email="a@x.com", role="admin")
    user = models.User(google_sub="user-sub", email="u@x.com", role="client")
    db.add_all([admin, user]); db.flush()
    db.add(models.Purchase(duration_minutes=30, total_sessions=10, sessions_remaining=10,
                           logged_by_user_id=user.id))
    db.commit()
    ids = {"cat": cat.id, "act": act.id, "admin": admin.id, "user": user.id}
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
    try:
        yield c
    finally:
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=test_engine)


def test_list_categories_includes_fields(client):
    r = client.get("/api/categories", headers={"accept": "application/json"})
    assert r.status_code == 200
    cats = r.json()
    assert cats[0]["name"] == "Strength"
    assert any(f["key"] == "reps" for f in cats[0]["fields"])


def test_list_activities_filtered(client):
    cat_id = client._ids["cat"]
    r = client.get(f"/api/activities?category_id={cat_id}", headers={"accept": "application/json"})
    assert r.status_code == 200
    assert any(a["name"] == "Bench Press" for a in r.json())


def test_admin_endpoints_require_admin(client):
    # No session user -> require_admin raises 401
    r = client.post("/api/admin/categories", json={"name": "Yoga"},
                    headers={"accept": "application/json"})
    assert r.status_code in (401, 403)


def test_admin_create_category_and_field(client):
    admin_id = client._ids["admin"]

    def fake_admin():
        d = TestSessionLocal()
        try:
            return d.get(models.User, admin_id)
        finally:
            d.close()

    main.app.dependency_overrides[main.require_admin] = fake_admin
    try:
        r = client.post("/api/admin/categories", json={"name": "Yoga"},
                        headers={"accept": "application/json"})
        assert r.status_code == 200, r.text
        new_cat_id = r.json()["id"]
        rf = client.post(f"/api/admin/categories/{new_cat_id}/fields",
                         json={"key": "minutes", "label": "Minutes", "field_type": "duration"},
                         headers={"accept": "application/json"})
        assert rf.status_code == 200, rf.text
        assert rf.json()["key"] == "minutes"
    finally:
        main.app.dependency_overrides.pop(main.require_admin, None)
