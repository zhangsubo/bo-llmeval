"""Tokenizer API 路由。"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from evalplat.api.deps import PaginationParams, get_db
from evalplat.schemas.tokenizer import TokenizerCreate, TokenizerList, TokenizerRead
from evalplat.services import tokenizer_service

router = APIRouter(prefix="/tokenizers", tags=["Tokenizer"])


@router.post("", response_model=TokenizerRead, status_code=201)
def create_tokenizer(payload: TokenizerCreate, db: Session = Depends(get_db)):
    return tokenizer_service.create_tokenizer(db, **payload.model_dump())


@router.get("", response_model=TokenizerList)
def list_tokenizers(
    pagination: PaginationParams = Depends(),
    status: str | None = Query(None),
    db: Session = Depends(get_db),
):
    items, total = tokenizer_service.list_tokenizers(db, skip=pagination.skip, limit=pagination.limit, status_filter=status)
    return TokenizerList(items=items, total=total)


@router.get("/{tokenizer_id}", response_model=TokenizerRead)
def get_tokenizer(tokenizer_id: uuid.UUID, db: Session = Depends(get_db)):
    return tokenizer_service.get_tokenizer(db, tokenizer_id)


@router.patch("/{tokenizer_id}/status", response_model=TokenizerRead)
def update_status(tokenizer_id: uuid.UUID, status: str = Query(...), error: str | None = Query(None), db: Session = Depends(get_db)):
    return tokenizer_service.update_tokenizer_status(db, tokenizer_id, status, error)
