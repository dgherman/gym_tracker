import pytest
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from gym_tracker.database import Base
from gym_tracker import models
from gym_tracker import invite_crud

test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="module")
def db():
    Base.metadata.create_all(bind=test_engine)
    session = TestSessionLocal()
    # Seed a user to act as inviter
    admin = models.User(
        google_sub="sub-admin",
        email="admin@gym.com",
        email_verified=True,
        role="admin",
        is_active=True,
        created_at=datetime.utcnow(),
        last_login_at=datetime.utcnow(),
    )
    session.add(admin)
    session.commit()
    session.refresh(admin)
    session._test_admin_id = admin.id
    try:
        yield session
    finally:
        session.close()


def test_create_invite(db):
    invite = invite_crud.create_invite(db, email="alice@gym.com", role="client", invited_by_user_id=db._test_admin_id)
    assert invite.id is not None
    assert invite.email == "alice@gym.com"
    assert invite.role == "client"
    assert invite.accepted_at is None


def test_get_invite_by_email_found(db):
    result = invite_crud.get_invite_by_email(db, "alice@gym.com")
    assert result is not None
    assert result.email == "alice@gym.com"


def test_get_invite_by_email_case_insensitive(db):
    result = invite_crud.get_invite_by_email(db, "ALICE@GYM.COM")
    assert result is not None


def test_get_invite_by_email_not_found(db):
    result = invite_crud.get_invite_by_email(db, "nobody@gym.com")
    assert result is None


def test_list_invites(db):
    invites = invite_crud.list_invites(db)
    assert len(invites) >= 1


def test_accept_invite(db):
    invite = invite_crud.get_invite_by_email(db, "alice@gym.com")
    invite_crud.accept_invite(db, invite)
    assert invite.accepted_at is not None


def test_delete_invite(db):
    invite = invite_crud.create_invite(db, email="bob@gym.com", role="client", invited_by_user_id=db._test_admin_id)
    invite_crud.delete_invite(db, invite.id)
    assert invite_crud.get_invite_by_email(db, "bob@gym.com") is None
