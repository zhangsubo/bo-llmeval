"""连接级运行时限流预留事件。"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from evalplat.db.base import Base, TimestampMixin


class ConnectionRateEvent(TimestampMixin, Base):
    """一次实际或预留的连接请求，用于并发和滚动窗口限额。"""

    __tablename__ = "connection_rate_events"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'released', 'expired')", name="status"),
        Index("ix_connection_rate_events_connection_started", "connection_id", "started_at"),
        Index("ix_connection_rate_events_active", "connection_id", "status", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id"), nullable=False
    )
    request_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("requests.id"))
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'active'")
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reserved_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    actual_tokens: Mapped[int | None] = mapped_column(Integer)
