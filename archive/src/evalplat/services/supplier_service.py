"""供应商业务逻辑。"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evalplat.core.errors import NotFoundError
from evalplat.constants import SupplierStatus
from evalplat.models.supplier import Supplier
from evalplat.services.audit_service import record_event

_UPDATE_KEYS = {"name", "short_name", "type", "status", "remark"}


def create_supplier(db: Session, **kwargs) -> Supplier:
    status = kwargs.get("status")
    if status is not None and status not in SupplierStatus.ALL:
        raise ValueError(f"供应商状态必须为 {', '.join(SupplierStatus.ALL)}")
    supplier = Supplier(**kwargs)
    db.add(supplier)
    db.flush()
    record_event(
        db,
        "supplier_created",
        target_type="supplier",
        target_id=supplier.id,
        diff={"name": supplier.name, "short_name": supplier.short_name, "type": supplier.type},
    )
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
    status = kwargs.get("status")
    if status is not None and status not in SupplierStatus.ALL:
        raise ValueError(f"供应商状态必须为 {', '.join(SupplierStatus.ALL)}")
    changed = {}
    for key, value in kwargs.items():
        # `remark` is intentionally clearable via an explicit JSON null;
        # required identity/status fields keep their existing value when
        # omitted or passed as null by a lower-level caller.
        if key in _UPDATE_KEYS and (value is not None or key == "remark"):
            setattr(supplier, key, value)
            if key != "remark":
                changed[key] = value
    if changed:
        record_event(db, "supplier_updated", target_type="supplier", target_id=supplier.id, diff=changed)
    db.commit()
    db.refresh(supplier)
    return supplier
