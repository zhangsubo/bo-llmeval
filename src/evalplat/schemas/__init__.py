"""供应商 Pydantic schemas（请求/响应模型，与 ORM 分离）。"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from evalplat.constants import SupplierStatus


class SupplierCreate(BaseModel):
    """创建供应商请求体。"""

    name: str = Field(..., max_length=128, description="供应商名称")
    short_name: str = Field(..., max_length=64, description="供应商简称")
    type: str = Field(..., max_length=32, description="类型（原厂/云厂商/其他）")
    remark: str | None = Field(None, description="备注")


class SupplierUpdate(BaseModel):
    """更新供应商请求体（PATCH，所有字段可选）。"""

    name: str | None = Field(None, max_length=128)
    short_name: str | None = Field(None, max_length=64)
    type: str | None = Field(None, max_length=32)
    status: str | None = Field(None, description=f"状态，可选值: {SupplierStatus.ALL}")
    remark: str | None = None


class SupplierRead(BaseModel):
    """供应商响应体。"""

    id: uuid.UUID
    name: str
    short_name: str
    type: str
    status: str
    remark: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SupplierList(BaseModel):
    """供应商列表响应体。"""

    items: list[SupplierRead]
    total: int
