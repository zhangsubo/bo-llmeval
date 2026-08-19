"""结果对比 API 路由（Phase 8）。"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from evalplat.api.deps import get_db
from evalplat.schemas.comparison import ComparisonCreate, ComparisonResult, DifferenceItem, RunSummary
from evalplat.services import comparison_service

router = APIRouter(prefix="/comparisons", tags=["结果对比"])


@router.post("", response_model=ComparisonResult)
def create_comparison(payload: ComparisonCreate, db: Session = Depends(get_db)):
    """创建对比：检查多个 Run 的可比性并返回对比结果。"""
    result = comparison_service.compare_runs(db, payload.run_ids)
    return ComparisonResult(
        runs=[RunSummary.model_validate(r) for r in result["runs"]],
        comparability=result["comparability"],
        differences=[DifferenceItem(**d) for d in result["differences"]],
    )
