"""供应商业务逻辑。"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evalplat.core.errors import NotFoundError
from evalplat.models.supplier import Supplier


def create_supplier(db: Session, **kwargs) -> Supplier:
    supplier = Supplier(**kwargs)
    db.add(supplier)
    db.commit()
    db.refresh(supplier)
    return supplier


def get_supplier(db: Session, supplier_id: uuid.UUID) -> Supplier:
    supplier = db.get(Supplier, supplier_id)
    if not supplier:
        raise NotFoundError("供应商不存在")
    return supplier


def list_suppliers(
    db: Session, skip: int = 0, limit: int = 20, status_filter: str | None = None, keyword: str | None = None
) -> tuple[list[Supplier], int]:
    stmt = select(Supplier)
    count_stmt = select(func.count()).select_from(Supplier)

    if status_filter:
        stmt = stmt.where(Supplier.status == status_filter)
        count_stmt = count_stmt.where(Supplier.status == status_filter)
    if keyword:
        like = f"%{keyword}%"
        cond = Supplier.name.ilike(like) | Supplier.short_name.ilike(like)
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    total = db.scalar(count_stmt) or 0
    items = list(db.scalars(stmt.order_by(Supplier.created_at.desc()).offset(skip).limit(limit)))
    return items, total


def update_supplier(db: Session, supplier_id: uuid.UUID, **kwargs) -> Supplier:
    supplier = get_supplier(db, supplier_id)
    for key, value in kwargs.items():
        if value is not None and hasattr(supplier, key):
            setattr(supplier, key, value)
    db.commit()
    db.refresh(supplier)
    return supplier
