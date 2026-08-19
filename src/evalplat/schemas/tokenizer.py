"""Tokenizer schemas。"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class TokenizerCreate(BaseModel):
    name: str = Field(..., max_length=128)
    source: str = Field(..., description="modelscope/huggingface/upload/builtin")
    resource_id: str | None = Field(None, max_length=256)
    revision: str | None = Field(None, max_length=64)


class TokenizerRead(BaseModel):
    id: uuid.UUID
    name: str
    source: str
    resource_id: str | None
    revision: str | None
    checksum: str | None
    size_bytes: int | None
    status: str
    error: str | None
    local_path: str | None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class TokenizerList(BaseModel):
    items: list[TokenizerRead]
    total: int
