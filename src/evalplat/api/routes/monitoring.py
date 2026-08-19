"""监控 API 路由。"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evalplat.api.deps import get_db
from evalplat.models.run import Run
from evalplat.models.task import Task
from evalplat.schemas.task import TaskRead
from evalplat.services import task_service

router = APIRouter(tags=["监控"])


@router.get("/tasks/{task_id}/status")
def task_status(task_id: uuid.UUID, db: Session = Depends(get_db)):
    """聚合任务及其 Run 状态。"""
    task = task_service.get_task(db, task_id)

    # 统计各状态 Run 数量
    stmt = (
        select(Run.status, func.count())
        .where(Run.task_id == task_id)
        .group_by(Run.status)
    )
    run_stats = {row[0]: row[1] for row in db.execute(stmt)}

    return {
        "task_id": task_id,
        "task_status": task.status,
        "task_name": task.name,
        "queued_at": task.queued_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "run_stats": run_stats,
        "total_runs": sum(run_stats.values()),
    }


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    """仪表盘：排队/运行/完成/失败统计。"""
    statuses = ["queued", "running", "completed", "failed"]
    task_counts: dict[str, int] = {}
    run_counts: dict[str, int] = {}

    for s in statuses:
        task_counts[s] = db.scalar(
            select(func.count()).select_from(Task).where(Task.status == s)
        ) or 0
        run_counts[s] = db.scalar(
            select(func.count()).select_from(Run).where(Run.status == s)
        ) or 0

    return {
        "tasks": task_counts,
        "runs": run_counts,
    }
