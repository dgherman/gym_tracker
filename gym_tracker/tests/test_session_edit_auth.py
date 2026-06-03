from gym_tracker import crud, models


class _S:  # lightweight stand-ins (helpers are pure)
    def __init__(self, created_by, partner=None):
        self.created_by_user_id = created_by
        self.partner_user_id = partner


class _P:
    def __init__(self, owner, partner=None):
        self.logged_by_user_id = owner
        self.partner_user_id = partner


def test_participant_ids_includes_owner_and_partner():
    ids = crud.session_participant_ids(_S(created_by=1), _P(owner=1, partner=2))
    assert ids == {1, 2}


def test_owner_can_edit():
    assert crud.user_can_edit_session(_S(1), _P(1, 2), 1) is True


def test_partner_can_edit():
    assert crud.user_can_edit_session(_S(1), _P(1, 2), 2) is True


def test_outsider_cannot_edit():
    assert crud.user_can_edit_session(_S(1), _P(1, 2), 99) is False


def test_session_partner_override_counts():
    assert crud.user_can_edit_session(_S(1, partner=5), _P(1, None), 5) is True
