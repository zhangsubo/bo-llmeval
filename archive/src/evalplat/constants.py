"""状态枚举常量（存储取值一律英文，界面中文映射在前端层处理）。

语义来源：PRD §8.8.2 状态机
「草稿 → 排队中 → 运行中 → 已完成/部分完成/失败」，
取消分支「排队中/运行中 → 取消中 → 已取消」；
Run 另含「中断失败」（Worker 失联，PRD §8.8）。

建模约定：状态列一律 String + CHECK 约束（禁止 PostgreSQL ENUM），
CHECK 取值引用本模块常量，保证模型、迁移、service 层单一事实源。
"""


class SupplierStatus:
    """供应商状态（PRD §8.2.1：启用/停用/归档，无物理删除）。"""

    ACTIVE = "active"
    DISABLED = "disabled"
    ARCHIVED = "archived"

    ALL: tuple[str, ...] = (ACTIVE, DISABLED, ARCHIVED)


class TaskStatus:
    """评测任务状态（PRD §8.8.2 八值）。"""

    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIALLY_COMPLETED = "partially_completed"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"

    ALL: tuple[str, ...] = (
        DRAFT,
        QUEUED,
        RUNNING,
        COMPLETED,
        PARTIALLY_COMPLETED,
        FAILED,
        CANCELLING,
        CANCELLED,
    )


class RunStatus:
    """评测 Run 状态：与 TaskStatus 相同，另含代码等待和中断失败状态。

    ``awaiting_code`` 表示模型请求已经结束，但代码评分作业仍在独立
    code worker 队列中。它是非终态，避免在异步证据完成前把 Run 当成可报告结果。
    """

    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIALLY_COMPLETED = "partially_completed"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    AWAITING_CODE = "awaiting_code"
    INTERRUPTED_FAILED = "interrupted_failed"

    ALL: tuple[str, ...] = (
        DRAFT,
        QUEUED,
        RUNNING,
        COMPLETED,
        PARTIALLY_COMPLETED,
        FAILED,
        CANCELLING,
        CANCELLED,
        AWAITING_CODE,
        INTERRUPTED_FAILED,
    )

    # Shared lifecycle sets. Keeping them in the status vocabulary prevents
    # queue, cancellation, credential and cleanup services from drifting when
    # a new state is introduced.
    ACTIVE: tuple[str, ...] = (
        QUEUED,
        RUNNING,
        CANCELLING,
        AWAITING_CODE,
    )
    TERMINAL: tuple[str, ...] = (
        COMPLETED,
        PARTIALLY_COMPLETED,
        FAILED,
        CANCELLED,
        INTERRUPTED_FAILED,
    )


# Destructive history cleanup requires an explicit operator confirmation.
HISTORY_CLEANUP_CONFIRMATION_TEXT = "CONFIRM"
