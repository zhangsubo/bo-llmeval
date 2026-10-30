"""评测 Run 查询 API 路由。"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from evalplat.api.deps import PaginationParams, get_db
from evalplat.api.serializers import serialize_run
from evalplat.schemas.run import AttemptRead, RequestDetail, RequestList, RequestSummary, RunList, RunRead
from evalplat.services import run_service
from evalplat.services.security_service import redact_sensitive_text, redact_sensitive_value

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
    return RunList(items=[serialize_run(item) for item in items], total=total)


@router.get("/runs/{run_id}", response_model=RunRead)
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db)):
    """获取单个 Run。"""
    return serialize_run(run_service.get_run(db, run_id))


@router.post("/runs/{run_id}/cancel", response_model=RunRead)
def cancel_run(run_id: uuid.UUID, db: Session = Depends(get_db)):
    """取消单个 Run；不会影响同一 Task 的其他 Run。"""

    return serialize_run(run_service.cancel_run(db, run_id))


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
    return RequestList(
        items=[RequestSummary.model_validate(item) for item in items],
        total=total,
    )


@router.get("/runs/{run_id}/requests/{request_id}", response_model=RequestDetail)
def get_request(run_id: uuid.UUID, request_id: int, db: Session = Depends(get_db)):
    """按需读取单条 Request 及其完整 Attempt 证据。"""

    request = run_service.get_request(db, run_id, request_id)
    base = RequestSummary.model_validate(request).model_dump()
    input_messages = redact_sensitive_value(request.input_messages)
    quality_result = redact_sensitive_value(request.quality_result)
    attempts = []
    for attempt in run_service.list_attempts(db, request.id):
        payload = AttemptRead.model_validate(attempt).model_dump()
        attempts.append(AttemptRead(**redact_sensitive_value(payload)))
    return RequestDetail(
        **base,
        input_messages=input_messages,
        quality_result=quality_result,
        attempts=attempts,
    )
