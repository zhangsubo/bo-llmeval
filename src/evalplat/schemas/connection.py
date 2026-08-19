"""服务连接 Pydantic schemas。"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ConnectionCreate(BaseModel):
    """创建连接请求体。"""

    name: str = Field(..., max_length=128, description="连接名称")
    base_url: str = Field(..., description="供应商 Base URL（不含 /v1）")
    api_key: str = Field(..., description="Bearer API Key（明文输入，加密存储）")
    region: str | None = Field(None, max_length=64)
    service_tier: str | None = Field(None, max_length=32)
    tls_skip_verify: bool = Field(False, description="忽略 TLS 错误（默认关闭）")
    timeouts: dict[str, Any] | None = Field(None, description="超时配置 JSON")
    limits: dict[str, Any] | None = Field(None, description="限额配置 JSON")
    remark: str | None = None


class ConnectionUpdate(BaseModel):
    """更新连接请求体（PATCH，不含 URL 和 Key 变更）。"""

    name: str | None = Field(None, max_length=128)
    region: str | None = None
    service_tier: str | None = None
    tls_skip_verify: bool | None = None
    timeouts: dict[str, Any] | None = None
    limits: dict[str, Any] | None = None
    status: str | None = Field(None, description="状态: active/disabled/archived")
    remark: str | None = None


class ConnectionRead(BaseModel):
    """连接响应体（API Key 只返回掩码）。"""

    id: uuid.UUID
    supplier_id: uuid.UUID
    name: str
    region: str | None
    service_tier: str | None
    base_url: str
    api_key_masked: str = Field(..., description="API Key 掩码")
    credential_version: int
    tls_skip_verify: bool
    timeouts: dict[str, Any] | None
    limits: dict[str, Any] | None
    status: str
    remark: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConnectionList(BaseModel):
    """连接列表响应体。"""

    items: list[ConnectionRead]
    total: int
