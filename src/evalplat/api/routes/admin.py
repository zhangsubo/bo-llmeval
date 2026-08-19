"""系统管理 API 路由（Phase 11）。"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evalplat.api.deps import get_db
from evalplat.constants import RunStatus, TaskStatus
from evalplat.models.run import Run
from evalplat.models.system_setting import SystemSetting
from evalplat.models.task import Task

router = APIRouter(tags=["系统管理"])


# ---- Dashboard ----


class TaskStats(BaseModel):
    """任务统计。"""

    queued: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0
    total: int = 0


class DashboardResponse(BaseModel):
    """仪表盘响应体。"""

    tasks: TaskStats
    recent_errors: list[str] = Field(default_factory=list, description="最近错误摘要")


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
        total=total,
    )

    # 最近失败 Run 的 error_summary
    recent_errors = list(
        db.scalars(
            select(Run.error_summary)
            .where(Run.status == RunStatus.FAILED, Run.error_summary.isnot(None))
            .order_by(Run.finished_at.desc())
            .limit(10)
        )
    )

    return DashboardResponse(tasks=task_stats, recent_errors=recent_errors)


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
    components = []

    # 数据库连接检查
    try:
        db.execute(select(func.now()))
        components.append(ComponentHealth(name="database", status="healthy"))
    except Exception as e:
        components.append(ComponentHealth(name="database", status="unhealthy", detail=str(e)))

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
            value=s.value,
            updated_at=s.updated_at.isoformat() if s.updated_at else "",
        ))
    return SettingList(items=result, total=total)


@router.patch("/settings/{key}", response_model=SettingRead)
def update_setting(key: str, payload: SettingUpdate, db: Session = Depends(get_db)):
    """更新指定 key 的系统设置。"""
    setting = db.scalar(
        select(SystemSetting).where(SystemSetting.key == key)
    )
    if not setting:
        setting = SystemSetting(key=key, value=payload.value)
        db.add(setting)
    else:
        setting.value = payload.value
    db.commit()
    db.refresh(setting)
    return SettingRead(
        id=setting.id,
        key=setting.key,
        value=setting.value,
        updated_at=setting.updated_at.isoformat() if setting.updated_at else "",
    )
