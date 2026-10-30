"""评测任务 Pydantic schemas。"""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class TaskCreate(BaseModel):
    """创建任务请求体。"""

    name: str = Field(..., max_length=256, description="任务名称")
    template_version_id: uuid.UUID | None = Field(None, description="模板版本 ID")
    run_mode: Literal["serial", "parallel"] = Field(
        ..., description="运行模式: serial/parallel"
    )
    evaluation_kind: Literal["performance", "quality"] = Field(
        "performance", description="评测类型"
    )
    evaluation_engine: Literal["native", "evalscope"] = Field(
        "evalscope", description="评测执行引擎；性能和文本质量统一使用 EvalScope"
    )
    quality_mode: Literal["text", "code"] = Field(
        "text", description="质量评测模式"
    )
    test_type: str | None = Field(None, max_length=32, description="模板测试类型")
    scenario: str | None = Field(None, max_length=32, description="模板场景")
    idempotency_key: str | None = Field(None, max_length=64, description="幂等键")
    model_deployment_ids: list[uuid.UUID] = Field(
        default_factory=list, description="目标模型部署 ID 列表"
    )
    dataset_version_id: uuid.UUID | None = Field(None, description="数据集版本 ID")
    tokenizer_id: uuid.UUID | None = Field(None, description="Tokenizer ID")
    generation_params: dict[str, Any] | None = Field(None, description="生成参数")
    input_length_preset: Literal["short", "medium", "long", "extra_long"] | None = Field(
        None, description="内置输入长度预设"
    )
    load_profile: dict[str, Any] | None = Field(None, description="负载配置")
    request_count: int = Field(1, gt=0, le=1_000_000, description="请求数量")
    warmup_count: int = Field(3, ge=0, le=1000, description="预热请求数量")
    stream: bool = Field(False, description="是否使用流式 Chat Completions")
    retry_policy: dict[str, Any] | None = Field(None, description="质量请求重试策略")
    timeouts: dict[str, Any] | None = Field(None, description="任务级超时配置")
    limits: dict[str, Any] | None = Field(None, description="任务级连接限额")
    messages: list[dict[str, Any]] | None = Field(None, description="Chat Completions 输入消息")
    quality_samples: list[dict[str, Any]] | None = Field(
        None, description="质量评测样本（每项至少包含 messages 和 expected）"
    )
    evalscope_datasets: list[str] | None = Field(None, description="EvalScope benchmark 名称")
    dataset_args: dict[str, Any] | None = Field(None, description="EvalScope 数据集参数")
    code_sandbox_image: str | None = Field(None, max_length=256, description="受控代码沙箱镜像")
    code_timeout_seconds: int | None = Field(None, gt=0, le=300)
    code_memory_mb: int | None = Field(None, gt=0, le=4096)
    code_cpus: float | None = Field(None, gt=0, le=8)
    code_pids_limit: int | None = Field(None, gt=0, le=1024)


class TaskUpdate(BaseModel):
    """更新任务请求体（PATCH，所有字段可选）。"""

    name: str | None = Field(None, max_length=256)
    remark: str | None = None
    model_deployment_ids: list[uuid.UUID] | None = None
    dataset_version_id: uuid.UUID | None = None
    tokenizer_id: uuid.UUID | None = None
    generation_params: dict[str, Any] | None = None
    input_length_preset: Literal["short", "medium", "long", "extra_long"] | None = None
    load_profile: dict[str, Any] | None = None
    request_count: int | None = Field(None, gt=0, le=1_000_000)
    warmup_count: int | None = Field(None, ge=0, le=1000)
    stream: bool | None = None
    retry_policy: dict[str, Any] | None = None
    messages: list[dict[str, Any]] | None = None
    evaluation_kind: Literal["performance", "quality"] | None = None
    evaluation_engine: Literal["native", "evalscope"] | None = None
    quality_mode: Literal["text", "code"] | None = None
    test_type: str | None = Field(None, max_length=32)
    scenario: str | None = Field(None, max_length=32)
    quality_samples: list[dict[str, Any]] | None = None
    evalscope_datasets: list[str] | None = None
    dataset_args: dict[str, Any] | None = None
    timeouts: dict[str, Any] | None = None
    limits: dict[str, Any] | None = None
    code_sandbox_image: str | None = Field(None, max_length=256)
    code_timeout_seconds: int | None = Field(None, gt=0, le=300)
    code_memory_mb: int | None = Field(None, gt=0, le=4096)
    code_cpus: float | None = Field(None, gt=0, le=8)
    code_pids_limit: int | None = Field(None, gt=0, le=1024)


class TaskRead(BaseModel):
    """任务响应体。"""

    id: uuid.UUID
    name: str | None
    template_version_id: uuid.UUID | None = None
    run_mode: str
    status: str
    config_snapshot: dict[str, Any] | None
    precheck_result: dict[str, Any] | None
    idempotency_key: str | None
    queued_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    remark: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskList(BaseModel):
    """任务列表响应体。"""

    items: list[TaskRead]
    total: int
