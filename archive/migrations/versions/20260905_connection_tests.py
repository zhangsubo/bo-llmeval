"""persist latest connection compatibility test evidence"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260905_connection_tests"
down_revision: Union[str, None] = "20260905_quality_results"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "connections",
        sa.Column("last_test_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "connections",
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("connections", "last_test_at")
    op.drop_column("connections", "last_test_result")
