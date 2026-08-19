"""数据集与数据集版本模型（PRD §8.5）。

数据集（Dataset）是逻辑容器，数据集版本（DatasetVersion）是不可变快照。
kind 区分用途：performance（性能测试）/ quality（质量测试）。
"""

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from evalplat.db.base import Base, TimestampMixin

_KIND_CHECK = "kind IN ('performance', 'quality')"
_VERSION_STATUS_CHECK = "status IN ('pending', 'processing', 'ready', 'failed', 'archived')"


class Dataset(TimestampMixin, Base):
    """数据集：逻辑容器，含多个版本。"""

    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    remark: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(_KIND_CHECK, name="kind"),
    )


class DatasetVersion(TimestampMixin, Base):
    """数据集版本：不可变快照，含采样统计和字段映射。"""

    __tablename__ = "dataset_versions"
    __table_args__ = (
        CheckConstraint(_VERSION_STATUS_CHECK, name="status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("datasets.id"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str | None] = mapped_column(String(32))
    license: Mapped[str | None] = mapped_column(String(128))
    checksum: Mapped[str | None] = mapped_column(String(128))
    sample_count: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(16))
    capability_tags: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    default_prompt: Mapped[str | None] = mapped_column(Text)
    scoring_method: Mapped[str | None] = mapped_column(String(32))
    field_mapping: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    token_length_stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_path: Mapped[str | None] = mapped_column(String(512))
