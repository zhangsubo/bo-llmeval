"""Tokenizer 业务逻辑（PRD §8.4）。"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evalplat.core.errors import ConflictError, NotFoundError
from evalplat.models.tokenizer import Tokenizer


def create_tokenizer(db: Session, **kwargs) -> Tokenizer:
    tok = Tokenizer(status="pending", **kwargs)
    db.add(tok)
    db.commit()
    db.refresh(tok)
    return tok


def get_tokenizer(db: Session, tokenizer_id: uuid.UUID) -> Tokenizer:
    tok = db.get(Tokenizer, tokenizer_id)
    if not tok:
        raise NotFoundError("Tokenizer 不存在")
    return tok


def list_tokenizers(db: Session, skip: int = 0, limit: int = 20, status_filter: str | None = None) -> tuple[list[Tokenizer], int]:
    stmt = select(Tokenizer)
    count_stmt = select(func.count()).select_from(Tokenizer)
    if status_filter:
        stmt = stmt.where(Tokenizer.status == status_filter)
        count_stmt = count_stmt.where(Tokenizer.status == status_filter)
    total = db.scalar(count_stmt) or 0
    items = list(db.scalars(stmt.order_by(Tokenizer.created_at.desc()).offset(skip).limit(limit)))
    return items, total


def update_tokenizer_status(db: Session, tokenizer_id: uuid.UUID, status: str, error: str | None = None) -> Tokenizer:
    tok = get_tokenizer(db, tokenizer_id)
    tok.status = status
    if error:
        tok.error = error
    db.commit()
    db.refresh(tok)
    return tok


def mark_ready(db: Session, tokenizer_id: uuid.UUID, local_path: str, checksum: str | None = None, size_bytes: int | None = None) -> Tokenizer:
    tok = get_tokenizer(db, tokenizer_id)
    if tok.status == "ready":
        raise ConflictError("Tokenizer 已就绪，不可覆盖（被任务引用的版本不可修改）")
    tok.status = "ready"
    tok.local_path = local_path
    if checksum:
        tok.checksum = checksum
    if size_bytes:
        tok.size_bytes = size_bytes
    db.commit()
    db.refresh(tok)
    return tok
