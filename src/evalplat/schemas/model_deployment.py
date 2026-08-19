"""模型部署 Pydantic schemas。"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ModelDeploymentCreate(BaseModel):
    """创建模型部署请求体（手工录入）。"""

    model_id: str = Field(..., max_length=256, description="实际调用的 model ID")
    display_name: str | None = Field(None, max_length=256)
    context_window: int | None = Field(None, gt=0)
    max_output_tokens: int | None = Field(None, gt=0)
    supports_streaming: bool | None = None
    supports_non_streaming: bool | None = None
    remark: str | None = None


class ModelDeploymentUpdate(BaseModel):
    """更新模型部署请求体（PATCH）。"""

    display_name: str | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None
    supports_streaming: bool | None = None
    supports_non_streaming: bool | None = None
    status: str | None = None
    remark: str | None = None


class ModelDeploymentRead(BaseModel):
    """模型部署响应体。"""

    id: uuid.UUID
    connection_id: uuid.UUID
    model_id: str
    display_name: str | None
    comparison_group_id: uuid.UUID | None
    context_window: int | None
    max_output_tokens: int | None
    supports_streaming: bool | None
    supports_non_streaming: bool | None
    tokenizer_id: uuid.UUID | None
    model_version: str | None
    source: str
    status: str
    remark: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ModelDeploymentList(BaseModel):
    items: list[ModelDeploymentRead]
    total: int


class SyncResult(BaseModel):
    """模型同步结果。"""

    total: int
    added: int
    skipped: int
    error: str | None = None
