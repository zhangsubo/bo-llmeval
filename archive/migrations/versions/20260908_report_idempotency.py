"""add idempotency key for report generation"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260908_report_idempotency"
down_revision: Union[str, None] = "20260907_cached_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("idempotency_key", sa.String(length=128), nullable=True))
    op.create_unique_constraint("uq_reports_idempotency_key", "reports", ["idempotency_key"])


def downgrade() -> None:
    op.drop_constraint("uq_reports_idempotency_key", "reports", type_="unique")
    op.drop_column("reports", "idempotency_key")
