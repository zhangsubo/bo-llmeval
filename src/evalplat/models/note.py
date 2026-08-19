"""备注模型（PRD §8.10）。

通用备注表，通过 target_type + target_id 关联到 task 或 run。
"""

import uuid

from sqlalchemy import CheckConstraint, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from evalplat.db.base import Base, TimestampMixin

_TARGET_TYPE_CHECK = "target_type IN ('task', 'run')"


class Note(TimestampMixin, Base):
    """备注：通过 target_type + target_id 关联到 task 或 run。"""

    __tablename__ = "notes"
    __table_args__ = (
        CheckConstraint(_TARGET_TYPE_CHECK, name="target_type"),
        Index("ix_notes_target", "target_type", "target_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
