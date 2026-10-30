"""Run 重试和历史任务复测 API。"""

import uuid

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from evalplat.api.deps import get_db
from evalplat.api.serializers import serialize_run
from evalplat.schemas.run import RunRead
from evalplat.schemas.task import TaskRead
from evalplat.services import retry_service
from evalplat.services.security_service import redact_sensitive_value

router = APIRouter(tags=["重试与复测"])


@router.post("/runs/{run_id}/retry", response_model=RunRead, status_code=201)
def retry_run(run_id: uuid.UUID, db: Session = Depends(get_db)):
    return serialize_run(retry_service.retry_run(db, run_id))


@router.post("/tasks/{task_id}/retest", status_code=201)
def retest_task(
    task_id: uuid.UUID,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    task, diff = retry_service.retest_task(db, task_id, idempotency_key=idempotency_key)
    task_payload = TaskRead.model_validate(task).model_dump()
    return {"task": TaskRead(**redact_sensitive_value(task_payload)), "differences": redact_sensitive_value(diff)}
