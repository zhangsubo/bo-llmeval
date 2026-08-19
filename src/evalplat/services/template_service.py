"""测试方案模板业务逻辑（PRD §8.6）。"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evalplat.core.errors import ConflictError, NotFoundError
from evalplat.models.test_template import TestTemplate, TestTemplateVersion


def create_template(db: Session, name: str, is_builtin: bool = False, status: str = "active") -> TestTemplate:
    tpl = TestTemplate(name=name, is_builtin=is_builtin, status=status)
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return tpl


def get_template(db: Session, template_id: uuid.UUID) -> TestTemplate:
    tpl = db.get(TestTemplate, template_id)
    if not tpl:
        raise NotFoundError("测试方案模板不存在")
    return tpl


def list_templates(db: Session, skip: int = 0, limit: int = 20, is_builtin: bool | None = None) -> tuple[list[TestTemplate], int]:
    stmt = select(TestTemplate)
    count_stmt = select(func.count()).select_from(TestTemplate)
    if is_builtin is not None:
        stmt = stmt.where(TestTemplate.is_builtin == is_builtin)
        count_stmt = count_stmt.where(TestTemplate.is_builtin == is_builtin)
    total = db.scalar(count_stmt) or 0
    items = list(db.scalars(stmt.order_by(TestTemplate.created_at.desc()).offset(skip).limit(limit)))
    return items, total


def create_version(db: Session, template_id: uuid.UUID, **kwargs) -> TestTemplateVersion:
    tpl = get_template(db, template_id)
    if tpl.is_builtin:
        raise ConflictError("内置模板不可直接修改，请先复制")
    max_ver = db.scalar(
        select(func.max(TestTemplateVersion.version)).where(TestTemplateVersion.template_id == template_id)
    ) or 0
    ver = TestTemplateVersion(template_id=template_id, version=max_ver + 1, **kwargs)
    db.add(ver)
    db.commit()
    db.refresh(ver)
    return ver


def copy_template(db: Session, source_id: uuid.UUID, new_name: str) -> TestTemplate:
    """复制模板（内置模板只能复制后修改）。"""
    source = get_template(db, source_id)
    new_tpl = TestTemplate(name=new_name, is_builtin=False, status="active")
    db.add(new_tpl)
    db.flush()
    # 复制最新版本
    latest_ver = db.scalar(
        select(TestTemplateVersion)
        .where(TestTemplateVersion.template_id == source_id)
        .order_by(TestTemplateVersion.version.desc())
        .limit(1)
    )
    if latest_ver:
        new_ver = TestTemplateVersion(
            template_id=new_tpl.id,
            version=1,
            test_type=latest_ver.test_type,
            scenario=latest_ver.scenario,
            dataset_version_id=latest_ver.dataset_version_id,
            tokenizer_id=latest_ver.tokenizer_id,
            generation_params=latest_ver.generation_params,
            stream_mode=latest_ver.stream_mode,
            warmup_count=latest_ver.warmup_count,
            load_profile=latest_ver.load_profile,
            request_count=latest_ver.request_count,
            retry_policy=latest_ver.retry_policy,
            timeouts=latest_ver.timeouts,
            limits=latest_ver.limits,
            remark=latest_ver.remark,
        )
        db.add(new_ver)
    db.commit()
    db.refresh(new_tpl)
    return new_tpl
