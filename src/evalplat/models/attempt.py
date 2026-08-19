"""尝试模型（PRD §8.9）。

Attempt 代表一次请求的重试尝试，记录延迟指标和原始响应。
使用 BIGINT IDENTITY 主键，与 Request 一致。
raw_response 用 Text 存字节级原文（不用 JSONB，避免编码破坏）。
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from evalplat.db.base import Base, TimestampMixin


class Attempt(TimestampMixin, Base):
    """尝试：一次请求的重试尝试，含延迟指标和原始响应。"""

    __tablename__ = "attempts"
    __table_args__ = (
        Index("ix_attempts_request_id", "request_id", "attempt_index"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("GENERATED ALWAYS AS IDENTITY"),
    )
    request_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("requests.id"),
        nullable=False,
    )
    attempt_index: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 延迟指标
    latency_ms: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    ttft_ms: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    tpot_ms: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    itl_series: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # 速度指标
    avg_speed: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    peak_speed: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    peak_tps: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    # 供应商返回的 usage
    vendor_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # 字节级原文（不用 JSONB，避免编码破坏）
    raw_response: Mapped[str | None] = mapped_column(Text)
    extracted_fields: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # 错误信息
    error_category: Mapped[str | None] = mapped_column(String(32))
    error_body: Mapped[str | None] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    vendor_request_id: Mapped[str | None] = mapped_column(String(128))
    response_headers: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
