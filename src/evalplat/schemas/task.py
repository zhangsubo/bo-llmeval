"""评测任务 Pydantic schemas。"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TaskCreate(BaseModel):
    """创建任务请求体。"""

    name: str = Field(..., max_length=256, description="任务名称")
    template_version_id: uuid.UUID | None = Field(None, description="模板版本 ID")
    run_mode: str = Field(..., description="运行模式: serial/parallel")
    idempotency_key: str | None = Field(None, max_length=64, description="幂等键")


class TaskUpdate(BaseModel):
    """更新任务请求体（PATCH，所有字段可选）。"""

    name: str | None = Field(None, max_length=256)
    remark: str | None = None


class TaskRead(BaseModel):
    """任务响应体。"""

    id: uuid.UUID
    name: str | None
    run_mode: str
    status: str
    config_snapshot: dict[str, Any] | None
    precheck_result: dict[str, Any] | None
    idempotency_key: str | None
    queued_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    remark: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskList(BaseModel):
    """任务列表响应体。"""

    items: list[TaskRead]
    total: int
