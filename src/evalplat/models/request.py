"""请求模型（PRD §8.9）。

Request 代表一次 API 调用（一条 sample 的一次请求）。
使用 BIGINT IDENTITY 主键（PG 10+ GENERATED ALWAYS AS IDENTITY），
性能优于 UUID 且保持严格递增。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from evalplat.db.base import Base, TimestampMixin

_KIND_CHECK = "kind IN ('perf', 'quality')"
_PHASE_CHECK = "phase IS NULL OR phase IN ('cold_start', 'warmup', 'steady')"
_STATUS_CHECK = "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')"


class Request(TimestampMixin, Base):
    """请求：一次 API 调用，含双口径 Token 统计。"""

    __tablename__ = "requests"
    __table_args__ = (
        CheckConstraint(_KIND_CHECK, name="kind"),
        CheckConstraint(_PHASE_CHECK, name="phase"),
        CheckConstraint(_STATUS_CHECK, name="status"),
        Index("ix_requests_run_id", "run_id", "id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("GENERATED ALWAYS AS IDENTITY"),
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id"),
        nullable=False,
    )
    sample_index: Mapped[int | None] = mapped_column(Integer)
    session_id: Mapped[str | None] = mapped_column(String(64))
    turn_index: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    phase: Mapped[str | None] = mapped_column(String(16))
    stream_mode: Mapped[str | None] = mapped_column(String(8))
    input_messages: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 供应商上报口径
    vendor_prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    vendor_completion_tokens: Mapped[int | None] = mapped_column(Integer)
    vendor_total_tokens: Mapped[int | None] = mapped_column(Integer)
    # 本地 Tokenizer 口径
    local_prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    local_completion_tokens: Mapped[int | None] = mapped_column(Integer)
    local_total_tokens: Mapped[int | None] = mapped_column(Integer)
