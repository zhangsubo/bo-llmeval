"""对比组模型（PRD §8.3.2）。

对比组用于将多个模型部署归为一组进行横向对比评测。
"""

import uuid

from sqlalchemy import ForeignKeyConstraint, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from evalplat.db.base import Base, TimestampMixin

_TOKENIZER_FK = ForeignKeyConstraint(
    ["default_tokenizer_id"],
    ["tokenizers.id"],
    name="fk_comparison_groups_default_tokenizer_id_tokenizers",
)
_TOKENIZER_FK.ddl_if(dialect="postgresql")


class ComparisonGroup(TimestampMixin, Base):
    """对比组：将多个模型部署归为一组进行横向对比。"""

    __tablename__ = "comparison_groups"
    __table_args__ = (_TOKENIZER_FK,)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # FK 约束由后续 Plan 补全（tokenizers 表同批次创建）
    default_tokenizer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    remark: Mapped[str | None] = mapped_column(Text)
