"""评测任务模型（PRD §8.8）。

Task 是评测的顶层调度单元，状态机参见 constants.TaskStatus。
config_snapshot 在入队时冻结，保证历史结果可复现。
idempotency_key 用于防止重复提交。
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from evalplat.constants import TaskStatus
from evalplat.db.base import Base, TimestampMixin

_STATUS_CHECK = "status IN (" + ", ".join(f"'{s}'" for s in TaskStatus.ALL) + ")"
_RUN_MODE_CHECK = "run_mode IN ('serial', 'parallel')"


class Task(TimestampMixin, Base):
    """评测任务：顶层调度单元，状态机参见 constants.TaskStatus。"""

    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(_STATUS_CHECK, name="status"),
        CheckConstraint(_RUN_MODE_CHECK, name="run_mode"),
        Index("ix_tasks_status_queued", "status", "queued_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str | None] = mapped_column(String(256))
    # FK 约束由后续 Plan 补全（test_template_versions 表同批次创建）
    template_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    run_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'draft'")
    )
    config_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    precheck_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(64), unique=True
    )
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remark: Mapped[str | None] = mapped_column(Text)
