"""测试方案模板 API 路由。"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from evalplat.api.deps import PaginationParams, get_db
from evalplat.schemas.template import (
    TemplateCopyRequest, TemplateCreate, TemplateList, TemplateRead,
    TemplateVersionCreate, TemplateVersionRead,
)
from evalplat.services import template_service

router = APIRouter(tags=["测试方案模板"])


@router.post("/templates", response_model=TemplateRead, status_code=201)
def create_template(payload: TemplateCreate, db: Session = Depends(get_db)):
    return template_service.create_template(db, **payload.model_dump())


@router.get("/templates", response_model=TemplateList)
def list_templates(
    pagination: PaginationParams = Depends(),
    is_builtin: bool | None = Query(None),
    db: Session = Depends(get_db),
):
    items, total = template_service.list_templates(db, skip=pagination.skip, limit=pagination.limit, is_builtin=is_builtin)
    return TemplateList(items=items, total=total)


@router.get("/templates/{template_id}", response_model=TemplateRead)
def get_template(template_id: uuid.UUID, db: Session = Depends(get_db)):
    return template_service.get_template(db, template_id)


@router.post("/templates/{template_id}/versions", response_model=TemplateVersionRead, status_code=201)
def create_version(template_id: uuid.UUID, payload: TemplateVersionCreate, db: Session = Depends(get_db)):
    return template_service.create_version(db, template_id, **payload.model_dump())


@router.post("/templates/{template_id}/copy", response_model=TemplateRead, status_code=201)
def copy_template(template_id: uuid.UUID, body: TemplateCopyRequest, db: Session = Depends(get_db)):
    return template_service.copy_template(db, template_id, body.new_name)
