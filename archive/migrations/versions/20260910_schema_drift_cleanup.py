"""normalize legacy constraint names and code execution defaults"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260910_schema_drift_cleanup"
down_revision: Union[str, None] = "20260909_system_setting_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RENAMES = (
    ("cleanup_batches", "ck_cleanup_batches_ck_cleanup_batches_status", "ck_cleanup_batches_status"),
    ("code_executions", "ck_code_executions_ck_code_executions_status", "ck_code_executions_status"),
    ("code_executions", "ck_code_executions_ck_code_executions_compile_status", "ck_code_executions_compile_status"),
    ("code_executions", "ck_code_executions_ck_code_executions_run_status", "ck_code_executions_run_status"),
    ("connection_rate_events", "ck_connection_rate_events_ck_connection_rate_events_status", "ck_connection_rate_events_status"),
    ("reports", "ck_reports_ck_reports_status", "ck_reports_status"),
)


def upgrade() -> None:
    for table, old_name, new_name in _RENAMES:
        op.execute(f'ALTER TABLE "{table}" RENAME CONSTRAINT "{old_name}" TO "{new_name}"')
    op.execute('ALTER TABLE code_executions ALTER COLUMN tests DROP DEFAULT')


def downgrade() -> None:
    op.execute("ALTER TABLE code_executions ALTER COLUMN tests SET DEFAULT ''")
    for table, old_name, new_name in reversed(_RENAMES):
        op.execute(f'ALTER TABLE "{table}" RENAME CONSTRAINT "{new_name}" TO "{old_name}"')
