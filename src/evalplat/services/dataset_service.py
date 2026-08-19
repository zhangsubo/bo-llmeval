"""数据集业务逻辑（PRD §8.5）。"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evalplat.core.errors import ConflictError, NotFoundError
from evalplat.models.dataset import Dataset, DatasetVersion


def create_dataset(db: Session, name: str, kind: str, remark: str | None = None) -> Dataset:
    ds = Dataset(name=name, kind=kind, remark=remark)
    db.add(ds)
    db.commit()
    db.refresh(ds)
    return ds


def get_dataset(db: Session, dataset_id: uuid.UUID) -> Dataset:
    ds = db.get(Dataset, dataset_id)
    if not ds:
        raise NotFoundError("数据集不存在")
    return ds


def list_datasets(db: Session, skip: int = 0, limit: int = 20, kind: str | None = None) -> tuple[list[Dataset], int]:
    stmt = select(Dataset)
    count_stmt = select(func.count()).select_from(Dataset)
    if kind:
        stmt = stmt.where(Dataset.kind == kind)
        count_stmt = count_stmt.where(Dataset.kind == kind)
    total = db.scalar(count_stmt) or 0
    items = list(db.scalars(stmt.order_by(Dataset.created_at.desc()).offset(skip).limit(limit)))
    return items, total


def create_version(db: Session, dataset_id: uuid.UUID, **kwargs) -> DatasetVersion:
    ds = get_dataset(db, dataset_id)
    # 查询当前最大版本号
    max_ver = db.scalar(
        select(func.max(DatasetVersion.version)).where(DatasetVersion.dataset_id == dataset_id)
    ) or 0
    ver = DatasetVersion(dataset_id=dataset_id, version=max_ver + 1, **kwargs)
    db.add(ver)
    db.commit()
    db.refresh(ver)
    return ver


def list_versions(db: Session, dataset_id: uuid.UUID, skip: int = 0, limit: int = 20) -> tuple[list[DatasetVersion], int]:
    stmt = select(DatasetVersion).where(DatasetVersion.dataset_id == dataset_id)
    count_stmt = select(func.count()).select_from(DatasetVersion).where(DatasetVersion.dataset_id == dataset_id)
    total = db.scalar(count_stmt) or 0
    items = list(db.scalars(stmt.order_by(DatasetVersion.version.desc()).offset(skip).limit(limit)))
    return items, total


def update_version_status(db: Session, version_id: uuid.UUID, status: str) -> DatasetVersion:
    ver = db.get(DatasetVersion, version_id)
    if not ver:
        raise NotFoundError("数据集版本不存在")
    ver.status = status
    db.commit()
    db.refresh(ver)
    return ver
