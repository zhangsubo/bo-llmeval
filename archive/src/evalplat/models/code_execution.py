"""代码生成样本的隔离执行记录。

普通 Run worker 只负责生成模型答案并创建 ``pending`` 作业；独立 code
worker 认领作业，在短生命周期 Docker 容器内执行并写回结果。代码执行的
lease/fencing 字段与 Run 队列保持相同语义，避免旧 worker 覆盖新结果。
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from evalplat.db.base import Base, TimestampMixin

_STATUS_CHECK = "status IN ('pending', 'running', 'passed', 'failed', 'timeout', 'unavailable', 'cancelled')"
_COMPILE_CHECK = "compile_status IN ('pending', 'passed', 'failed', 'not_run')"
_RUN_CHECK = "run_status IN ('pending', 'passed', 'failed', 'not_run')"


class CodeExecution(TimestampMixin, Base):
    """单个模型代码答案的隔离执行作业及不可变证据。"""

    __tablename__ = "code_executions"
    __table_args__ = (
        CheckConstraint(_STATUS_CHECK, name="status"),
        CheckConstraint(_COMPILE_CHECK, name="compile_status"),
        CheckConstraint(_RUN_CHECK, name="run_status"),
        Index("ix_code_executions_queue", "created_at", postgresql_where=text("status = 'pending'")),
        Index("ix_code_executions_run_id", "run_id", "id"),
        Index("ix_code_executions_request_id", "request_id"),
        UniqueConstraint("request_id", name="uq_code_executions_request_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id"), nullable=False
    )
    request_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("requests.id"), nullable=True
    )
    sample_index: Mapped[int | None] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    tests: Mapped[str] = mapped_column(Text, nullable=False, default="")

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    compile_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    run_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    stdout: Mapped[str | None] = mapped_column(Text)
    stderr: Mapped[str | None] = mapped_column(Text)
    test_results: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[float | None] = mapped_column(Numeric(12, 2))
    error_category: Mapped[str | None] = mapped_column(String(32))
    error_summary: Mapped[str | None] = mapped_column(Text)

    container_image: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        server_default=text(
            "'python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534'"
        ),
    )
    timeout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("10")
    )
    memory_mb: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("256")
    )
    cpus: Mapped[float] = mapped_column(
        Numeric(6, 2), nullable=False, server_default=text("1.0")
    )
    pids_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("64")
    )

    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fencing_token: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
