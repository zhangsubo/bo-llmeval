"""评测 Run 模型（兼 PostgreSQL 持久化 FIFO 队列表，PRD §8.8 / EXEC-01）。

队列预埋设计（RESEARCH.md Pattern 4）：Phase 1 建表时即带上
lease_owner / lease_expires_at / fencing_token / heartbeat_at 队列列
与 ix_runs_queue 部分索引，Phase 4 Worker 直接启用，避免在线加列迁移。

Phase 4 认领 SQL（固化于此，Worker 直接引用）：

    UPDATE runs
    SET status = 'running',
        lease_owner = :worker_id,
        lease_expires_at = now() + interval '60 seconds',
        fencing_token = fencing_token + 1,
        heartbeat_at = now()
    WHERE id = (
        SELECT id FROM runs
        WHERE status = 'queued'
        ORDER BY queued_at ASC          -- FIFO：严格按入队时间
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING *;

fencing token 校验模式：Worker 每次写结果携带认领时的 token，
``UPDATE ... WHERE id=:id AND fencing_token=:token``，影响行数为 0
即已失去 lease，上报被拒绝（PRD §8.8：旧 Worker 不得覆盖新 Worker
或终态结果）。代码质量评测使用 ``awaiting_code`` 作为非终态，等待
独立 code worker 完成所有 CodeExecution 后再写入最终 Run 状态。
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from evalplat.constants import RunStatus
from evalplat.db.base import Base, TimestampMixin

# CHECK 约束取值与 constants.RunStatus 保持单一事实源
_STATUS_CHECK = "status IN (" + ", ".join(f"'{s}'" for s in RunStatus.ALL) + ")"

_TASK_FK = ForeignKeyConstraint(
    ["task_id"], ["tasks.id"], name="fk_runs_task_id_tasks"
)
_TASK_FK.ddl_if(dialect="postgresql")
_MODEL_DEPLOYMENT_FK = ForeignKeyConstraint(
    ["model_deployment_id"],
    ["model_deployments.id"],
    name="fk_runs_model_deployment_id_model_deployments",
)
_MODEL_DEPLOYMENT_FK.ddl_if(dialect="postgresql")


class Run(TimestampMixin, Base):
    """评测 Run：业务记录 + 队列表（不另建 queue 表，EXEC-01）。"""

    __tablename__ = "runs"
    __table_args__ = (
        # 命名约定展开为 ck_runs_status
        CheckConstraint(_STATUS_CHECK, name="status"),
        # FIFO 认领只扫排队行，部分索引保持极小
        Index(
            "ix_runs_queue",
            "queued_at",
            postgresql_where=text("status = 'queued'"),
        ),
        # 全局并发计数扫描 running/cancelling 状态，与监控筛选共用状态索引
        Index("ix_runs_status", "status"),
        # A source Run can have one durable manual retry operation.
        UniqueConstraint("retry_of_run_id", name="uq_runs_retry_of_run_id"),
        Index("ix_runs_retry_of_run_id", "retry_of_run_id"),
        _TASK_FK,
        _MODEL_DEPLOYMENT_FK,
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # 关联任务：FK 约束由 Plan 02 建 tasks 表时补全，本任务先存裸 UUID 列
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # 关联模型部署：FK 约束同样由 Plan 02 补全
    model_deployment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # Self-reference used as the durable idempotency key for manual retries.
    retry_of_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id"), nullable=True
    )
    seq: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'queued'")
    )
    # 任务配置快照（不可变，PRD §6.2）；快照语义上禁止原地修改，不挂 MutableDict
    run_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # —— 队列预埋列（Phase 4 启用，见模块 docstring 认领 SQL）——
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fencing_token: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_summary: Mapped[str | None] = mapped_column(Text)
