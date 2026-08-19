"""测试模板与模板版本模型（PRD §8.6）。

测试模板（TestTemplate）定义评测方案，模板版本（TestTemplateVersion）
是不可变快照，包含负载参数、生成参数等。
"""

import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from evalplat.db.base import Base, TimestampMixin

_STATUS_CHECK = "status IN ('draft', 'active', 'archived')"


class TestTemplate(TimestampMixin, Base):
    """测试模板：定义评测方案的逻辑容器。"""

    __tablename__ = "test_templates"
    __table_args__ = (
        CheckConstraint(_STATUS_CHECK, name="status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_builtin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # remark 在 TestTemplate 上没有定义（PRD 未要求），如有需要后续可加


class TestTemplateVersion(TimestampMixin, Base):
    """测试模板版本：不可变快照，含负载参数、生成参数等。"""

    __tablename__ = "test_template_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_templates.id"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    test_type: Mapped[str | None] = mapped_column(String(32))
    scenario: Mapped[str | None] = mapped_column(String(32))
    # FK 约束由后续 Plan 补全
    dataset_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # FK 约束由后续 Plan 补全
    tokenizer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    generation_params: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    stream_mode: Mapped[str | None] = mapped_column(String(16))
    warmup_count: Mapped[int | None] = mapped_column(Integer)
    load_profile: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    request_count: Mapped[int | None] = mapped_column(Integer)
    retry_policy: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    timeouts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    limits: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    remark: Mapped[str | None] = mapped_column(Text)
