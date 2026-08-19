"""连接模型（PRD §8.2.2）。

每个连接绑定一个供应商，携带 base_url 和加密后的 API Key。
连接状态流转：active → disabled → archived（与供应商一致，无物理删除）。
"""

import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from evalplat.db.base import Base, TimestampMixin

_STATUS_CHECK = "status IN ('active', 'disabled', 'archived')"


class Connection(TimestampMixin, Base):
    """供应商连接：base_url + Fernet 加密 API Key + 超时/限流配置。"""

    __tablename__ = "connections"
    __table_args__ = (
        CheckConstraint(_STATUS_CHECK, name="status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("suppliers.id"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    region: Mapped[str | None] = mapped_column(String(64))
    service_tier: Mapped[str | None] = mapped_column(String(32))
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    api_key_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    credential_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    tls_skip_verify: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    timeouts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    limits: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'active'")
    )
    remark: Mapped[str | None] = mapped_column(Text)
