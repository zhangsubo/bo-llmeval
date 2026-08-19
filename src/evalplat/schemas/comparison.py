"""结果对比 Pydantic schemas（Phase 8）。"""

import uuid
from typing import Any

from pydantic import BaseModel, Field


class ComparisonCreate(BaseModel):
    """创建对比请求体：指定要对比的 Run ID 列表。"""

    run_ids: list[uuid.UUID] = Field(..., min_length=2, max_length=20, description="要对比的 Run ID 列表（至少 2 个）")


class RunSummary(BaseModel):
    """单个 Run 的摘要信息。"""

    id: uuid.UUID
    task_id: uuid.UUID | None = None
    model_deployment_id: uuid.UUID | None = None
    status: str
    snapshot: dict[str, Any] | None = Field(None, alias="run_snapshot")

    model_config = {"from_attributes": True, "populate_by_name": True}


class DifferenceItem(BaseModel):
    """差异项：描述两个 Run 之间的某个维度差异。"""

    dimension: str = Field(..., description="差异维度（如 dataset, tokenizer, params）")
    details: str = Field(..., description="差异描述")


class ComparisonResult(BaseModel):
    """对比结果响应体。"""

    runs: list[RunSummary]
    comparability: str = Field(..., description="可比性判定: comparable / not_comparable")
    differences: list[DifferenceItem] = Field(default_factory=list, description="差异列表（不可比时非空）")
