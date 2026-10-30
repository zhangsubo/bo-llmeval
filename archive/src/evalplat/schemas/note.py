"""任务和 Run 备注 schemas。"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class NoteCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=20_000)


class NoteUpdate(BaseModel):
    body: str = Field(..., min_length=1, max_length=20_000)


class NoteRead(BaseModel):
    id: uuid.UUID
    target_type: str
    target_id: uuid.UUID
    body: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class NoteList(BaseModel):
    items: list[NoteRead]
    total: int
