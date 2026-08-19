"""价格 Pydantic schemas。"""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class PriceCreate(BaseModel):
    """创建价格请求体（追加式，每次 INSERT 新行）。"""

    input_token_price: Decimal = Field(..., ge=0, description="输入 Token 单价")
    output_token_price: Decimal = Field(..., ge=0, description="输出 Token 单价")
    cached_token_price: Decimal | None = Field(None, ge=0, description="缓存 Token 单价")
    unit: str = Field("per_1k_tokens", description="计价单位")
    currency: str = Field("CNY", description="原始币种")
    exchange_rate: Decimal = Field(..., gt=0, description="对基准币种的汇率")
    effective_from: datetime | None = Field(None, description="生效时间")


class PriceRead(BaseModel):
    id: uuid.UUID
    model_deployment_id: uuid.UUID
    input_token_price: Decimal
    output_token_price: Decimal
    cached_token_price: Decimal | None
    unit: str
    currency: str
    exchange_rate: Decimal
    effective_from: datetime | None
    price_version: int
    source: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PriceList(BaseModel):
    items: list[PriceRead]
    total: int
