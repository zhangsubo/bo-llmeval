"""对比组 Pydantic schemas。"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ComparisonGroupCreate(BaseModel):
    name: str = Field(..., max_length=128)
    default_tokenizer_id: uuid.UUID | None = None
    remark: str | None = None


class ComparisonGroupUpdate(BaseModel):
    name: str | None = Field(None, max_length=128)
    default_tokenizer_id: uuid.UUID | None = None
    remark: str | None = None


class ComparisonGroupRead(BaseModel):
    id: uuid.UUID
    name: str
    default_tokenizer_id: uuid.UUID | None
    remark: str | None
    created_at: datetime
    updated_at: datetime
    member_count: int = 0

    model_config = {"from_attributes": True}


class ComparisonGroupList(BaseModel):
    items: list[ComparisonGroupRead]
    total: int
