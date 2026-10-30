"""代码执行记录 API schemas。"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_serializer

from evalplat.services.security_service import redact_sensitive_text, redact_sensitive_value


class CodeExecutionRead(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    request_id: int | None
    sample_index: int | None
    code: str
    tests: str
    status: str
    compile_status: str
    run_status: str
    stdout: str | None
    stderr: str | None
    test_results: dict[str, Any] | None
    exit_code: int | None
    duration_ms: float | None
    error_category: str | None
    error_summary: str | None
    container_image: str
    timeout_seconds: int
    memory_mb: int
    cpus: float
    pids_limit: int
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("code", "tests", "stdout", "stderr", "error_summary")
    def serialize_sensitive_text(self, value: str | None) -> str | None:
        return redact_sensitive_text(value) if value is not None else None

    @field_serializer("test_results")
    def serialize_test_results(self, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return redact_sensitive_value(value)


class CodeExecutionList(BaseModel):
    items: list[CodeExecutionRead]
    total: int
