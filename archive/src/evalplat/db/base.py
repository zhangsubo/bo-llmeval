"""ORM 基类：MetaData 命名约定 + 时间戳 Mixin。

命名约定必须在第一个模型落笔前建立（RESEARCH.md Pitfall 1）：
没有它，PostgreSQL 自动生成的约束名不可预测，后续 Alembic
autogenerate 会产生无法对齐的约束重命名 diff。

本文件由 01-01 Task 1 checkpoint 确认为 one-way 决策：
ix_/uq_/ck_/fk_/pk_ 五段式，逐字采用 SQLAlchemy 官方示例。
"""

from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Source: docs.sqlalchemy.org/en/latest/core/constraints.html
# "Configuring a Naming Convention for a MetaData Collection" 官方示例
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """全部 ORM 模型的声明基类，挂带命名约定的 MetaData。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """created_at / updated_at 时间戳列（一律 timestamptz，存 UTC）。

    默认值由数据库端 server_default=func.now() 产生，应用侧不赋时间，
    避免应用/DB 双时钟源漂移（AGENTS.md：所有时间使用 UTC 存储）。
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
