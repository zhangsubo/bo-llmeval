"""供应商 API 路由（Phase 1 样板 + Phase 2 完整 CRUD）。"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from evalplat.api.deps import PaginationParams, get_db
from evalplat.schemas.supplier import SupplierCreate, SupplierList, SupplierRead, SupplierUpdate
from evalplat.services import supplier_service

router = APIRouter(prefix="/suppliers", tags=["供应商"])


@router.post("", response_model=SupplierRead, status_code=201)
def create_supplier(payload: SupplierCreate, db: Session = Depends(get_db)):
    return supplier_service.create_supplier(db, **payload.model_dump())


@router.get("", response_model=SupplierList)
def list_suppliers(
    pagination: PaginationParams = Depends(),
    status: str | None = Query(None, description="状态筛选"),
    keyword: str | None = Query(None, description="关键词搜索"),
    db: Session = Depends(get_db),
):
    items, total = supplier_service.list_suppliers(db, skip=pagination.skip, limit=pagination.limit, status_filter=status, keyword=keyword)
    return SupplierList(items=items, total=total)


@router.get("/{supplier_id}", response_model=SupplierRead)
def get_supplier(supplier_id: uuid.UUID, db: Session = Depends(get_db)):
    return supplier_service.get_supplier(db, supplier_id)


@router.patch("/{supplier_id}", response_model=SupplierRead)
def update_supplier(supplier_id: uuid.UUID, payload: SupplierUpdate, db: Session = Depends(get_db)):
    return supplier_service.update_supplier(db, supplier_id, **payload.model_dump(exclude_unset=True))
