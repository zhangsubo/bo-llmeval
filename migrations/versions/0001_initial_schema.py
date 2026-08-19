"""初始 schema：suppliers + runs（含队列预埋列与部分索引）

Revision ID: 0001
Revises:
Create Date: 2026-08-18

本迁移为全阶段迁移链的基准（one-way）：约束/索引命名遵循
NAMING_CONVENTION（ix_/uq_/ck_/fk_/pk_），后续 autogenerate 以其为准。

按 PG 14 最低兼容线编写（gen_random_uuid 自 PG 13 内置）；
runs 表队列预埋列与 ix_runs_queue 部分索引见 RESEARCH.md Pattern 4。
task_id / model_deployment_id 暂为裸 UUID 列，FK 由 Plan 02 建
tasks / model_deployments 表时补全。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "suppliers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("short_name", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_suppliers"),
        sa.CheckConstraint(
            "status IN ('active', 'disabled', 'archived')",
            name="ck_suppliers_status",
        ),
    )

    op.create_table(
        "runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "model_deployment_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("seq", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column("run_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        # 队列预埋列（Phase 4 Worker lease / fencing token / heartbeat）
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "fencing_token",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_runs"),
        sa.CheckConstraint(
            "status IN ('draft', 'queued', 'running', 'completed', "
            "'partially_completed', 'failed', 'cancelling', 'cancelled', "
            "'interrupted_failed')",
            name="ck_runs_status",
        ),
    )
    # FIFO 认领部分索引：只覆盖排队行
    op.create_index(
        "ix_runs_queue",
        "runs",
        ["queued_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index("ix_runs_status", "runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_runs_status", table_name="runs")
    op.drop_index("ix_runs_queue", table_name="runs")
    op.drop_table("runs")
    op.drop_table("suppliers")
