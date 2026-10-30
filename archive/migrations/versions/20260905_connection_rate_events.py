"""add connection runtime rate reservations"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260905_connection_rate_events"
down_revision: Union[str, None] = "20260905_code_executions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "connection_rate_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'active'"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reserved_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("actual_tokens", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('active', 'released', 'expired')", name="ck_connection_rate_events_status"),
        sa.ForeignKeyConstraint(["connection_id"], ["connections.id"], name="fk_connection_rate_events_connection_id_connections"),
        sa.ForeignKeyConstraint(["request_id"], ["requests.id"], name="fk_connection_rate_events_request_id_requests"),
        sa.PrimaryKeyConstraint("id", name="pk_connection_rate_events"),
    )
    op.create_index(
        "ix_connection_rate_events_connection_started",
        "connection_rate_events",
        ["connection_id", "started_at"],
        unique=False,
    )
    op.create_index(
        "ix_connection_rate_events_active",
        "connection_rate_events",
        ["connection_id", "status", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_connection_rate_events_active", table_name="connection_rate_events")
    op.drop_index("ix_connection_rate_events_connection_started", table_name="connection_rate_events")
    op.drop_table("connection_rate_events")
