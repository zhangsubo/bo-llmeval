"""add explicit Run state while asynchronous code scoring is pending"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260905_run_awaiting_code"
down_revision: Union[str, None] = "20260905_connection_rate_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RUN_STATUS_WITH_CODE = (
    "status IN ('draft', 'queued', 'running', 'awaiting_code', "
    "'completed', 'partially_completed', 'failed', 'cancelling', "
    "'cancelled', 'interrupted_failed')"
)
_RUN_STATUS_LEGACY = (
    "status IN ('draft', 'queued', 'running', 'completed', "
    "'partially_completed', 'failed', 'cancelling', 'cancelled', "
    "'interrupted_failed')"
)


def upgrade() -> None:
    op.drop_constraint(op.f("ck_runs_status"), "runs", type_="check")
    op.create_check_constraint(op.f("ck_runs_status"), "runs", _RUN_STATUS_WITH_CODE)


def downgrade() -> None:
    op.drop_constraint(op.f("ck_runs_status"), "runs", type_="check")
    op.create_check_constraint(op.f("ck_runs_status"), "runs", _RUN_STATUS_LEGACY)
