"""监控 API 路由。"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from evalplat.api.deps import get_db
from evalplat.models.run import Run
from evalplat.models.request import Request
from evalplat.models.attempt import Attempt
from evalplat.models.task import Task
from evalplat.services import task_service
from evalplat.services import metrics_service

router = APIRouter(tags=["监控"])


def _target_rps(snapshot: dict) -> float | None:
    profile = snapshot.get("load_profile") or {}
    if not isinstance(profile, dict):
        return None
    mode = profile.get("mode")
    if mode == "fixed_rps":
        raw_value = profile.get("rps")
        if raw_value is None:
            return None
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None
    if mode == "staircase":
        stages = profile.get("stages")
        if isinstance(stages, list):
            values = []
            for stage in stages:
                if not isinstance(stage, dict):
                    continue
                raw_value = stage.get("rps")
                if raw_value is None:
                    continue
                try:
                    value = float(raw_value)
                except (TypeError, ValueError):
                    continue
                if value > 0:
                    values.append(value)
            return max(values) if values else None
    return None


def _actual_rps(db: Session, run_id: uuid.UUID, started_at: datetime | None) -> float | None:
    if started_at is None:
        return None
    started = started_at.replace(tzinfo=timezone.utc) if started_at.tzinfo is None else started_at
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    if elapsed <= 0:
        return None
    started_count = db.scalar(
        select(func.count()).select_from(Request).where(
            Request.run_id == run_id, Request.started_at.isnot(None)
        )
    ) or 0
    return round(started_count / elapsed, 4)


def _error_summary(db: Session, run_id: uuid.UUID) -> tuple[dict[str, int], dict[str, datetime | None], datetime | None]:
    rows = db.execute(
        select(Attempt.error_category, func.count(), func.max(Attempt.finished_at))
        .join(Request, Request.id == Attempt.request_id)
        .where(Request.run_id == run_id, Attempt.error_category.isnot(None))
        .group_by(Attempt.error_category)
    )
    counts: dict[str, int] = {}
    last_by_category: dict[str, datetime | None] = {}
    latest: datetime | None = None
    for category, count, last_at in rows:
        key = str(category)
        counts[key] = int(count)
        last_by_category[key] = last_at
        if last_at is not None and (latest is None or last_at > latest):
            latest = last_at
    # A failed Request can exist without a persisted Attempt (for example, a
    # scheduling or worker failure). Keep it visible in monitoring without
    # double-counting requests that already have a classified Attempt.
    unclassified = db.execute(
        select(func.count(), func.max(Request.finished_at))
        .where(
            Request.run_id == run_id,
            Request.status == "failed",
            ~exists(
                select(Attempt.id).where(
                    Attempt.request_id == Request.id,
                    Attempt.error_category.isnot(None),
                )
            ),
        )
    ).one()
    unclassified_count, unclassified_last_at = unclassified
    if unclassified_count:
        counts["request_failed"] = int(unclassified_count)
        last_by_category["request_failed"] = unclassified_last_at
        if unclassified_last_at is not None and (latest is None or unclassified_last_at > latest):
            latest = unclassified_last_at
    return counts, last_by_category, latest


@router.get("/runs/{run_id}/metrics")
def run_metrics(run_id: uuid.UUID, db: Session = Depends(get_db)):
    """返回 Run 的延迟、成功率和分位数指标。"""
    return metrics_service.aggregate_run_metrics(db, run_id)


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
    runs = list(db.scalars(select(Run).where(Run.task_id == task_id).order_by(Run.seq, Run.id)))
    run_details = []
    for run in runs:
        snapshot = run.run_snapshot or {}
        planned = int(snapshot.get("request_count", 1) or 1)
        # Quality samples are executed once each; warmup/cold-start phases are
        # only part of the performance scheduler. Counting them for a quality
        # Run makes progress stop below 100% and inflates remaining time.
        if snapshot.get("evaluation_kind", "performance") == "performance" and "warmup_count" in snapshot:
            planned += int(snapshot.get("warmup_count", 0) or 0) + 1
        request_stats = {
            row[0]: row[1]
            for row in db.execute(
                select(Request.status, func.count()).where(Request.run_id == run.id).group_by(Request.status)
            )
        }
        finished_count = sum(request_stats.get(status, 0) for status in ("completed", "failed", "cancelled"))
        current_concurrency = request_stats.get("running", 0)
        target_rps = _target_rps(snapshot)
        actual_rps = _actual_rps(db, run.id, run.started_at)
        error_categories, error_category_last_at, last_error_at = _error_summary(db, run.id)
        queue_position = None
        if run.status == "queued" and run.queued_at is not None:
            queue_position = (db.scalar(
                select(func.count()).select_from(Run).where(
                    Run.status == "queued",
                    Run.queued_at.isnot(None),
                    Run.queued_at < run.queued_at,
                )
            ) or 0) + 1
        remaining_seconds = None
        if finished_count and run.started_at is not None and run.status in {"running", "awaiting_code"}:
            started = run.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            elapsed = max(0.0, (datetime.now(timezone.utc) - started).total_seconds())
            rate = finished_count / elapsed if elapsed > 0 else 0
            if rate > 0:
                remaining_seconds = round(max(0.0, planned - finished_count) / rate, 1)
        run_details.append({
            "run_id": run.id,
            "seq": run.seq,
            "status": run.status,
            "queue_position": queue_position,
            "planned_requests": planned,
            "completed_requests": finished_count,
            "progress": round(min(1.0, finished_count / planned), 4) if planned else None,
            "current_concurrency": current_concurrency,
            "target_rps": target_rps,
            "actual_rps": actual_rps,
            "error_categories": error_categories,
            "error_category_last_at": error_category_last_at,
            "last_error_at": last_error_at,
            "remaining_seconds": remaining_seconds,
            "request_stats": request_stats,
        })

    all_errors: dict[str, int] = {}
    all_last_error_at: datetime | None = None
    target_values = []
    actual_values = []
    for detail in run_details:
        if detail["target_rps"] is not None:
            target_values.append(detail["target_rps"])
        if detail["actual_rps"] is not None:
            actual_values.append(detail["actual_rps"])
        for category, count in detail["error_categories"].items():
            all_errors[category] = all_errors.get(category, 0) + count
        latest = detail["last_error_at"]
        if latest is not None and (all_last_error_at is None or latest > all_last_error_at):
            all_last_error_at = latest

    return {
        "task_id": task_id,
        "task_status": task.status,
        "task_name": task.name,
        "queued_at": task.queued_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "run_stats": run_stats,
        "total_runs": sum(run_stats.values()),
        "target_rps": round(sum(target_values), 4) if target_values else None,
        "actual_rps": round(sum(actual_values), 4) if actual_values else None,
        "error_categories": all_errors,
        "last_error_at": all_last_error_at,
        "runs": run_details,
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
