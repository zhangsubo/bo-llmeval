"""add immutable report versions"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260904_reports"
down_revision: Union[str, None] = "9e14dc04566b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'generating'"), nullable=False),
        sa.Column("summary_path", sa.String(length=512), nullable=True),
        sa.Column("details_path", sa.String(length=512), nullable=True),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('generating', 'completed', 'failed')", name="ck_reports_status"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_reports_task_id_tasks"),
        sa.PrimaryKeyConstraint("id", name="pk_reports"),
        sa.UniqueConstraint("task_id", "version", name="uq_reports_task_version"),
    )
    op.create_index("ix_reports_task_id", "reports", ["task_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_reports_task_id", table_name="reports")
    op.drop_table("reports")
