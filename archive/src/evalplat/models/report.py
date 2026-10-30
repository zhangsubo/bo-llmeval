"""不可变静态报告版本模型。"""

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from evalplat.db.base import Base, TimestampMixin


class Report(TimestampMixin, Base):
    """任务报告的生成记录；完成后文件和快照只读。"""

    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint("status IN ('generating', 'completed', 'failed')", name="status"),
        UniqueConstraint("task_id", "version", name="uq_reports_task_version"),
        Index("ix_reports_task_id", "task_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'generating'")
    )
    summary_path: Mapped[str | None] = mapped_column(String(512))
    details_path: Mapped[str | None] = mapped_column(String(512))
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
