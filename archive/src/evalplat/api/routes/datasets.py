"""数据集 API 路由。"""

import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from evalplat.api.deps import PaginationParams, get_db
from evalplat.schemas.dataset import (
    BuiltinDatasetCatalog, BuiltinDatasetCatalogItem, DatasetCreate, DatasetList, DatasetRead,
    DatasetVersionCreate, DatasetVersionList, DatasetVersionRead,
)
from evalplat.services import dataset_service
from evalplat.config import get_settings

router = APIRouter(tags=["数据集"])


@router.post("/datasets", response_model=DatasetRead, status_code=201)
def create_dataset(payload: DatasetCreate, db: Session = Depends(get_db)):
    return dataset_service.create_dataset(db, **payload.model_dump())


@router.get("/datasets", response_model=DatasetList)
def list_datasets(
    pagination: PaginationParams = Depends(),
    kind: str | None = Query(None),
    keyword: str | None = Query(None, description="数据集名称关键词"),
    db: Session = Depends(get_db),
):
    items, total = dataset_service.list_datasets(
        db, skip=pagination.skip, limit=pagination.limit, kind=kind, keyword=keyword
    )
    return DatasetList(items=items, total=total)


@router.get("/dataset-versions", response_model=DatasetVersionList)
def list_all_versions(
    pagination: PaginationParams = Depends(),
    dataset_id: uuid.UUID | None = Query(None),
    version_id: uuid.UUID | None = Query(None),
    status: str | None = Query(None),
    keyword: str | None = Query(None, description="数据集名称或来源关键词"),
    db: Session = Depends(get_db),
):
    """跨数据集分页查询版本，供任务编辑器按需搜索。"""

    items, total = dataset_service.list_all_versions(
        db,
        skip=pagination.skip,
        limit=pagination.limit,
        dataset_id=dataset_id,
        version_id=version_id,
        status=status,
        keyword=keyword,
    )
    return DatasetVersionList(items=items, total=total)


@router.get("/datasets/builtins/catalog", response_model=BuiltinDatasetCatalog)
def list_builtin_datasets():
    """列出可下载的内置质量数据集，不访问数据库或远程 hub。"""

    return BuiltinDatasetCatalog(
        items=[BuiltinDatasetCatalogItem.model_validate(item) for item in dataset_service.builtin_catalog()]
    )


@router.post(
    "/datasets/builtins/{builtin_key}/download",
    response_model=DatasetVersionRead,
    status_code=201,
)
def download_builtin_dataset(
    builtin_key: str,
    force: bool = Query(False),
    db: Session = Depends(get_db),
):
    """下载并缓存一个内置 benchmark；重复调用默认复用 ready 版本。"""

    return dataset_service.download_builtin_dataset(
        db,
        builtin_key,
        get_settings().dataset_dir,
        force=force,
    )


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


@router.post("/datasets/{dataset_id}/versions/upload", response_model=DatasetVersionRead, status_code=201)
def upload_version(dataset_id: uuid.UUID, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """上传并全量校验 JSONL/CSV 数据集。"""

    content = file.file.read(dataset_service.MAX_UPLOAD_BYTES + 1)
    return dataset_service.upload_version(
        db,
        dataset_id,
        file.filename or "dataset.jsonl",
        content,
        get_settings().dataset_dir,
    )
