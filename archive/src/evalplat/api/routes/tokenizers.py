"""Tokenizer API 路由。"""

import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from evalplat.api.deps import PaginationParams, get_db
from evalplat.schemas.tokenizer import TokenizerCreate, TokenizerList, TokenizerRead
from evalplat.services import tokenizer_service
from evalplat.config import get_settings

router = APIRouter(prefix="/tokenizers", tags=["Tokenizer"])


@router.post("", response_model=TokenizerRead, status_code=201)
def create_tokenizer(payload: TokenizerCreate, db: Session = Depends(get_db)):
    return tokenizer_service.create_tokenizer(db, **payload.model_dump())


@router.get("", response_model=TokenizerList)
def list_tokenizers(
    pagination: PaginationParams = Depends(),
    status: str | None = Query(None),
    keyword: str | None = Query(None, description="Tokenizer 名称或资源关键词"),
    db: Session = Depends(get_db),
):
    items, total = tokenizer_service.list_tokenizers(
        db, skip=pagination.skip, limit=pagination.limit, status_filter=status, keyword=keyword
    )
    return TokenizerList(items=items, total=total)


@router.get("/{tokenizer_id}", response_model=TokenizerRead)
def get_tokenizer(tokenizer_id: uuid.UUID, db: Session = Depends(get_db)):
    return tokenizer_service.get_tokenizer(db, tokenizer_id)


@router.patch("/{tokenizer_id}/status", response_model=TokenizerRead)
def update_status(tokenizer_id: uuid.UUID, status: str = Query(...), error: str | None = Query(None), db: Session = Depends(get_db)):
    return tokenizer_service.update_tokenizer_status(db, tokenizer_id, status, error)


@router.post("/{tokenizer_id}/download", response_model=TokenizerRead)
def download(tokenizer_id: uuid.UUID, db: Session = Depends(get_db)):
    return tokenizer_service.download_tokenizer(db, tokenizer_id, get_settings().tokenizer_dir)


@router.post("/{tokenizer_id}/upload", response_model=TokenizerRead)
def upload(tokenizer_id: uuid.UUID, file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = file.file.read(tokenizer_service.MAX_TOKENIZER_UPLOAD_BYTES + 1)
    return tokenizer_service.upload_tokenizer_archive(
        db, tokenizer_id, file.filename or "tokenizer.zip", content, get_settings().tokenizer_dir
    )


@router.delete("/{tokenizer_id}", status_code=204)
def delete_tokenizer(tokenizer_id: uuid.UUID, db: Session = Depends(get_db)):
    tokenizer_service.delete_tokenizer(db, tokenizer_id, get_settings().tokenizer_dir)
