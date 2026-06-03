"""set unit=min on duration fields

Revision ID: 8caa7497563a
Revises: b48be0474e99
Create Date: 2026-06-03 14:50:48.559751

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8caa7497563a'
down_revision: Union[str, Sequence[str], None] = 'b48be0474e99'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Label duration fields with the 'min' unit so the logging form hints
    minutes. Idempotent: only touches duration fields with no unit set."""
    op.execute(
        "UPDATE category_fields SET unit = 'min' "
        "WHERE field_type = 'duration' AND (unit IS NULL OR unit = '')"
    )


def downgrade() -> None:
    """Best-effort revert: clear the unit only on duration fields we set."""
    op.execute(
        "UPDATE category_fields SET unit = NULL "
        "WHERE field_type = 'duration' AND unit = 'min'"
    )
