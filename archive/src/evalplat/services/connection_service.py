"""连接业务逻辑（含 Fernet 加密 + SSRF 校验）。"""

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from evalplat.core.crypto import CryptoService
from evalplat.core.errors import NotFoundError
from evalplat.core.ssrf import SSRFGuard
from evalplat.models.connection import Connection
from evalplat.models.supplier import Supplier
from evalplat.services.audit_service import record_event


_LIMIT_KEYS = {
    "concurrency",
    "max_concurrency",
    "rps",
    "max_rps",
    "rpm",
    "max_rpm",
    "tpm",
    "max_tpm",
    "max_requests",
    "max_request_count",
    "max_total_tokens",
    "max_duration_seconds",
}

_CONNECTION_STATUSES = {"active", "disabled", "archived"}
_UPDATE_KEYS = {
    "name",
    "region",
    "service_tier",
    "tls_skip_verify",
    "timeouts",
    "limits",
    "status",
    "remark",
}
_CLEARABLE_UPDATE_KEYS = {"region", "service_tier", "timeouts", "limits", "remark"}


def _validate_limits(limits: dict | None) -> None:
    """Validate known runtime limits before persisting connection config."""

    if limits is None:
        return
    if not isinstance(limits, dict):
        raise ValueError("连接限额必须为对象")
    for key, raw_value in limits.items():
        if key not in _LIMIT_KEYS or raw_value is None:
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            raise ValueError(f"连接限额 {key} 必须为数字") from None
        if value <= 0:
            raise ValueError(f"连接限额 {key} 必须大于 0")


def _normalize_base_url(url: str) -> str:
    """规范化 Base URL（PRD §8.2.2）。"""
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"不允许的协议: {parsed.scheme}")
    if parsed.username or parsed.password:
        raise ValueError("URL 中不允许嵌入用户名/密码")
    if not parsed.hostname:
        raise ValueError("URL 缺少主机名")

    # 去除末尾 /，主机名小写化
    hostname = parsed.hostname.lower()
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]  # 去除 /v1 后缀

    # IPv6 literals must retain brackets in the URL authority component.
    authority_host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = f"{authority_host}:{parsed.port}" if parsed.port else authority_host
    return urlunparse((parsed.scheme, netloc, path, "", "", ""))


def create_connection(
    db: Session,
    supplier_id: uuid.UUID,
    name: str,
    base_url: str,
    api_key: str,
    crypto: CryptoService,
    ssrf_guard: SSRFGuard,
    region: str | None = None,
    service_tier: str | None = None,
    tls_skip_verify: bool = False,
    tls_skip_verify_confirm: bool = False,
    timeouts: dict | None = None,
    limits: dict | None = None,
    remark: str | None = None,
) -> Connection:
    """创建服务连接：校验 supplier → 规范化 URL → SSRF 校验 → 加密 Key → 写入 DB。"""
    if tls_skip_verify and not tls_skip_verify_confirm:
        raise ValueError("开启忽略 TLS 错误前必须确认风险")
    if not api_key:
        raise ValueError("API Key 不能为空")
    _validate_limits(limits)

    # 校验 supplier 存在且状态为 active
    supplier = db.get(Supplier, supplier_id)
    if not supplier:
        raise NotFoundError("供应商不存在")
    if supplier.status != "active":
        raise ValueError("供应商已停用或归档，无法创建连接")

    # 规范化 base_url
    try:
        normalized_url = _normalize_base_url(base_url)
    except ValueError as e:
        raise ValueError(f"Base URL 格式错误: {e}")

    # SSRF 校验
    allowed, reason = ssrf_guard.check_url(normalized_url)
    if not allowed:
        raise ValueError(f"SSRF 校验失败: {reason}")

    # 加密 API Key
    ciphertext = crypto.encrypt(api_key)

    conn = Connection(
        supplier_id=supplier_id,
        name=name,
        base_url=normalized_url,
        api_key_ciphertext=ciphertext,
        credential_version=1,
        region=region,
        service_tier=service_tier,
        tls_skip_verify=tls_skip_verify,
        timeouts=timeouts,
        limits=limits,
        remark=remark,
    )
    db.add(conn)
    db.flush()
    record_event(
        db,
        "connection_created",
        target_type="connection",
        target_id=conn.id,
        diff={"name": name, "base_url": normalized_url, "status": conn.status},
    )
    db.commit()
    db.refresh(conn)
    return conn


def get_connection(db: Session, connection_id: uuid.UUID) -> Connection:
    conn = db.get(Connection, connection_id)
    if not conn:
        raise NotFoundError("连接不存在")
    return conn


def list_connections(
    db: Session,
    supplier_id: uuid.UUID,
    skip: int = 0,
    limit: int = 20,
    status_filter: str | None = None,
    keyword: str | None = None,
) -> tuple[list[Connection], int]:
    stmt = select(Connection).where(Connection.supplier_id == supplier_id)
    count_stmt = select(func.count()).select_from(Connection).where(Connection.supplier_id == supplier_id)

    if status_filter:
        stmt = stmt.where(Connection.status == status_filter)
        count_stmt = count_stmt.where(Connection.status == status_filter)
    if keyword and (keyword := keyword.strip()):
        like = f"%{keyword}%"
        condition = or_(
            Connection.name.ilike(like),
            Connection.base_url.ilike(like),
            Connection.region.ilike(like),
            Connection.service_tier.ilike(like),
        )
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    total = db.scalar(count_stmt) or 0
    items = list(db.scalars(stmt.order_by(Connection.created_at.desc()).offset(skip).limit(limit)))
    return items, total


def list_all_connections(
    db: Session,
    skip: int = 0,
    limit: int = 20,
    supplier_id: uuid.UUID | None = None,
    status_filter: str | None = None,
    keyword: str | None = None,
    include_archived: bool = False,
) -> tuple[list[Connection], int]:
    """List connections across suppliers with one database-side page query."""

    stmt = select(Connection)
    count_stmt = select(func.count()).select_from(Connection)
    if supplier_id is not None:
        stmt = stmt.where(Connection.supplier_id == supplier_id)
        count_stmt = count_stmt.where(Connection.supplier_id == supplier_id)
    if status_filter:
        stmt = stmt.where(Connection.status == status_filter)
        count_stmt = count_stmt.where(Connection.status == status_filter)
    elif not include_archived:
        stmt = stmt.where(Connection.status != "archived")
        count_stmt = count_stmt.where(Connection.status != "archived")
    if keyword and (keyword := keyword.strip()):
        like = f"%{keyword}%"
        condition = or_(
            Connection.name.ilike(like),
            Connection.base_url.ilike(like),
            Connection.region.ilike(like),
            Connection.service_tier.ilike(like),
        )
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    total = db.scalar(count_stmt) or 0
    items = list(db.scalars(stmt.order_by(Connection.created_at.desc()).offset(skip).limit(limit)))
    return items, total


def update_connection(db: Session, connection_id: uuid.UUID, **kwargs) -> Connection:
    conn = get_connection(db, connection_id)
    if "base_url" in kwargs:
        # ConnectionUpdate intentionally excludes URL changes. Reject direct
        # service calls too, otherwise callers could bypass SSRF validation.
        raise ValueError("连接 Base URL 不支持直接更新，请新建连接")
    tls_skip_verify_confirm = bool(kwargs.pop("tls_skip_verify_confirm", False))
    if kwargs.get("tls_skip_verify") is True and not tls_skip_verify_confirm:
        raise ValueError("开启忽略 TLS 错误前必须确认风险")
    if "limits" in kwargs:
        _validate_limits(kwargs["limits"])
    status = kwargs.get("status")
    if status is not None and status not in _CONNECTION_STATUSES:
        raise ValueError("连接状态必须为 active、disabled 或 archived")
    changed = {
        key: value
        for key, value in kwargs.items()
        if key in _UPDATE_KEYS and (value is not None or key in _CLEARABLE_UPDATE_KEYS)
    }
    for key, value in changed.items():
        setattr(conn, key, value)
    if changed:
        record_event(db, "connection_updated", target_type="connection", target_id=conn.id, diff=changed)
    db.commit()
    db.refresh(conn)
    return conn


def delete_connection(db: Session, connection_id: uuid.UUID) -> None:
    """删除连接（软删除，设置状态为 archived）。"""
    conn = get_connection(db, connection_id)
    conn.status = "archived"
    record_event(db, "connection_archived", target_type="connection", target_id=conn.id)
    db.commit()
