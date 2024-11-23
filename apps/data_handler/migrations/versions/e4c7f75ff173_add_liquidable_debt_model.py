"""add liquidable debt model

Revision ID: e4c7f75ff173
Revises: d2fa8201b04a
Create Date: 2024-06-03 17:48:10.443847

"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlalchemy_utils
from alembic import op
from data_handler.handlers.liquidable_debt.values import LendingProtocolNames

# revision identifiers, used by Alembic.
revision: str = "e4c7f75ff173"
down_revision: Union[str, None] = "d2fa8201b04a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Creates the 'liquidable_debt' table if it does not exist."""
    # ### commands auto generated by Alembic - please adjust! ###
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "liquidable_debt" not in inspector.get_table_names():
        op.create_table(
            "liquidable_debt",
            sa.Column("id", sa.UUID(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.Column(
                "protocol",
                sqlalchemy_utils.types.choice.ChoiceType(LendingProtocolNames),
                nullable=False,
            ),
            sa.Column("liquidable_debt", sa.DECIMAL(), nullable=False),
            sa.Column("price", sa.DECIMAL(), nullable=False),
            sa.Column("collateral_token", sa.String(), nullable=False),
            sa.Column("debt_token", sa.String(), nullable=False),
        )
    # ### end Alembic commands ###


def downgrade() -> None:
    """Drops the 'liquidable_debt' table."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table("liquidable_debt")
    # ### end Alembic commands ###