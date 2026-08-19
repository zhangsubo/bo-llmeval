"""价格版本化管理（追加式，PRD §8.3.3）。"""

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evalplat.core.errors import NotFoundError
from evalplat.models.model_deployment import ModelDeployment
from evalplat.models.model_price import ModelPrice


def create_price(db: Session, model_deployment_id: uuid.UUID, **kwargs) -> ModelPrice:
    """创建价格（追加式，每次 INSERT 新行，price_version 自增）。"""
    md = db.get(ModelDeployment, model_deployment_id)
    if not md:
        raise NotFoundError("模型部署不存在")

    # 查询当前最大版本号
    max_version = db.scalar(
        select(func.max(ModelPrice.price_version)).where(ModelPrice.model_deployment_id == model_deployment_id)
    ) or 0

    price = ModelPrice(
        model_deployment_id=model_deployment_id,
        price_version=max_version + 1,
        source="manual",
        **kwargs,
    )
    db.add(price)
    db.commit()
    db.refresh(price)
    return price


def list_prices(db: Session, model_deployment_id: uuid.UUID, skip: int = 0, limit: int = 20) -> tuple[list[ModelPrice], int]:
    stmt = select(ModelPrice).where(ModelPrice.model_deployment_id == model_deployment_id)
    count_stmt = select(func.count()).select_from(ModelPrice).where(ModelPrice.model_deployment_id == model_deployment_id)

    total = db.scalar(count_stmt) or 0
    items = list(db.scalars(stmt.order_by(ModelPrice.price_version.desc()).offset(skip).limit(limit)))
    return items, total


def get_latest_price(db: Session, model_deployment_id: uuid.UUID) -> ModelPrice | None:
    return db.scalar(
        select(ModelPrice)
        .where(ModelPrice.model_deployment_id == model_deployment_id)
        .order_by(ModelPrice.price_version.desc())
        .limit(1)
    )
