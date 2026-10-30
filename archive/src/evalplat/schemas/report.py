"""报告版本 API schemas。"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_serializer

from evalplat.core.paths import public_basename


class ReportRead(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID
    version: int
    status: str
    summary_path: str | None
    details_path: str | None
    idempotency_key: str | None
    snapshot: dict[str, Any] | None
    error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("summary_path", "details_path")
    def serialize_public_path(self, value: str | None) -> str | None:
        if not value:
            return None
        return public_basename(value)


class ReportList(BaseModel):
    items: list[ReportRead]
    total: int


class ReportGenerateResponse(BaseModel):
    """报告生成操作的公开响应。

    生成器内部还会维护快照、错误和数据库时间戳；这些字段属于报告
    版本查询接口，不应因为操作接口缺少 response model 而意外泄露。
    """

    id: uuid.UUID
    task_id: uuid.UUID
    version: int
    status: str
    path: str | None = None
    details_path: str | None = None
    page_paths: dict[str, str] = Field(default_factory=dict)
    download_url: str | None = None
    details_download_url: str | None = None
    created_at: datetime

    @field_serializer("path", "details_path")
    def serialize_generated_path(self, value: str | None) -> str | None:
        return public_basename(value) if value else None

    @field_serializer("page_paths")
    def serialize_generated_page_paths(self, value: dict[str, str]) -> dict[str, str]:
        return {
            str(key): filename
            for key, raw_path in value.items()
            if (filename := public_basename(raw_path)) is not None
        }
