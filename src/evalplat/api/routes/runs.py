"""评测 Run 查询 API 路由。"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from evalplat.api.deps import PaginationParams, get_db
from evalplat.schemas.run import RequestList, RunList, RunRead
from evalplat.services import run_service

router = APIRouter(tags=["评测运行"])


@router.get("/tasks/{task_id}/runs", response_model=RunList)
def list_runs(
    task_id: uuid.UUID,
    pagination: PaginationParams = Depends(),
    status: str | None = Query(None, description="状态筛选"),
    db: Session = Depends(get_db),
):
    """列出某任务下的 Run。"""
    items, total = run_service.list_runs(
        db, task_id=task_id, skip=pagination.skip, limit=pagination.limit, status_filter=status
    )
    return RunList(items=items, total=total)


@router.get("/runs/{run_id}", response_model=RunRead)
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db)):
    """获取单个 Run。"""
    return run_service.get_run(db, run_id)


@router.get("/runs/{run_id}/requests", response_model=RequestList)
def list_requests(
    run_id: uuid.UUID,
    pagination: PaginationParams = Depends(),
    db: Session = Depends(get_db),
):
    """列出某 Run 下的请求。"""
    items, total = run_service.list_requests(
        db, run_id=run_id, skip=pagination.skip, limit=pagination.limit
    )
    return RequestList(items=items, total=total)
