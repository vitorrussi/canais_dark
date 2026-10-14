"""add user last_seen_version

Revision ID: 3ac2ac0a85c8
Revises: e78f5794b833
Create Date: 2026-10-14 20:50:00.034604

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '3ac2ac0a85c8'
down_revision: Union[str, Sequence[str], None] = 'e78f5794b833'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_seen_version', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''))



def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('last_seen_version')

