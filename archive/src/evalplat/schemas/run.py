"""评测 Run Pydantic schemas。"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class RunRead(BaseModel):
    """Run 响应体。"""

    id: uuid.UUID
    task_id: uuid.UUID | None
    model_deployment_id: uuid.UUID | None
    retry_of_run_id: uuid.UUID | None
    seq: int | None
    status: str
    run_snapshot: dict[str, Any] | None
    queued_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    error_summary: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RunList(BaseModel):
    """Run 列表响应体。"""

    items: list[RunRead]
    total: int


class RequestSummary(BaseModel):
    """请求分页摘要；不携带 Prompt、回答或质量证据正文。"""

    id: int
    run_id: uuid.UUID
    sample_index: int | None
    kind: str
    status: str
    scheduled_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    vendor_prompt_tokens: int | None
    vendor_cached_tokens: int | None
    vendor_completion_tokens: int | None
    vendor_total_tokens: int | None
    local_prompt_tokens: int | None
    local_completion_tokens: int | None
    local_total_tokens: int | None

    model_config = {"from_attributes": True}


class RequestList(BaseModel):
    """请求列表响应体。"""

    items: list[RequestSummary]
    total: int


class AttemptRead(BaseModel):
    """单次 Attempt 详情；仅在按需打开 Request 时返回。"""

    id: int
    request_id: int
    attempt_index: int
    started_at: datetime | None
    finished_at: datetime | None
    latency_ms: Any | None
    ttft_ms: Any | None
    tpot_ms: Any | None
    itl_series: dict[str, Any] | None
    avg_speed: Any | None
    peak_speed: Any | None
    peak_tps: Any | None
    vendor_usage: dict[str, Any] | None
    raw_response: str | None
    extracted_fields: dict[str, Any] | None
    error_category: str | None
    error_body: str | None
    http_status: int | None
    vendor_request_id: str | None
    response_headers: dict[str, Any] | None

    model_config = {"from_attributes": True}


class RequestDetail(RequestSummary):
    """Request 完整详情；正文不进入分页列表。"""

    input_messages: dict[str, Any] | list[Any] | None
    quality_result: dict[str, Any] | None
    attempts: list[AttemptRead]
