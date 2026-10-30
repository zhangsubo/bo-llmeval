"""模型部署模型（PRD §8.3.1）。

模型部署 = 连接 + model_id 的组合，代表一个可调用的模型端点。
source 标记来源：synced（自动同步）/ manual（手动创建）。
"""

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from evalplat.db.base import Base, TimestampMixin

_SOURCE_CHECK = "source IN ('synced', 'manual')"
_STATUS_CHECK = "status IN ('active', 'disabled', 'archived')"
_COMPARISON_GROUP_FK = ForeignKeyConstraint(
    ["comparison_group_id"],
    ["comparison_groups.id"],
    name="fk_model_deployments_comparison_group_id_comparison_groups",
)
_COMPARISON_GROUP_FK.ddl_if(dialect="postgresql")
_TOKENIZER_FK = ForeignKeyConstraint(
    ["tokenizer_id"],
    ["tokenizers.id"],
    name="fk_model_deployments_tokenizer_id_tokenizers",
)
_TOKENIZER_FK.ddl_if(dialect="postgresql")


class ModelDeployment(TimestampMixin, Base):
    """模型部署：连接 + model_id，代表一个可调用的模型端点。"""

    __tablename__ = "model_deployments"
    __table_args__ = (
        CheckConstraint(_SOURCE_CHECK, name="source"),
        CheckConstraint(_STATUS_CHECK, name="status"),
        UniqueConstraint(
            "connection_id",
            "model_id",
            name="uq_model_deployments_connection_model",
        ),
        Index("ix_model_deployments_connection_id", "connection_id"),
        Index("ix_model_deployments_comparison_group_id", "comparison_group_id"),
        _COMPARISON_GROUP_FK,
        _TOKENIZER_FK,
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("connections.id"),
        nullable=False,
    )
    model_id: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(256))
    # FK 约束由后续 Plan 补全（comparison_groups 表同批次创建）
    comparison_group_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    context_window: Mapped[int | None] = mapped_column(Integer)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer)
    supports_streaming: Mapped[bool | None] = mapped_column(Boolean)
    supports_non_streaming: Mapped[bool | None] = mapped_column(Boolean)
    # FK 约束由后续 Plan 补全（tokenizers 表同批次创建）
    tokenizer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    model_version: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'active'")
    )
    remark: Mapped[str | None] = mapped_column(Text)
