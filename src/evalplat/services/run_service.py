"""评测 Run 查询业务逻辑。"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evalplat.core.errors import NotFoundError
from evalplat.models.request import Request
from evalplat.models.run import Run


def get_run(db: Session, run_id: uuid.UUID) -> Run:
    """获取单个 Run。"""
    run = db.get(Run, run_id)
    if not run:
        raise NotFoundError("Run 不存在")
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

    stmt = select(Request).where(Request.run_id == str(run_id))
    count_stmt = select(func.count()).select_from(Request).where(Request.run_id == str(run_id))

    total = db.scalar(count_stmt) or 0
    items = list(db.scalars(stmt.order_by(Request.id).offset(skip).limit(limit)))
    return items, total
