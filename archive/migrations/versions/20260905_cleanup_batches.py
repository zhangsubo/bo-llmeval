"""add immutable history cleanup batches"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260905_cleanup_batches"
down_revision: Union[str, None] = "20260905_run_awaiting_code"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cleanup_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("statuses", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("planned_counts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("deleted_counts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("failed_items", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'running', 'completed', 'partial', 'failed')", name="ck_cleanup_batches_status"),
        sa.PrimaryKeyConstraint("id", name="pk_cleanup_batches"),
    )
    op.create_index("ix_cleanup_batches_status_cutoff", "cleanup_batches", ["status", "cutoff_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_cleanup_batches_status_cutoff", table_name="cleanup_batches")
    op.drop_table("cleanup_batches")
