"""模型服务（同步 + 手工录入 + CRUD）。"""

import uuid

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evalplat.core.errors import ConflictError, NotFoundError
from evalplat.models.connection import Connection
from evalplat.models.model_deployment import ModelDeployment


def sync_models(db: Session, connection_id: uuid.UUID, base_url: str, api_key: str) -> dict:
    """同步供应商模型列表（PRD §8.2.4）。

    只新增不删除，已存在记录（无论 source）不覆盖。
    /v1/models 不可用不阻断接入。
    """
    url = f"{base_url}/v1/models"
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            resp = client.get(url, headers=headers)
        if resp.status_code != 200:
            return {"total": 0, "added": 0, "skipped": 0, "error": f"HTTP {resp.status_code}"}
    except httpx.HTTPError as e:
        return {"total": 0, "added": 0, "skipped": 0, "error": str(e)[:200]}

    data = resp.json()
    model_ids = [m["id"] for m in data.get("data", [])]

    # 查询已有记录
    existing = db.scalars(
        select(ModelDeployment).where(ModelDeployment.connection_id == connection_id)
    ).all()
    existing_ids = {m.model_id: m for m in existing}

    added = 0
    for model_id in model_ids:
        if model_id not in existing_ids:
            db.add(ModelDeployment(
                connection_id=connection_id,
                model_id=model_id,
                display_name=model_id,
                source="synced",
                status="active",
            ))
            added += 1

    db.commit()
    return {"total": len(model_ids), "added": added, "skipped": len(model_ids) - added}


def create_model_deployment(db: Session, connection_id: uuid.UUID, source: str, **kwargs) -> ModelDeployment:
    """创建模型部署（手工录入或同步）。"""
    # 检查唯一性
    existing = db.scalar(
        select(ModelDeployment).where(
            ModelDeployment.connection_id == connection_id,
            ModelDeployment.model_id == kwargs.get("model_id"),
        )
    )
    if existing:
        raise ConflictError("该连接下已存在同名模型部署")

    md = ModelDeployment(connection_id=connection_id, source=source, status="active", **kwargs)
    db.add(md)
    db.commit()
    db.refresh(md)
    return md


def get_model_deployment(db: Session, deployment_id: uuid.UUID) -> ModelDeployment:
    md = db.get(ModelDeployment, deployment_id)
    if not md:
        raise NotFoundError("模型部署不存在")
    return md


def list_model_deployments(
    db: Session, connection_id: uuid.UUID | None = None, skip: int = 0, limit: int = 20, status_filter: str | None = None
) -> tuple[list[ModelDeployment], int]:
    stmt = select(ModelDeployment)
    count_stmt = select(func.count()).select_from(ModelDeployment)

    if connection_id:
        stmt = stmt.where(ModelDeployment.connection_id == connection_id)
        count_stmt = count_stmt.where(ModelDeployment.connection_id == connection_id)
    if status_filter:
        stmt = stmt.where(ModelDeployment.status == status_filter)
        count_stmt = count_stmt.where(ModelDeployment.status == status_filter)

    total = db.scalar(count_stmt) or 0
    items = list(db.scalars(stmt.order_by(ModelDeployment.created_at.desc()).offset(skip).limit(limit)))
    return items, total


def update_model_deployment(db: Session, deployment_id: uuid.UUID, **kwargs) -> ModelDeployment:
    md = get_model_deployment(db, deployment_id)
    for key, value in kwargs.items():
        if value is not None and hasattr(md, key):
            setattr(md, key, value)
    db.commit()
    db.refresh(md)
    return md
