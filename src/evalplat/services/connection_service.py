"""连接业务逻辑（含 Fernet 加密 + SSRF 校验）。"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evalplat.core.crypto import CryptoService
from evalplat.core.errors import NotFoundError
from evalplat.core.ssrf import SSRFGuard
from evalplat.models.connection import Connection
from evalplat.models.supplier import Supplier


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

    return urlunparse((parsed.scheme, f"{hostname}:{parsed.port}" if parsed.port else hostname, path, "", "", ""))


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
    timeouts: dict | None = None,
    limits: dict | None = None,
    remark: str | None = None,
) -> Connection:
    """创建服务连接：校验 supplier → 规范化 URL → SSRF 校验 → 加密 Key → 写入 DB。"""
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
    db.commit()
    db.refresh(conn)
    return conn


def get_connection(db: Session, connection_id: uuid.UUID) -> Connection:
    conn = db.get(Connection, connection_id)
    if not conn:
        raise NotFoundError("连接不存在")
    return conn


def list_connections(
    db: Session, supplier_id: uuid.UUID, skip: int = 0, limit: int = 20, status_filter: str | None = None
) -> tuple[list[Connection], int]:
    stmt = select(Connection).where(Connection.supplier_id == supplier_id)
    count_stmt = select(func.count()).select_from(Connection).where(Connection.supplier_id == supplier_id)

    if status_filter:
        stmt = stmt.where(Connection.status == status_filter)
        count_stmt = count_stmt.where(Connection.status == status_filter)

    total = db.scalar(count_stmt) or 0
    items = list(db.scalars(stmt.order_by(Connection.created_at.desc()).offset(skip).limit(limit)))
    return items, total


def update_connection(db: Session, connection_id: uuid.UUID, **kwargs) -> Connection:
    conn = get_connection(db, connection_id)
    for key, value in kwargs.items():
        if value is not None and hasattr(conn, key):
            setattr(conn, key, value)
    db.commit()
    db.refresh(conn)
    return conn
