"""数据集 schemas。"""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_serializer

from evalplat.core.paths import public_basename
from evalplat.services.security_service import redact_sensitive_text


class DatasetCreate(BaseModel):
    name: str = Field(..., max_length=128)
    kind: Literal["performance", "quality"] = Field(..., description="performance/quality")
    remark: str | None = None


class DatasetRead(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    remark: str | None
    is_builtin: bool = False
    builtin_key: str | None = None
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
    source_id: str | None = None
    source_revision: str | None = None
    source_split: str | None = None
    source_subset: str | None = None
    sample_count: int | None = None
    language: str | None = None
    capability_tags: list[str] | None = None
    default_prompt: str | None = None
    scoring_method: str | None = None
    field_mapping: dict[str, Any] | None = None
    status: Literal["pending", "processing", "ready", "failed", "archived"] = "pending"


class DatasetVersionRead(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID
    version: int
    source: str | None
    license: str | None
    checksum: str | None
    source_id: str | None = None
    source_revision: str | None = None
    source_split: str | None = None
    source_subset: str | None = None
    sample_count: int | None
    language: str | None
    capability_tags: list[str] | None
    default_prompt: str | None
    scoring_method: str | None
    field_mapping: dict[str, Any] | None
    token_length_stats: dict[str, Any] | None
    status: str
    storage_path: str | None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}

    @field_serializer("storage_path")
    def serialize_storage_path(self, value: str | None) -> str | None:
        return public_basename(value)

    @field_serializer("error")
    def serialize_error(self, value: str | None) -> str | None:
        return redact_sensitive_text(value) or None


class DatasetVersionList(BaseModel):
    items: list[DatasetVersionRead]
    total: int


class BuiltinDatasetCatalogItem(BaseModel):
    key: str
    name: str
    evalscope_name: str
    source_id: str
    source: str
    split: str
    subset: str
    license: str
    language: str
    capability_tags: list[str]
    scoring_method: str
    description: str


class BuiltinDatasetCatalog(BaseModel):
    items: list[BuiltinDatasetCatalogItem]
