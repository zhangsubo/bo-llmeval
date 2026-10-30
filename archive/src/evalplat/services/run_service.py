"""评测 Run 查询业务逻辑。"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evalplat.core.errors import ConflictError, NotFoundError
from evalplat.models.code_execution import CodeExecution
from evalplat.models.request import Request
from evalplat.models.attempt import Attempt
from evalplat.models.run import Run
from evalplat.models.task import Task


def get_run(db: Session, run_id: uuid.UUID) -> Run:
    """获取单个 Run。"""
    run = db.get(Run, run_id)
    if not run:
        raise NotFoundError("Run 不存在")
    return run


def cancel_run(db: Session, run_id: uuid.UUID) -> Run:
    """Cancel one Run without cancelling sibling Runs in the same Task.

    The parent Task row is locked first to match task submit/cancel/retry and
    report/cleanup operations. Running provider calls are allowed to finish,
    but the Worker observes ``cancelling`` before scheduling the next call.
    """

    existing = db.get(Run, run_id)
    if existing is None:
        raise NotFoundError("Run 不存在")
    task = (
        db.scalar(select(Task).where(Task.id == existing.task_id).with_for_update())
        if existing.task_id
        else None
    )
    run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if run is None:
        raise NotFoundError("Run 不存在")
    if run.status in {"cancelling", "cancelled"}:
        return run
    if run.status in {"completed", "partially_completed", "failed", "interrupted_failed"}:
        raise ConflictError("终态 Run 不可取消")
    was_awaiting_code = run.status == "awaiting_code"
    if run.status in {"draft", "queued"}:
        run.status = "cancelled"
        run.finished_at = datetime.now(timezone.utc)
    elif run.status in {"running", "awaiting_code"}:
        run.status = "cancelling"
    else:
        raise ConflictError("当前 Run 状态不可取消")

    pending_jobs = list(
        db.scalars(
            select(CodeExecution)
            .where(CodeExecution.run_id == run.id, CodeExecution.status == "pending")
            .with_for_update(skip_locked=True)
        )
    )
    for job in pending_jobs:
        job.status = "cancelled"
        job.error_category = "run_cancelled"
        job.error_summary = "Run 已取消，代码作业未执行"
        job.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)

    if was_awaiting_code or pending_jobs:
        from evalplat.services.code_execution_service import reconcile_run_after_code_execution

        reconcile_run_after_code_execution(db, run.id)
        run = db.get(Run, run.id) or run
    if task is not None:
        from evalplat.services.task_lifecycle_service import sync_from_runs

        sync_from_runs(db, task.id)
        run = db.get(Run, run.id) or run
    return run


def list_runs(
    db: Session,
    task_id: uuid.UUID | None = None,
    skip: int = 0,
    limit: int = 20,
    status_filter: str | None = None,
) -> tuple[list[Run], int]:
    """列出 Run，支持按任务 ID 和状态筛选。"""
    stmt = select(Run)
    count_stmt = select(func.count()).select_from(Run)

    if task_id:
        stmt = stmt.where(Run.task_id == task_id)
        count_stmt = count_stmt.where(Run.task_id == task_id)
    if status_filter:
        stmt = stmt.where(Run.status == status_filter)
        count_stmt = count_stmt.where(Run.status == status_filter)

    total = db.scalar(count_stmt) or 0
    items = list(db.scalars(stmt.order_by(Run.created_at.desc()).offset(skip).limit(limit)))
    return items, total


def list_requests(
    db: Session,
    run_id: uuid.UUID,
    skip: int = 0,
    limit: int = 20,
) -> tuple[list[Request], int]:
    """列出某 Run 下的请求。"""
    run = db.get(Run, run_id)
    if not run:
        raise NotFoundError("Run 不存在")

    stmt = select(Request).where(Request.run_id == run_id)
    count_stmt = select(func.count()).select_from(Request).where(Request.run_id == run_id)

    total = db.scalar(count_stmt) or 0
    items = list(db.scalars(stmt.order_by(Request.id).offset(skip).limit(limit)))
    return items, total


def get_request(db: Session, run_id: uuid.UUID, request_id: int) -> Request:
    """获取某 Run 下单条 Request，防止跨 Run 读取明细。"""

    run = db.get(Run, run_id)
    if not run:
        raise NotFoundError("Run 不存在")
    request = db.scalar(
        select(Request).where(Request.id == request_id, Request.run_id == run_id)
    )
    if not request:
        raise NotFoundError("Request 不存在")
    return request


def list_attempts(db: Session, request_id: int) -> list[Attempt]:
    """按 attempt_index 顺序返回某 Request 的完整尝试证据。"""

    return list(
        db.scalars(
            select(Attempt)
            .where(Attempt.request_id == request_id)
            .order_by(Attempt.attempt_index)
        )
    )
