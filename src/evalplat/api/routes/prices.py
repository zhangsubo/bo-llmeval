"""价格 API 路由（追加式版本管理）。"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from evalplat.api.deps import PaginationParams, get_db
from evalplat.schemas.price import PriceCreate, PriceList, PriceRead
from evalplat.services import price_service

router = APIRouter(tags=["价格"])


@router.post("/model-deployments/{deployment_id}/prices", response_model=PriceRead, status_code=201)
def create_price(deployment_id: uuid.UUID, payload: PriceCreate, db: Session = Depends(get_db)):
    return price_service.create_price(db, deployment_id, **payload.model_dump())


@router.get("/model-deployments/{deployment_id}/prices", response_model=PriceList)
def list_prices(deployment_id: uuid.UUID, pagination: PaginationParams = Depends(), db: Session = Depends(get_db)):
    items, total = price_service.list_prices(db, deployment_id, skip=pagination.skip, limit=pagination.limit)
    return PriceList(items=items, total=total)


@router.get("/model-deployments/{deployment_id}/prices/latest", response_model=PriceRead)
def get_latest_price(deployment_id: uuid.UUID, db: Session = Depends(get_db)):
    price = price_service.get_latest_price(db, deployment_id)
    if not price:
        from evalplat.core.errors import NotFoundError
        raise NotFoundError("暂无价格记录")
    return price
