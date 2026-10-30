"""代码执行作业和结果查询 API。"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from evalplat.api.deps import PaginationParams, get_db
from evalplat.schemas.code_execution import CodeExecutionList, CodeExecutionRead
from evalplat.services.code_execution_service import get_code_execution, list_code_executions

router = APIRouter(tags=["代码评测"])


@router.get("/runs/{run_id}/code-executions", response_model=CodeExecutionList)
def list_run_code_executions(
    run_id: uuid.UUID,
    pagination: PaginationParams = Depends(),
    db: Session = Depends(get_db),
):
    items, total = list_code_executions(
        db, run_id, skip=pagination.skip, limit=pagination.limit
    )
    return CodeExecutionList(items=items, total=total)


@router.get("/code-executions/{execution_id}", response_model=CodeExecutionRead)
def get_code_execution_detail(execution_id: uuid.UUID, db: Session = Depends(get_db)):
    return get_code_execution(db, execution_id)
