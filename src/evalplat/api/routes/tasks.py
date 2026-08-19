"""评测任务 API 路由。"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from evalplat.api.deps import PaginationParams, get_db
from evalplat.schemas.task import TaskCreate, TaskList, TaskRead, TaskUpdate
from evalplat.services import task_service

router = APIRouter(prefix="/tasks", tags=["任务"])


@router.post("", response_model=TaskRead, status_code=201)
def create_task(payload: TaskCreate, db: Session = Depends(get_db)):
    """创建评测任务。"""
    return task_service.create_task(db, **payload.model_dump())


@router.get("", response_model=TaskList)
def list_tasks(
    pagination: PaginationParams = Depends(),
    status: str | None = Query(None, description="状态筛选"),
    db: Session = Depends(get_db),
):
    """列出任务。"""
    items, total = task_service.list_tasks(
        db, skip=pagination.skip, limit=pagination.limit, status_filter=status
    )
    return TaskList(items=items, total=total)


@router.get("/{task_id}", response_model=TaskRead)
def get_task(task_id: uuid.UUID, db: Session = Depends(get_db)):
    """获取单个任务。"""
    return task_service.get_task(db, task_id)


@router.patch("/{task_id}", response_model=TaskRead)
def update_task(task_id: uuid.UUID, payload: TaskUpdate, db: Session = Depends(get_db)):
    """更新任务（仅 draft 状态）。"""
    return task_service.update_task(db, task_id, **payload.model_dump(exclude_unset=True))


@router.post("/{task_id}/submit", response_model=TaskRead)
def submit_task(task_id: uuid.UUID, db: Session = Depends(get_db)):
    """提交任务：draft → queued。"""
    return task_service.submit_task(db, task_id)


@router.post("/{task_id}/cancel", response_model=TaskRead)
def cancel_task(task_id: uuid.UUID, db: Session = Depends(get_db)):
    """取消任务。"""
    return task_service.cancel_task(db, task_id)
