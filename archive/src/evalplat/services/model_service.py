"""模型服务（同步 + 手工录入 + CRUD）。"""

import uuid

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from evalplat.core.errors import ConflictError, NotFoundError
from evalplat.core.ssrf import SSRFGuard
from evalplat.models.connection import Connection
from evalplat.models.model_deployment import ModelDeployment
from evalplat.services.protocol_validation import extract_model_ids, openai_api_url
from evalplat.services.security_service import redact_sensitive_text

_MODEL_STATUSES = {"active", "disabled", "archived"}
_MODEL_SOURCES = {"synced", "manual"}
_MODEL_UPDATE_KEYS = {
    "display_name",
    "context_window",
    "max_output_tokens",
    "supports_streaming",
    "supports_non_streaming",
    "status",
    "remark",
}


def sync_models(
    db: Session,
    connection_id: uuid.UUID,
    base_url: str,
    api_key: str,
    ssrf_guard: SSRFGuard | None = None,
    *,
    verify: bool = True,
) -> dict:
    """同步供应商模型列表（PRD §8.2.4）。

    只新增不删除，已存在记录（无论 source）不覆盖。
    /v1/models 不可用不阻断接入。
    """
    guard = ssrf_guard or SSRFGuard()
    target = None
    if hasattr(guard, "resolve_url"):
        allowed, reason, target = guard.resolve_url(base_url)
    else:
        allowed, reason = guard.check_url(base_url)
    if not allowed:
        return {"total": 0, "added": 0, "skipped": 0, "error": f"SSRF 校验失败: {reason}"}
    try:
        url = openai_api_url(base_url, "models")
    except ValueError as exc:
        return {
            "total": 0,
            "added": 0,
            "skipped": 0,
            "error": redact_sensitive_text(str(exc)[:200], api_key),
        }
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        client_options: dict[str, object] = {
            "timeout": 15,
            "follow_redirects": False,
            "verify": verify,
            "trust_env": False,
        }
        if target is not None and hasattr(guard, "transport_for"):
            client_options["transport"] = guard.transport_for(target, verify=verify)
        with httpx.Client(**client_options) as client:
            resp = client.get(url, headers=headers)
        if resp.status_code != 200:
            result = {"total": 0, "added": 0, "skipped": 0, "error": f"HTTP {resp.status_code}"}
            if 300 <= resp.status_code < 400:
                result["error"] = "3xx 重定向（平台不允许跟随重定向）"
                location = resp.headers.get("location")
                if location:
                    result["redirect_location"] = redact_sensitive_text(str(location)[:500], api_key)
            return result
    except httpx.HTTPError as e:
        return {"total": 0, "added": 0, "skipped": 0, "error": redact_sensitive_text(str(e)[:200], api_key)}

    try:
        model_ids = extract_model_ids(resp.json())
    except (TypeError, ValueError) as exc:
        return {
            "total": 0,
            "added": 0,
            "skipped": 0,
            "error": f"模型目录响应格式无效: {redact_sensitive_text(str(exc)[:200], api_key)}",
        }

    # Serialize model catalog writes with manual creation and API-key changes.
    # The database unique constraint remains the final race-proof guard.
    db.scalar(
        select(Connection)
        .where(Connection.id == connection_id)
        .with_for_update()
    )
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

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if "uq_model_deployments_connection_model" not in str(exc.orig) and "UNIQUE constraint failed" not in str(exc.orig):
            raise
        # A concurrent writer may have won the unique key between the
        # snapshot query and INSERT. Report an idempotent skip for sync rather
        # than leaking a 500 from the API route.
        return {"total": len(model_ids), "added": 0, "skipped": len(model_ids)}
    return {"total": len(model_ids), "added": added, "skipped": len(model_ids) - added}


def create_model_deployment(db: Session, connection_id: uuid.UUID, source: str, **kwargs) -> ModelDeployment:
    """创建模型部署（手工录入或同步）。"""
    if source not in _MODEL_SOURCES:
        raise ValueError("模型部署来源必须为 synced 或 manual")
    connection = db.scalar(
        select(Connection)
        .where(Connection.id == connection_id)
        .with_for_update()
    )
    if connection is None:
        raise NotFoundError("连接不存在")
    if connection.status != "active":
        raise ValueError("连接未启用，无法创建模型部署")
    model_id = str(kwargs.get("model_id") or "").strip()
    if not model_id:
        raise ValueError("model_id 不能为空")
    kwargs["model_id"] = model_id
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
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if "uq_model_deployments_connection_model" not in str(exc.orig) and "UNIQUE constraint failed" not in str(exc.orig):
            raise
        existing = db.scalar(
            select(ModelDeployment).where(
                ModelDeployment.connection_id == connection_id,
                ModelDeployment.model_id == model_id,
            )
        )
        if existing is not None:
            raise ConflictError("该连接下已存在同名模型部署") from None
        raise
    db.refresh(md)
    return md


def get_model_deployment(db: Session, deployment_id: uuid.UUID) -> ModelDeployment:
    md = db.get(ModelDeployment, deployment_id)
    if not md:
        raise NotFoundError("模型部署不存在")
    return md


def list_model_deployments(
    db: Session,
    connection_id: uuid.UUID | None = None,
    supplier_id: uuid.UUID | None = None,
    skip: int = 0,
    limit: int = 20,
    status_filter: str | None = None,
    keyword: str | None = None,
) -> tuple[list[ModelDeployment], int]:
    stmt = select(ModelDeployment)
    count_stmt = select(func.count()).select_from(ModelDeployment)

    if connection_id:
        stmt = stmt.where(ModelDeployment.connection_id == connection_id)
        count_stmt = count_stmt.where(ModelDeployment.connection_id == connection_id)
    if supplier_id:
        stmt = stmt.join(Connection, Connection.id == ModelDeployment.connection_id).where(
            Connection.supplier_id == supplier_id
        )
        count_stmt = count_stmt.join(Connection, Connection.id == ModelDeployment.connection_id).where(
            Connection.supplier_id == supplier_id
        )
    if status_filter:
        stmt = stmt.where(ModelDeployment.status == status_filter)
        count_stmt = count_stmt.where(ModelDeployment.status == status_filter)
    if keyword and (keyword := keyword.strip()):
        like = f"%{keyword}%"
        condition = or_(
            ModelDeployment.model_id.ilike(like),
            ModelDeployment.display_name.ilike(like),
        )
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    total = db.scalar(count_stmt) or 0
    items = list(db.scalars(stmt.order_by(ModelDeployment.created_at.desc()).offset(skip).limit(limit)))
    return items, total


def update_model_deployment(db: Session, deployment_id: uuid.UUID, **kwargs) -> ModelDeployment:
    md = get_model_deployment(db, deployment_id)
    status = kwargs.get("status")
    if status is not None and status not in _MODEL_STATUSES:
        raise ValueError("模型部署状态必须为 active、disabled 或 archived")
    for key, value in kwargs.items():
        if key in _MODEL_UPDATE_KEYS and value is not None:
            setattr(md, key, value)
    db.commit()
    db.refresh(md)
    return md
