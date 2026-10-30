"""历史数据清理批次。

清理是高风险人工操作，因此把预览得到的任务 ID、文件清单和计数固化到
批次中。执行和补偿重试只消费这份不可变清单，不重新扩大删除范围。
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from evalplat.db.base import Base, TimestampMixin


class CleanupBatch(TimestampMixin, Base):
    """一次历史任务清理计划及其执行结果。"""

    __tablename__ = "cleanup_batches"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'partial', 'failed')",
            name="status",
        ),
        Index("ix_cleanup_batches_status_cutoff", "status", "cutoff_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    statuses: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    # Immutable task/file manifest created by preview/create.
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    planned_counts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    deleted_counts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    failed_items: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
