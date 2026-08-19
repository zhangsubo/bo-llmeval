"""对比组业务逻辑。"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evalplat.core.errors import ConflictError, NotFoundError
from evalplat.models.comparison_group import ComparisonGroup
from evalplat.models.model_deployment import ModelDeployment


def create_group(db: Session, name: str, default_tokenizer_id: uuid.UUID | None = None, remark: str | None = None) -> ComparisonGroup:
    group = ComparisonGroup(name=name, default_tokenizer_id=default_tokenizer_id, remark=remark)
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


def get_group(db: Session, group_id: uuid.UUID) -> ComparisonGroup:
    group = db.get(ComparisonGroup, group_id)
    if not group:
        raise NotFoundError("对比组不存在")
    return group


def list_groups(db: Session, skip: int = 0, limit: int = 20, keyword: str | None = None) -> tuple[list[ComparisonGroup], int]:
    stmt = select(ComparisonGroup)
    count_stmt = select(func.count()).select_from(ComparisonGroup)

    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(ComparisonGroup.name.ilike(like))
        count_stmt = count_stmt.where(ComparisonGroup.name.ilike(like))

    total = db.scalar(count_stmt) or 0
    items = list(db.scalars(stmt.order_by(ComparisonGroup.created_at.desc()).offset(skip).limit(limit)))
    return items, total


def add_member(db: Session, group_id: uuid.UUID, model_deployment_id: uuid.UUID) -> None:
    """添加成员到对比组。一个部署最多属于一个对比组。"""
    md = db.get(ModelDeployment, model_deployment_id)
    if not md:
        raise NotFoundError("模型部署不存在")
    if md.comparison_group_id is not None:
        raise ConflictError("该模型部署已属于其他对比组")

    md.comparison_group_id = group_id
    db.commit()


def remove_member(db: Session, group_id: uuid.UUID, model_deployment_id: uuid.UUID) -> None:
    """从对比组移除成员。"""
    md = db.get(ModelDeployment, model_deployment_id)
    if not md:
        raise NotFoundError("模型部署不存在")
    if md.comparison_group_id != group_id:
        raise NotFoundError("该模型部署不属于此对比组")

    md.comparison_group_id = None
    db.commit()
