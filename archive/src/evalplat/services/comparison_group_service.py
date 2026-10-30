"""对比组业务逻辑。"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evalplat.core.errors import ConflictError, NotFoundError
from evalplat.models.comparison_group import ComparisonGroup
from evalplat.models.model_deployment import ModelDeployment
from evalplat.models.tokenizer import Tokenizer


def create_group(db: Session, name: str, default_tokenizer_id: uuid.UUID | None = None, remark: str | None = None) -> ComparisonGroup:
    if default_tokenizer_id is not None and db.get(Tokenizer, default_tokenizer_id) is None:
        raise NotFoundError("默认 Tokenizer 不存在")
    group = ComparisonGroup(name=name, default_tokenizer_id=default_tokenizer_id, remark=remark)
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


def update_group(db: Session, group_id: uuid.UUID, **kwargs) -> ComparisonGroup:
    """Update a comparison group through an explicit service boundary."""

    group = get_group(db, group_id)
    if "default_tokenizer_id" in kwargs:
        tokenizer_id = kwargs["default_tokenizer_id"]
        if tokenizer_id is not None and db.get(Tokenizer, tokenizer_id) is None:
            raise NotFoundError("默认 Tokenizer 不存在")
    allowed = {"name", "default_tokenizer_id", "remark"}
    for key, value in kwargs.items():
        if key in allowed:
            setattr(group, key, value)
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
    if items:
        counts = dict(
            db.execute(
                select(ModelDeployment.comparison_group_id, func.count(ModelDeployment.id))
                .where(ModelDeployment.comparison_group_id.in_([item.id for item in items]))
                .group_by(ModelDeployment.comparison_group_id)
            ).all()
        )
        # `member_count` is a response-only aggregate, not persisted state.
        # Attach it to the loaded rows so the Pydantic response model exposes
        # an accurate value without issuing one query per group.
        for item in items:
            item.member_count = int(counts.get(item.id, 0))
    return items, total


def list_members(
    db: Session,
    group_id: uuid.UUID,
    *,
    skip: int = 0,
    limit: int = 20,
) -> tuple[list[ModelDeployment], int]:
    """List the model deployments assigned to one comparison group."""

    get_group(db, group_id)
    count_stmt = select(func.count()).select_from(ModelDeployment).where(
        ModelDeployment.comparison_group_id == group_id
    )
    total = db.scalar(count_stmt) or 0
    items = list(
        db.scalars(
            select(ModelDeployment)
            .where(ModelDeployment.comparison_group_id == group_id)
            .order_by(ModelDeployment.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
    )
    return items, total


def add_member(db: Session, group_id: uuid.UUID, model_deployment_id: uuid.UUID) -> None:
    """添加成员到对比组。一个部署最多属于一个对比组。"""
    get_group(db, group_id)
    md = db.scalar(
        select(ModelDeployment)
        .where(ModelDeployment.id == model_deployment_id)
        .with_for_update()
    )
    if not md:
        raise NotFoundError("模型部署不存在")
    if md.comparison_group_id is not None:
        raise ConflictError("该模型部署已属于其他对比组")

    md.comparison_group_id = group_id
    db.commit()


def remove_member(db: Session, group_id: uuid.UUID, model_deployment_id: uuid.UUID) -> None:
    """从对比组移除成员。"""
    md = db.scalar(
        select(ModelDeployment)
        .where(ModelDeployment.id == model_deployment_id)
        .with_for_update()
    )
    if not md:
        raise NotFoundError("模型部署不存在")
    if md.comparison_group_id != group_id:
        raise NotFoundError("该模型部署不属于此对比组")

    md.comparison_group_id = None
    db.commit()
