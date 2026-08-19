"""质量评测业务逻辑（桩实现）。"""

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from evalplat.core.errors import NotFoundError
from evalplat.models.run import Run


def evaluate_quality(db: Session, run_id: uuid.UUID, scoring_method: str) -> dict:
    """质量评测桩：记录评分请求并返回模拟结果。

    真实实现将在后续 Phase 中接入 EvalScope 评分引擎。
    """
    run = db.get(Run, run_id)
    if not run:
        raise NotFoundError("Run 不存在")

    return {
        "run_id": str(run_id),
        "scoring_method": scoring_method,
        "status": "stub",
        "message": "质量评测桩：真实评分引擎尚未接入",
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }
