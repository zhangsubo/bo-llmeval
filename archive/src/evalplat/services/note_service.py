"""任务和 Run 人工备注。"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evalplat.core.errors import NotFoundError
from evalplat.models.note import Note
from evalplat.models.run import Run
from evalplat.models.task import Task
from evalplat.services.audit_service import record_event


def _validate_target(db: Session, target_type: str, target_id: uuid.UUID) -> None:
    model = Task if target_type == "task" else Run if target_type == "run" else None
    if model is None or db.get(model, target_id) is None:
        raise NotFoundError("备注目标不存在")


def create_note(db: Session, target_type: str, target_id: uuid.UUID, body: str) -> Note:
    _validate_target(db, target_type, target_id)
    note = Note(target_type=target_type, target_id=target_id, body=body)
    db.add(note)
    db.flush()
    record_event(db, "note_created", target_type=target_type, target_id=target_id, diff={"note_id": str(note.id)})
    db.commit()
    db.refresh(note)
    return note


def list_notes(db: Session, target_type: str, target_id: uuid.UUID) -> tuple[list[Note], int]:
    _validate_target(db, target_type, target_id)
    base = select(Note).where(Note.target_type == target_type, Note.target_id == target_id)
    total = db.scalar(select(func.count()).select_from(Note).where(Note.target_type == target_type, Note.target_id == target_id)) or 0
    return list(db.scalars(base.order_by(Note.created_at.asc()))), total


def get_note(db: Session, note_id: uuid.UUID) -> Note:
    note = db.get(Note, note_id)
    if note is None:
        raise NotFoundError("备注不存在")
    return note


def update_note(db: Session, note_id: uuid.UUID, body: str) -> Note:
    note = get_note(db, note_id)
    note.body = body
    record_event(db, "note_updated", target_type=note.target_type, target_id=note.target_id, diff={"note_id": str(note.id)})
    db.commit()
    db.refresh(note)
    return note
