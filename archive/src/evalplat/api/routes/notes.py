"""任务和 Run 备注 API。"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from evalplat.api.deps import get_db
from evalplat.schemas.note import NoteCreate, NoteList, NoteRead, NoteUpdate
from evalplat.services import note_service

router = APIRouter(tags=["备注"])


def _list(target_type: str, target_id: uuid.UUID, db: Session) -> NoteList:
    items, total = note_service.list_notes(db, target_type, target_id)
    return NoteList(items=items, total=total)


@router.get("/tasks/{task_id}/notes", response_model=NoteList)
def list_task_notes(task_id: uuid.UUID, db: Session = Depends(get_db)):
    return _list("task", task_id, db)


@router.post("/tasks/{task_id}/notes", response_model=NoteRead, status_code=201)
def create_task_note(task_id: uuid.UUID, payload: NoteCreate, db: Session = Depends(get_db)):
    return note_service.create_note(db, "task", task_id, payload.body)


@router.get("/runs/{run_id}/notes", response_model=NoteList)
def list_run_notes(run_id: uuid.UUID, db: Session = Depends(get_db)):
    return _list("run", run_id, db)


@router.post("/runs/{run_id}/notes", response_model=NoteRead, status_code=201)
def create_run_note(run_id: uuid.UUID, payload: NoteCreate, db: Session = Depends(get_db)):
    return note_service.create_note(db, "run", run_id, payload.body)


@router.patch("/notes/{note_id}", response_model=NoteRead)
def update_note(note_id: uuid.UUID, payload: NoteUpdate, db: Session = Depends(get_db)):
    return note_service.update_note(db, note_id, payload.body)
