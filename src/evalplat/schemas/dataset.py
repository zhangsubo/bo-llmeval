"""数据集 schemas。"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DatasetCreate(BaseModel):
    name: str = Field(..., max_length=128)
    kind: str = Field(..., description="performance/quality")
    remark: str | None = None


class DatasetRead(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    remark: str | None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class DatasetList(BaseModel):
    items: list[DatasetRead]
    total: int


class DatasetVersionCreate(BaseModel):
    source: str | None = None
    license: str | None = None
    checksum: str | None = None
    sample_count: int | None = None
    language: str | None = None
    capability_tags: list[str] | None = None
    default_prompt: str | None = None
    scoring_method: str | None = None
    field_mapping: dict[str, Any] | None = None
    status: str = "pending"


class DatasetVersionRead(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID
    version: int
    source: str | None
    license: str | None
    checksum: str | None
    sample_count: int | None
    language: str | None
    capability_tags: list[str] | None
    default_prompt: str | None
    scoring_method: str | None
    field_mapping: dict[str, Any] | None
    token_length_stats: dict[str, Any] | None
    status: str
    storage_path: str | None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class DatasetVersionList(BaseModel):
    items: list[DatasetVersionRead]
    total: int
