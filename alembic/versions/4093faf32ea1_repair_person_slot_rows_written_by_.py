"""repair person_slot rows written by partner creators

Data-only migration, no schema change.

Before the requester-relative slot fix (gym_tracker/activities.py:
relative_to_stored_slot), the frontend sent person_slot relative to the
logged-in user (1=me, 2=the other person) while the backend stored and
displayed it as absolute (1=purchase owner, 2=partner). Rows created or
edited by the PARTNER of the purchase were therefore stored inverted.

This swaps slots 1<->2 on activity rows of couples sessions whose creator
is not the purchase owner — exactly the rows written inverted. Run once,
together with deploying the code fix; running it against rows written
after the fix would corrupt them.

Revision ID: 4093faf32ea1
Revises: 8caa7497563a
Create Date: 2026-06-11 10:39:34.050769

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4093faf32ea1'
down_revision: Union[str, Sequence[str], None] = '8caa7497563a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SWAP_SQL = """
    UPDATE session_activities sa
    JOIN sessions s ON s.id = sa.session_id
    JOIN purchases p ON p.id = s.purchase_id
    SET sa.person_slot = 3 - sa.person_slot
    WHERE p.num_people > 1
      AND sa.person_slot IN (1, 2)
      AND s.created_by_user_id IS NOT NULL
      AND p.logged_by_user_id IS NOT NULL
      AND s.created_by_user_id <> p.logged_by_user_id
"""


def upgrade() -> None:
    op.execute(_SWAP_SQL)


def downgrade() -> None:
    # The swap is its own inverse.
    op.execute(_SWAP_SQL)
