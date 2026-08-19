"""供应商模型（PRD §8.2.1）。

语义：供应商无物理删除，只有状态流转（启用/停用/归档）——
本表刻意不提供 deleted_at 列。
"""

import uuid

from sqlalchemy import CheckConstraint, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from evalplat.constants import SupplierStatus
from evalplat.db.base import Base, TimestampMixin

# CHECK 约束取值与 constants.SupplierStatus 保持单一事实源
_STATUS_CHECK = "status IN (" + ", ".join(f"'{s}'" for s in SupplierStatus.ALL) + ")"


class Supplier(TimestampMixin, Base):
    """供应商：name/short_name/type/status/remark，无物理删除。"""

    __tablename__ = "suppliers"
    __table_args__ = (
        # 命名约定展开为 ck_suppliers_status
        CheckConstraint(_STATUS_CHECK, name="status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        # gen_random_uuid() 自 PG 13 内置，无需 pgcrypto 扩展
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    short_name: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)  # 原厂/云厂商/其他
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'active'")
    )
    remark: Mapped[str | None] = mapped_column(Text)
