"""连接 API 路由（CRUD + 测试 + 同步 + Key 替换）。"""

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from evalplat.api.deps import PaginationParams, get_db
from evalplat.core.crypto import get_crypto_service
from evalplat.core.ssrf import SSRFGuard
from evalplat.schemas.connection import ConnectionCreate, ConnectionList, ConnectionRead, ConnectionUpdate
from evalplat.schemas.model_deployment import ModelDeploymentCreate, ModelDeploymentRead, SyncResult
from evalplat.services import connection_service, key_replace_service, model_service, test_service

router = APIRouter(tags=["连接"])


# --- CRUD ---

@router.post("/suppliers/{supplier_id}/connections", response_model=ConnectionRead, status_code=201)
def create_connection(supplier_id: uuid.UUID, payload: ConnectionCreate, db: Session = Depends(get_db)):
    crypto = get_crypto_service()
    ssrf_guard = SSRFGuard()  # 空白名单 = 拒绝私有地址
    conn = connection_service.create_connection(db, supplier_id, crypto=crypto, ssrf_guard=ssrf_guard, **payload.model_dump())
    return _connection_to_read(conn, crypto)


@router.get("/suppliers/{supplier_id}/connections", response_model=ConnectionList)
def list_connections(
    supplier_id: uuid.UUID,
    pagination: PaginationParams = Depends(),
    status: str | None = Query(None),
    db: Session = Depends(get_db),
):
    items, total = connection_service.list_connections(db, supplier_id, skip=pagination.skip, limit=pagination.limit, status_filter=status)
    crypto = get_crypto_service()
    return ConnectionList(items=[_connection_to_read(c, crypto) for c in items], total=total)


@router.get("/connections/{connection_id}", response_model=ConnectionRead)
def get_connection(connection_id: uuid.UUID, db: Session = Depends(get_db)):
    conn = connection_service.get_connection(db, connection_id)
    return _connection_to_read(conn, get_crypto_service())


@router.patch("/connections/{connection_id}", response_model=ConnectionRead)
def update_connection(connection_id: uuid.UUID, payload: ConnectionUpdate, db: Session = Depends(get_db)):
    conn = connection_service.update_connection(db, connection_id, **payload.model_dump(exclude_unset=True))
    return _connection_to_read(conn, get_crypto_service())


# --- 测试 ---

class ConnectionTestRequest(BaseModel):
    model_id: str


@router.post("/connections/{connection_id}/test")
def test_connection(connection_id: uuid.UUID, body: ConnectionTestRequest, db: Session = Depends(get_db)):
    crypto = get_crypto_service()
    return test_service.run_connection_test(db, str(connection_id), body.model_id, crypto)


# --- 模型同步 ---

@router.post("/connections/{connection_id}/sync-models", response_model=SyncResult)
def sync_models(connection_id: uuid.UUID, db: Session = Depends(get_db)):
    conn = connection_service.get_connection(db, connection_id)
    crypto = get_crypto_service()
    api_key = crypto.decrypt(conn.api_key_ciphertext)
    result = model_service.sync_models(db, connection_id, conn.base_url, api_key)
    return SyncResult(**result)


# --- 手工录入模型 ---

@router.post("/connections/{connection_id}/models", response_model=ModelDeploymentRead, status_code=201)
def create_model(connection_id: uuid.UUID, payload: ModelDeploymentCreate, db: Session = Depends(get_db)):
    return model_service.create_model_deployment(db, connection_id, source="manual", **payload.model_dump())


# --- API Key 替换 ---

class KeyReplaceRequest(BaseModel):
    new_api_key: str


@router.post("/connections/{connection_id}/replace-key")
def replace_key(connection_id: uuid.UUID, body: KeyReplaceRequest, db: Session = Depends(get_db)):
    crypto = get_crypto_service()
    return key_replace_service.replace_api_key(db, connection_id, body.new_api_key, crypto)


# --- helper ---

def _connection_to_read(conn, crypto) -> ConnectionRead:
    """将 ORM Connection 转换为 ConnectionRead（解密掩码）。"""
    masked = ""
    if conn.api_key_ciphertext:
        try:
            plain = crypto.decrypt(conn.api_key_ciphertext)
            masked = crypto.mask(plain)
        except Exception:
            masked = "***解密失败***"
    return ConnectionRead(
        id=conn.id,
        supplier_id=conn.supplier_id,
        name=conn.name,
        region=conn.region,
        service_tier=conn.service_tier,
        base_url=conn.base_url,
        api_key_masked=masked,
        credential_version=conn.credential_version,
        tls_skip_verify=conn.tls_skip_verify,
        timeouts=conn.timeouts,
        limits=conn.limits,
        status=conn.status,
        remark=conn.remark,
        created_at=conn.created_at,
        updated_at=conn.updated_at,
    )
