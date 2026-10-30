"""评测任务 API 路由。"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from evalplat.api.deps import PaginationParams, get_db, get_ssrf_guard
from evalplat.core.ssrf import SSRFGuard
from evalplat.schemas.task import TaskCreate, TaskList, TaskRead, TaskUpdate
from evalplat.services import task_service
from evalplat.services.security_service import redact_sensitive_value

router = APIRouter(prefix="/tasks", tags=["任务"])


def _task_read(task: object) -> TaskRead:
    payload = TaskRead.model_validate(task).model_dump()
    return TaskRead(**redact_sensitive_value(payload))


@router.post("", response_model=TaskRead, status_code=201)
def create_task(payload: TaskCreate, db: Session = Depends(get_db)):
    """创建评测任务。"""
    return _task_read(task_service.create_task(db, **payload.model_dump(exclude_unset=True)))


@router.get("", response_model=TaskList)
def list_tasks(
    pagination: PaginationParams = Depends(),
    status: str | None = Query(None, description="状态筛选"),
    keyword: str | None = Query(None, description="任务名称或 ID"),
    supplier_id: uuid.UUID | None = Query(None),
    model_deployment_id: uuid.UUID | None = Query(None),
    comparison_group_id: uuid.UUID | None = Query(None),
    template_id: uuid.UUID | None = Query(None),
    template_version_id: uuid.UUID | None = Query(None),
    test_type: str | None = Query(None),
    created_from: datetime | None = Query(None),
    created_to: datetime | None = Query(None),
    db: Session = Depends(get_db),
):
    """列出任务。"""
    items, total = task_service.list_tasks(
        db,
        skip=pagination.skip,
        limit=pagination.limit,
        status_filter=status,
        keyword=keyword,
        supplier_id=supplier_id,
        model_deployment_id=model_deployment_id,
        comparison_group_id=comparison_group_id,
        template_id=template_id,
        template_version_id=template_version_id,
        test_type=test_type,
        created_from=created_from,
        created_to=created_to,
    )
    return TaskList(items=[_task_read(item) for item in items], total=total)


@router.get("/{task_id}", response_model=TaskRead)
def get_task(task_id: uuid.UUID, db: Session = Depends(get_db)):
    """获取单个任务。"""
    return _task_read(task_service.get_task(db, task_id))


@router.patch("/{task_id}", response_model=TaskRead)
def update_task(task_id: uuid.UUID, payload: TaskUpdate, db: Session = Depends(get_db)):
    """更新任务（仅 draft 状态）。"""
    return _task_read(task_service.update_task(db, task_id, **payload.model_dump(exclude_unset=True)))


@router.post("/{task_id}/submit", response_model=TaskRead)
def submit_task(
    task_id: uuid.UUID,
    db: Session = Depends(get_db),
    ssrf_guard: SSRFGuard = Depends(get_ssrf_guard),
):
    """提交任务：draft → queued。"""
    return _task_read(task_service.submit_task(db, task_id, ssrf_guard=ssrf_guard))


@router.post("/{task_id}/cancel", response_model=TaskRead)
def cancel_task(task_id: uuid.UUID, db: Session = Depends(get_db)):
    """取消任务。"""
    return _task_read(task_service.cancel_task(db, task_id))
