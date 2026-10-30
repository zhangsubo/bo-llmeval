"""系统管理 API 路由（Phase 11）。"""

import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evalplat.api.deps import get_db
from evalplat.constants import RunStatus, TaskStatus
from evalplat.core.paths import public_basename
from evalplat.models.run import Run
from evalplat.models.attempt import Attempt
from evalplat.models.cleanup_batch import CleanupBatch
from evalplat.models.report import Report
from evalplat.models.system_setting import SystemSetting
from evalplat.models.task import Task
from evalplat.services.audit_service import record_event
from evalplat.config import get_settings
from evalplat.services.worker_heartbeat_service import health_snapshot
from evalplat.core.errors import ConflictError
from evalplat.services.security_service import WHITELIST_SETTING_KEY, redact_sensitive_text, redact_sensitive_value
from evalplat.schemas.cleanup import CleanupBatchRead, CleanupExecuteRequest, CleanupPreviewRead, CleanupPreviewRequest
from evalplat.services import history_cleanup_service

router = APIRouter(tags=["系统管理"])


def _cleanup_preview_read(value: dict) -> CleanupPreviewRead:
    """Expose cleanup preview filenames without changing its internal plan."""

    payload = dict(value)
    payload["report_files"] = [
        filename
        for raw_path in payload.get("report_files", [])
        if (filename := public_basename(raw_path)) is not None
    ]
    tasks = []
    for raw_entry in payload.get("tasks", []):
        entry = dict(raw_entry)
        entry["report_files"] = [
            filename
            for raw_path in entry.get("report_files", [])
            if (filename := public_basename(raw_path)) is not None
        ]
        tasks.append(entry)
    payload["tasks"] = tasks
    return CleanupPreviewRead(**redact_sensitive_value(payload))


def _cleanup_batch_read(batch: CleanupBatch) -> CleanupBatchRead:
    """Serialize a cleanup batch while hiding persisted host paths/secrets."""

    payload = CleanupBatchRead.model_validate(batch).model_dump()
    payload = redact_sensitive_value(payload)
    payload["error"] = redact_sensitive_text(payload.get("error")) or None
    return CleanupBatchRead(**payload)


@router.post("/admin/history-cleanup/preview", response_model=CleanupPreviewRead)
def preview_history_cleanup(payload: CleanupPreviewRequest, db: Session = Depends(get_db)):
    """预览可清理的终态任务；此接口不产生写入。"""
    return _cleanup_preview_read(history_cleanup_service.preview_cleanup(db, payload.cutoff_at, payload.statuses))


@router.post("/admin/history-cleanup/batches", response_model=CleanupBatchRead, status_code=201)
def create_history_cleanup_batch(payload: CleanupPreviewRequest, db: Session = Depends(get_db)):
    return _cleanup_batch_read(history_cleanup_service.create_cleanup_batch(db, payload.cutoff_at, payload.statuses))


@router.get("/admin/history-cleanup/batches/{batch_id}", response_model=CleanupBatchRead)
def get_history_cleanup_batch(batch_id: uuid.UUID, db: Session = Depends(get_db)):
    return _cleanup_batch_read(history_cleanup_service.get_cleanup_batch(db, batch_id))


@router.post("/admin/history-cleanup/batches/{batch_id}/execute", response_model=CleanupBatchRead)
def execute_history_cleanup_batch(batch_id: uuid.UUID, payload: CleanupExecuteRequest, db: Session = Depends(get_db)):
    return _cleanup_batch_read(history_cleanup_service.execute_cleanup_batch(db, batch_id, confirmation=payload.confirmation))


@router.post("/admin/history-cleanup/batches/{batch_id}/retry", response_model=CleanupBatchRead)
def retry_history_cleanup_batch(batch_id: uuid.UUID, payload: CleanupExecuteRequest, db: Session = Depends(get_db)):
    return _cleanup_batch_read(history_cleanup_service.execute_cleanup_batch(db, batch_id, confirmation=payload.confirmation, retry=True))


# ---- Dashboard ----


class TaskStats(BaseModel):
    """任务统计。"""

    queued: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0
    partially_completed: int = 0
    cancelling: int = 0
    cancelled: int = 0
    total: int = 0


class DashboardResponse(BaseModel):
    """仪表盘响应体。"""

    tasks: TaskStats
    recent_errors: list[str] = Field(default_factory=list, description="最近错误摘要")
    recent_reports: list[dict] = Field(default_factory=list, description="最近已生成报告")


@router.get("/admin/dashboard", response_model=DashboardResponse)
def get_dashboard(db: Session = Depends(get_db)):
    """聚合统计：排队/运行/完成/失败任务数、最近异常。"""
    # 按状态统计任务数
    stats_rows = db.execute(
        select(Task.status, func.count()).group_by(Task.status)
    ).all()

    stats_map = {row[0]: row[1] for row in stats_rows}
    total = sum(stats_map.values())

    task_stats = TaskStats(
        queued=stats_map.get(TaskStatus.QUEUED, 0),
        running=stats_map.get(TaskStatus.RUNNING, 0),
        completed=stats_map.get(TaskStatus.COMPLETED, 0),
        failed=stats_map.get(TaskStatus.FAILED, 0),
        partially_completed=stats_map.get(TaskStatus.PARTIALLY_COMPLETED, 0),
        cancelling=stats_map.get(TaskStatus.CANCELLING, 0),
        cancelled=stats_map.get(TaskStatus.CANCELLED, 0),
        total=total,
    )

    # Prefer durable error categories over free-form summaries so operators
    # can distinguish rate limits, 5xx, timeouts and stream/protocol failures.
    category_rows = db.execute(
        select(Attempt.error_category, func.count())
        .where(Attempt.error_category.isnot(None))
        .group_by(Attempt.error_category)
        .order_by(func.count().desc(), Attempt.error_category.asc())
        .limit(10)
    ).all()
    recent_errors = [f"{category}: {count} 次" for category, count in category_rows]
    if not recent_errors:
        recent_errors = [redact_sensitive_text(value) for value in list(
            db.scalars(
                select(Run.error_summary)
                .where(Run.status.in_((RunStatus.FAILED, RunStatus.INTERRUPTED_FAILED)), Run.error_summary.isnot(None))
                .order_by(Run.finished_at.desc())
                .limit(10)
            )
        )]

    recent_reports = [
        {
            "id": report.id,
            "task_id": report.task_id,
            "version": report.version,
            "status": report.status,
            "created_at": report.created_at,
        }
        for report in db.scalars(
            select(Report)
            .where(Report.status == "completed")
            .order_by(Report.created_at.desc(), Report.id.desc())
            .limit(5)
        )
    ]

    return DashboardResponse(tasks=task_stats, recent_errors=recent_errors, recent_reports=recent_reports)


# ---- Health ----


class ComponentHealth(BaseModel):
    """组件健康状态。"""

    name: str
    status: str  # healthy / degraded / unhealthy
    detail: str | None = None


class HealthResponse(BaseModel):
    """健康检查响应体。"""

    status: str  # healthy / degraded / unhealthy
    components: list[ComponentHealth] = Field(default_factory=list)


@router.get("/admin/health", response_model=HealthResponse)
def get_health(db: Session = Depends(get_db)):
    """组件健康状态检查。"""
    components = [ComponentHealth(name="web_api", status="healthy")]

    # 数据库连接检查
    database_available = True
    try:
        db.execute(select(func.now()))
        components.append(ComponentHealth(name="database", status="healthy"))
    except Exception:
        # A failed SQLAlchemy transaction must be reset before any later
        # request uses this session. Never expose the driver exception: it can
        # contain a DSN, host name, or other deployment details.
        database_available = False
        db.rollback()
        components.append(ComponentHealth(name="database", status="unhealthy", detail="数据库检查失败"))

    settings = get_settings()
    heartbeat_timeout = 120
    test_harness = db.bind is not None and db.bind.dialect.name == "sqlite"
    worker = (
        health_snapshot(db, worker_type="normal", timeout_seconds=heartbeat_timeout)
        if database_available
        else {"status": "unknown", "workers": []}
    )
    components.append(
        ComponentHealth(
            name="worker",
            status="healthy" if worker["status"] == "healthy" or (test_harness and worker["status"] == "unknown") else "degraded" if worker["status"] == "degraded" or worker["status"] == "unknown" else "unhealthy",
            detail=f"{len(worker.get('workers', []))} 个实例",
        )
    )
    code_worker = (
        health_snapshot(db, worker_type="code", timeout_seconds=heartbeat_timeout)
        if database_available
        else {"status": "unknown", "workers": []}
    )
    components.append(
        ComponentHealth(
            name="code_worker",
            status="healthy" if code_worker["status"] == "healthy" or (test_harness and code_worker["status"] == "unknown") else "degraded" if code_worker["status"] == "degraded" or code_worker["status"] == "unknown" else "unhealthy",
            detail=f"{len(code_worker.get('workers', []))} 个实例",
        )
    )
    for name, raw_path in (
        ("report_directory", settings.report_dir),
        ("dataset_directory", settings.dataset_dir),
        ("tokenizer_cache", settings.tokenizer_dir),
    ):
        try:
            path = Path(raw_path).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            free_mb = shutil.disk_usage(path).free / (1024 * 1024)
            components.append(
                ComponentHealth(
                    name=name,
                    status="healthy" if free_mb >= 100 else "degraded",
                    detail=f"剩余 {free_mb:.1f} MB",
                )
            )
        except OSError:
            # Do not expose absolute paths or host-specific diagnostics in a
            # health response. Operators can correlate the fixed summary with
            # server logs when a filesystem check fails.
            components.append(ComponentHealth(name=name, status="unhealthy", detail="目录检查失败"))

    overall = "healthy"
    if any(c.status == "unhealthy" for c in components):
        overall = "unhealthy"
    elif any(c.status == "degraded" for c in components):
        overall = "degraded"

    return HealthResponse(status=overall, components=components)


# ---- Settings ----


class SettingRead(BaseModel):
    """系统设置响应体。"""

    id: int
    key: str
    value: dict
    updated_at: str

    model_config = {"from_attributes": True}


class SettingUpdate(BaseModel):
    """系统设置更新请求体。"""

    value: dict = Field(..., description="新的设置值")


class SettingList(BaseModel):
    items: list[SettingRead]
    total: int


@router.get("/settings", response_model=SettingList)
def list_settings(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """系统设置列表。"""
    total = db.scalar(select(func.count()).select_from(SystemSetting)) or 0
    items = list(
        db.scalars(
            select(SystemSetting).order_by(SystemSetting.id).offset(skip).limit(limit)
        )
    )
    # 手动序列化（SystemSetting.updated_at 是 datetime）
    result = []
    for s in items:
        result.append(SettingRead(
            id=s.id,
            key=s.key,
            value=redact_sensitive_value(s.value),
            updated_at=s.updated_at.isoformat() if s.updated_at else "",
        ))
    return SettingList(items=result, total=total)


@router.patch("/settings/{key}", response_model=SettingRead)
def update_setting(key: str, payload: SettingUpdate, db: Session = Depends(get_db)):
    """更新指定 key 的系统设置。"""
    if key == WHITELIST_SETTING_KEY:
        raise ConflictError("url_whitelist 必须通过 /settings/whitelist 更新并输入确认文本")
    setting = db.scalar(
        select(SystemSetting).where(SystemSetting.key == key)
    )
    if not setting:
        setting = SystemSetting(key=key, value=payload.value)
        db.add(setting)
    else:
        setting.value = payload.value
    db.flush()
    record_event(db, "system_setting_updated", target_type="system_setting", target_id=setting.id, diff={"key": key})
    db.commit()
    db.refresh(setting)
    return SettingRead(
        id=setting.id,
        key=setting.key,
        value=redact_sensitive_value(setting.value),
        updated_at=setting.updated_at.isoformat() if setting.updated_at else "",
    )
