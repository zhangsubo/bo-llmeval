"""模型价格模型（PRD §8.3.3）。

价格采用追加式版本表：每次价格变更 INSERT 新行，永不 UPDATE。
task 快照在入队时捕获 price_version，保证历史结果可复现。
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from evalplat.db.base import Base, TimestampMixin


class ModelPrice(TimestampMixin, Base):
    """模型价格：追加式版本表，每次变更 INSERT 新行。"""

    __tablename__ = "model_prices"
    __table_args__ = (
        CheckConstraint("input_token_price >= 0", name="input_token_price"),
        CheckConstraint("output_token_price >= 0", name="output_token_price"),
        CheckConstraint("cached_token_price >= 0", name="cached_token_price"),
        CheckConstraint("exchange_rate > 0", name="exchange_rate"),
        UniqueConstraint("model_deployment_id", "price_version", name="uq_model_prices_deployment_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    model_deployment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model_deployments.id"),
        nullable=False,
    )
    input_token_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False
    )
    output_token_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False
    )
    cached_token_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    unit: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'per_1k_tokens'")
    )
    currency: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'CNY'")
    )
    exchange_rate: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False
    )
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    price_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'manual'")
    )
