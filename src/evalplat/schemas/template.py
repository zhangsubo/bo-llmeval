"""测试方案模板 schemas。"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TemplateCreate(BaseModel):
    name: str = Field(..., max_length=128)
    is_builtin: bool = False


class TemplateRead(BaseModel):
    id: uuid.UUID
    name: str
    is_builtin: bool
    status: str
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class TemplateList(BaseModel):
    items: list[TemplateRead]
    total: int


class TemplateVersionCreate(BaseModel):
    test_type: str | None = None
    scenario: str | None = None
    dataset_version_id: uuid.UUID | None = None
    tokenizer_id: uuid.UUID | None = None
    generation_params: dict[str, Any] | None = None
    stream_mode: str | None = None
    warmup_count: int | None = None
    load_profile: dict[str, Any] | None = None
    request_count: int | None = None
    retry_policy: dict[str, Any] | None = None
    timeouts: dict[str, Any] | None = None
    limits: dict[str, Any] | None = None
    remark: str | None = None


class TemplateVersionRead(BaseModel):
    id: uuid.UUID
    template_id: uuid.UUID
    version: int
    test_type: str | None
    scenario: str | None
    dataset_version_id: uuid.UUID | None
    tokenizer_id: uuid.UUID | None
    generation_params: dict[str, Any] | None
    stream_mode: str | None
    warmup_count: int | None
    load_profile: dict[str, Any] | None
    request_count: int | None
    retry_policy: dict[str, Any] | None
    timeouts: dict[str, Any] | None
    limits: dict[str, Any] | None
    remark: str | None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class TemplateCopyRequest(BaseModel):
    new_name: str = Field(..., max_length=128)
