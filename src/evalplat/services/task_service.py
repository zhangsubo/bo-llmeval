"""评测任务业务逻辑。"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evalplat.core.errors import ConflictError, NotFoundError
from evalplat.models.task import Task


def create_task(db: Session, **kwargs) -> Task:
    """创建任务（状态默认 draft）。"""
    task = Task(**kwargs)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def get_task(db: Session, task_id: uuid.UUID) -> Task:
    """获取单个任务。"""
    task = db.get(Task, task_id)
    if not task:
        raise NotFoundError("任务不存在")
    return task


def list_tasks(
    db: Session,
    skip: int = 0,
    limit: int = 20,
    status_filter: str | None = None,
) -> tuple[list[Task], int]:
    """列出任务，支持状态筛选。"""
    stmt = select(Task)
    count_stmt = select(func.count()).select_from(Task)

    if status_filter:
        stmt = stmt.where(Task.status == status_filter)
        count_stmt = count_stmt.where(Task.status == status_filter)

    total = db.scalar(count_stmt) or 0
    items = list(db.scalars(stmt.order_by(Task.created_at.desc()).offset(skip).limit(limit)))
    return items, total


def update_task(db: Session, task_id: uuid.UUID, **kwargs) -> Task:
    """更新任务（仅 draft 状态可改）。"""
    task = get_task(db, task_id)
    if task.status != "draft":
        raise ConflictError("仅 draft 状态的任务可编辑")
    for key, value in kwargs.items():
        if value is not None and hasattr(task, key):
            setattr(task, key, value)
    db.commit()
    db.refresh(task)
    return task


def submit_task(db: Session, task_id: uuid.UUID) -> Task:
    """提交任务：draft → queued。"""
    task = get_task(db, task_id)
    if task.status != "draft":
        raise ConflictError("仅 draft 状态的任务可提交")
    task.status = "queued"
    task.queued_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(task)
    return task


def cancel_task(db: Session, task_id: uuid.UUID) -> Task:
    """取消任务：draft/queued → cancelled。"""
    task = get_task(db, task_id)
    if task.status not in ("draft", "queued"):
        raise ConflictError("仅 draft 或 queued 状态的任务可取消")
    task.status = "cancelled"
    db.commit()
    db.refresh(task)
    return task
