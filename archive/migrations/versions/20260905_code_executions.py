"""add isolated code execution jobs"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260905_code_executions"
down_revision: Union[str, None] = "20260904_reports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "code_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", sa.BigInteger(), nullable=True),
        sa.Column("sample_index", sa.Integer(), nullable=True),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("tests", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("compile_status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("run_status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("stdout", sa.Text(), nullable=True),
        sa.Column("stderr", sa.Text(), nullable=True),
        sa.Column("test_results", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("error_category", sa.String(length=32), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("container_image", sa.String(length=256), server_default=sa.text("'python:3.11-slim'"), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), server_default=sa.text("10"), nullable=False),
        sa.Column("memory_mb", sa.Integer(), server_default=sa.text("256"), nullable=False),
        sa.Column("cpus", sa.Numeric(precision=6, scale=2), server_default=sa.text("1.0"), nullable=False),
        sa.Column("pids_limit", sa.Integer(), server_default=sa.text("64"), nullable=False),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fencing_token", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'passed', 'failed', 'timeout', 'unavailable', 'cancelled')",
            name="ck_code_executions_status",
        ),
        sa.CheckConstraint(
            "compile_status IN ('pending', 'passed', 'failed', 'not_run')",
            name="ck_code_executions_compile_status",
        ),
        sa.CheckConstraint(
            "run_status IN ('pending', 'passed', 'failed', 'not_run')",
            name="ck_code_executions_run_status",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name="fk_code_executions_run_id_runs"),
        sa.ForeignKeyConstraint(["request_id"], ["requests.id"], name="fk_code_executions_request_id_requests"),
        sa.PrimaryKeyConstraint("id", name="pk_code_executions"),
        sa.UniqueConstraint("request_id", name="uq_code_executions_request_id"),
    )
    op.create_index(
        "ix_code_executions_queue",
        "code_executions",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index("ix_code_executions_run_id", "code_executions", ["run_id", "id"], unique=False)
    op.create_index("ix_code_executions_request_id", "code_executions", ["request_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_code_executions_request_id", table_name="code_executions")
    op.drop_index("ix_code_executions_run_id", table_name="code_executions")
    op.drop_index("ix_code_executions_queue", table_name="code_executions")
    op.drop_table("code_executions")
