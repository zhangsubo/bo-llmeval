"""评测 Run Pydantic schemas。"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class RunRead(BaseModel):
    """Run 响应体。"""

    id: uuid.UUID
    task_id: uuid.UUID | None
    model_deployment_id: uuid.UUID | None
    seq: int | None
    status: str
    run_snapshot: dict[str, Any] | None
    queued_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    error_summary: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RunList(BaseModel):
    """Run 列表响应体。"""

    items: list[RunRead]
    total: int


class RequestRead(BaseModel):
    """请求响应体（精简字段）。"""

    id: int
    run_id: str
    sample_index: int | None
    kind: str
    status: str
    scheduled_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    vendor_prompt_tokens: int | None
    vendor_completion_tokens: int | None
    vendor_total_tokens: int | None

    model_config = {"from_attributes": True}


class RequestList(BaseModel):
    """请求列表响应体。"""

    items: list[RequestRead]
    total: int
