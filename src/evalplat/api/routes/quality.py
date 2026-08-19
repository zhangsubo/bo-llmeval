"""质量评测 API 路由（桩实现）。"""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from evalplat.api.deps import get_db
from evalplat.services import quality_service

router = APIRouter(tags=["质量评测"])


class EvaluateRequest(BaseModel):
    """质量评测请求体。"""

    scoring_method: str = Field(..., description="评分方法")


@router.post("/runs/{run_id}/evaluate")
def evaluate_quality(
    run_id: uuid.UUID,
    payload: EvaluateRequest,
    db: Session = Depends(get_db),
):
    """触发质量评测（桩）。"""
    return quality_service.evaluate_quality(db, run_id, payload.scoring_method)
