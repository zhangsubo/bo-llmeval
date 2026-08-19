"""报告与导出 API 路由（Phase 9，桩实现）。"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from evalplat.api.deps import get_db
from evalplat.services import report_service

router = APIRouter(tags=["报告与导出"])


@router.post("/tasks/{task_id}/report", status_code=200)
def generate_report(task_id: uuid.UUID, db: Session = Depends(get_db)):
    """为指定任务生成报告（桩）。"""
    return report_service.generate_report(db, task_id)


@router.get("/runs/{run_id}/export", status_code=200)
def export_csv(run_id: uuid.UUID, db: Session = Depends(get_db)):
    """导出指定 Run 的 CSV 数据（桩）。"""
    return report_service.export_csv(db, run_id)
