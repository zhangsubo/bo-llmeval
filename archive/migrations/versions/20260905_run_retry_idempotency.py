"""add durable retry lineage and idempotency constraint"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260905_run_retry_idempotency"
down_revision: Union[str, None] = "20260905_connection_tests"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("retry_of_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_runs_retry_of_run_id_runs",
        "runs",
        "runs",
        ["retry_of_run_id"],
        ["id"],
    )
    op.create_unique_constraint("uq_runs_retry_of_run_id", "runs", ["retry_of_run_id"])
    op.create_index("ix_runs_retry_of_run_id", "runs", ["retry_of_run_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_runs_retry_of_run_id", table_name="runs")
    op.drop_constraint("uq_runs_retry_of_run_id", "runs", type_="unique")
    op.drop_constraint("fk_runs_retry_of_run_id_runs", "runs", type_="foreignkey")
    op.drop_column("runs", "retry_of_run_id")
