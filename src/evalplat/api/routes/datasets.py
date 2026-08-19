"""数据集 API 路由。"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from evalplat.api.deps import PaginationParams, get_db
from evalplat.schemas.dataset import (
    DatasetCreate, DatasetList, DatasetRead,
    DatasetVersionCreate, DatasetVersionList, DatasetVersionRead,
)
from evalplat.services import dataset_service

router = APIRouter(tags=["数据集"])


@router.post("/datasets", response_model=DatasetRead, status_code=201)
def create_dataset(payload: DatasetCreate, db: Session = Depends(get_db)):
    return dataset_service.create_dataset(db, **payload.model_dump())


@router.get("/datasets", response_model=DatasetList)
def list_datasets(
    pagination: PaginationParams = Depends(),
    kind: str | None = Query(None),
    db: Session = Depends(get_db),
):
    items, total = dataset_service.list_datasets(db, skip=pagination.skip, limit=pagination.limit, kind=kind)
    return DatasetList(items=items, total=total)


@router.get("/datasets/{dataset_id}", response_model=DatasetRead)
def get_dataset(dataset_id: uuid.UUID, db: Session = Depends(get_db)):
    return dataset_service.get_dataset(db, dataset_id)


@router.post("/datasets/{dataset_id}/versions", response_model=DatasetVersionRead, status_code=201)
def create_version(dataset_id: uuid.UUID, payload: DatasetVersionCreate, db: Session = Depends(get_db)):
    return dataset_service.create_version(db, dataset_id, **payload.model_dump())


@router.get("/datasets/{dataset_id}/versions", response_model=DatasetVersionList)
def list_versions(dataset_id: uuid.UUID, pagination: PaginationParams = Depends(), db: Session = Depends(get_db)):
    items, total = dataset_service.list_versions(db, dataset_id, skip=pagination.skip, limit=pagination.limit)
    return DatasetVersionList(items=items, total=total)
