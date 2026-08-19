"""对比组 API 路由。"""

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from evalplat.api.deps import PaginationParams, get_db
from evalplat.schemas.comparison_group import ComparisonGroupCreate, ComparisonGroupList, ComparisonGroupRead, ComparisonGroupUpdate
from evalplat.services import comparison_group_service

router = APIRouter(prefix="/comparison-groups", tags=["对比组"])


@router.post("", response_model=ComparisonGroupRead, status_code=201)
def create_group(payload: ComparisonGroupCreate, db: Session = Depends(get_db)):
    return comparison_group_service.create_group(db, **payload.model_dump())


@router.get("", response_model=ComparisonGroupList)
def list_groups(
    pagination: PaginationParams = Depends(),
    keyword: str | None = Query(None),
    db: Session = Depends(get_db),
):
    items, total = comparison_group_service.list_groups(db, skip=pagination.skip, limit=pagination.limit, keyword=keyword)
    return ComparisonGroupList(items=items, total=total)


@router.get("/{group_id}", response_model=ComparisonGroupRead)
def get_group(group_id: uuid.UUID, db: Session = Depends(get_db)):
    return comparison_group_service.get_group(db, group_id)


@router.patch("/{group_id}", response_model=ComparisonGroupRead)
def update_group(group_id: uuid.UUID, payload: ComparisonGroupUpdate, db: Session = Depends(get_db)):
    group = comparison_group_service.get_group(db, group_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(group, key, value)
    db.commit()
    db.refresh(group)
    return group


class MemberRequest(BaseModel):
    model_deployment_id: uuid.UUID


@router.post("/{group_id}/members")
def add_member(group_id: uuid.UUID, body: MemberRequest, db: Session = Depends(get_db)):
    comparison_group_service.add_member(db, group_id, body.model_deployment_id)
    return {"status": "ok"}


@router.delete("/{group_id}/members/{deployment_id}")
def remove_member(group_id: uuid.UUID, deployment_id: uuid.UUID, db: Session = Depends(get_db)):
    comparison_group_service.remove_member(db, group_id, deployment_id)
    return {"status": "ok"}
