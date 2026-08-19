"""模型部署 API 路由（平铺 CRUD）。"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from evalplat.api.deps import PaginationParams, get_db
from evalplat.schemas.model_deployment import ModelDeploymentList, ModelDeploymentRead, ModelDeploymentUpdate
from evalplat.services import model_service

router = APIRouter(prefix="/model-deployments", tags=["模型部署"])


@router.get("", response_model=ModelDeploymentList)
def list_model_deployments(
    pagination: PaginationParams = Depends(),
    connection_id: uuid.UUID | None = Query(None),
    status: str | None = Query(None),
    db: Session = Depends(get_db),
):
    items, total = model_service.list_model_deployments(db, connection_id=connection_id, skip=pagination.skip, limit=pagination.limit, status_filter=status)
    return ModelDeploymentList(items=items, total=total)


@router.get("/{deployment_id}", response_model=ModelDeploymentRead)
def get_model_deployment(deployment_id: uuid.UUID, db: Session = Depends(get_db)):
    return model_service.get_model_deployment(db, deployment_id)


@router.patch("/{deployment_id}", response_model=ModelDeploymentRead)
def update_model_deployment(deployment_id: uuid.UUID, payload: ModelDeploymentUpdate, db: Session = Depends(get_db)):
    return model_service.update_model_deployment(db, deployment_id, **payload.model_dump(exclude_unset=True))
