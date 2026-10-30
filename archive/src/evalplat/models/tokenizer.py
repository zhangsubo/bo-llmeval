"""Tokenizer 模型（PRD §8.4）。

Tokenizer 用于本地 Token 计数，实现双口径统计。
source 表示来源：modelscope / huggingface / upload / builtin。
状态流转：pending → downloading → ready / failed，可归档。
"""

import uuid

from sqlalchemy import BigInteger, CheckConstraint, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from evalplat.db.base import Base, TimestampMixin

_SOURCE_CHECK = "source IN ('modelscope', 'huggingface', 'upload', 'builtin')"
_STATUS_CHECK = "status IN ('pending', 'downloading', 'ready', 'failed', 'archived')"


class Tokenizer(TimestampMixin, Base):
    """Tokenizer：本地 Token 计数器，支持多种来源。"""

    __tablename__ = "tokenizers"
    __table_args__ = (
        CheckConstraint(_SOURCE_CHECK, name="source"),
        CheckConstraint(_STATUS_CHECK, name="status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(256))
    revision: Mapped[str | None] = mapped_column(String(64))
    checksum: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    local_path: Mapped[str | None] = mapped_column(String(512))
