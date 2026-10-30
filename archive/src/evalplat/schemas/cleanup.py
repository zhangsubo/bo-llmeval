"""历史清理 API schemas。"""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from evalplat.constants import HISTORY_CLEANUP_CONFIRMATION_TEXT


TERMINAL_TASK_STATUSES = {
    "completed",
    "partially_completed",
    "failed",
    "cancelled",
}


class CleanupPreviewRequest(BaseModel):
    cutoff_at: datetime
    statuses: list[str] = Field(
        default_factory=lambda: ["completed", "partially_completed", "failed", "cancelled"]
    )

    @field_validator("statuses")
    @classmethod
    def validate_statuses(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value))
        if not normalized:
            raise ValueError("statuses 不能为空")
        invalid = set(normalized) - TERMINAL_TASK_STATUSES
        if invalid:
            raise ValueError(f"只能清理终态任务，非法状态: {', '.join(sorted(invalid))}")
        return normalized


class CleanupPreviewRead(BaseModel):
    cutoff_at: datetime
    statuses: list[str]
    task_ids: list[uuid.UUID]
    tasks: list[dict[str, Any]]
    planned_counts: dict[str, int]
    report_files: list[str]
    estimated_bytes: int


class CleanupExecuteRequest(BaseModel):
    confirmation: str = Field(
        ...,
        description=f"必须输入确认文本 {HISTORY_CLEANUP_CONFIRMATION_TEXT}",
    )


class CleanupBatchRead(BaseModel):
    id: uuid.UUID
    cutoff_at: datetime
    statuses: list[str]
    status: Literal["pending", "running", "completed", "partial", "failed"]
    manifest: dict[str, Any]
    planned_counts: dict[str, Any] | None
    deleted_counts: dict[str, Any] | None
    failed_items: list[dict[str, Any]] | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
