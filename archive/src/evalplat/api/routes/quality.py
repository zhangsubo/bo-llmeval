"""质量评测 API 路由。"""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from evalplat.api.deps import get_db
from evalplat.services import quality_service
from evalplat.services.security_service import redact_sensitive_value

router = APIRouter(tags=["质量评测"])


class EvaluateRequest(BaseModel):
    """质量评测请求体。"""

    scoring_method: str = Field(..., description="评分方法")
    tolerance: float = Field(1e-6, ge=0, description="数值容差（numeric_tolerance 使用）")


@router.post("/runs/{run_id}/evaluate")
def evaluate_quality(
    run_id: uuid.UUID,
    payload: EvaluateRequest,
    db: Session = Depends(get_db),
):
    """触发确定性客观质量评测。"""
    return redact_sensitive_value(
        quality_service.evaluate_quality(db, run_id, payload.scoring_method, payload.tolerance)
    )
