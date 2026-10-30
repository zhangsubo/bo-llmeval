"""Tokenizer schemas。"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_serializer

from evalplat.core.paths import public_basename
from evalplat.services.security_service import redact_sensitive_text


class TokenizerCreate(BaseModel):
    name: str = Field(..., max_length=128)
    source: Literal["modelscope", "huggingface", "upload", "builtin"] = Field(
        ..., description="modelscope/huggingface/upload/builtin"
    )
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

    @field_serializer("local_path")
    def serialize_local_path(self, value: str | None) -> str | None:
        return public_basename(value)

    @field_serializer("error")
    def serialize_error(self, value: str | None) -> str | None:
        return redact_sensitive_text(value) or None


class TokenizerList(BaseModel):
    items: list[TokenizerRead]
    total: int
