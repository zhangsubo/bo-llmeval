"""测试方案模板业务逻辑（PRD §8.6）。"""

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from evalplat.core.errors import ConflictError, NotFoundError
from evalplat.models.test_template import TestTemplate, TestTemplateVersion

BUILTIN_TEMPLATES: tuple[dict, ...] = (
    {"name": "冒烟测试", "test_type": "performance", "scenario": "smoke", "request_count": 1, "warmup_count": 0, "load_profile": {"mode": "fixed_concurrency", "concurrency": 1}},
    {"name": "快速基线", "test_type": "performance", "scenario": "quick_baseline", "request_count": 20, "warmup_count": 1, "load_profile": {"mode": "fixed_concurrency", "concurrency": 2}},
    {"name": "标准基线", "test_type": "performance", "scenario": "standard_baseline", "request_count": 100, "warmup_count": 3, "load_profile": {"mode": "fixed_concurrency", "concurrency": 4}},
    {"name": "并发梯度", "test_type": "performance", "scenario": "concurrency", "request_count": 100, "warmup_count": 3, "load_profile": {"mode": "fixed_concurrency", "concurrency": 8}},
    {"name": "固定 RPS", "test_type": "performance", "scenario": "fixed_rps", "request_count": 100, "warmup_count": 3, "load_profile": {"mode": "fixed_rps", "rps": 5, "max_inflight": 8}},
    {"name": "稳定性", "test_type": "performance", "scenario": "stability", "request_count": 500, "warmup_count": 3, "load_profile": {"mode": "fixed_rps", "rps": 2, "max_inflight": 8}},
    {"name": "轻量质量", "test_type": "quality", "scenario": "quality_light", "evalscope_datasets": ["ifeval"], "request_count": 20, "warmup_count": 0, "retry_policy": {"max_retries": 2}, "load_profile": {"mode": "fixed_concurrency", "concurrency": 2}},
    {"name": "代码生成", "test_type": "code", "scenario": "code_generation", "request_count": 20, "warmup_count": 0, "retry_policy": {"max_retries": 2}, "load_profile": {"mode": "fixed_concurrency", "concurrency": 2}},
)
TEMPLATE_STATUSES = {"draft", "active", "archived"}


def create_template(db: Session, name: str, is_builtin: bool = False, status: str = "active") -> TestTemplate:
    if status not in TEMPLATE_STATUSES:
        raise ValueError("模板状态必须为 draft、active 或 archived")
    tpl = TestTemplate(name=name, is_builtin=is_builtin, status=status)
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return tpl


def ensure_builtin_templates(db: Session) -> int:
    """幂等创建首版内置模板及其第一版本，返回新增模板数量。"""

    created = 0
    for definition in BUILTIN_TEMPLATES:
        template = db.scalar(
            select(TestTemplate).where(
                TestTemplate.name == definition["name"], TestTemplate.is_builtin.is_(True)
            )
        )
        if template:
            continue
        template = TestTemplate(name=definition["name"], is_builtin=True, status="active")
        db.add(template)
        db.flush()
        version_values = {key: value for key, value in definition.items() if key != "name"}
        db.add(TestTemplateVersion(template_id=template.id, version=1, **version_values))
        created += 1
    if created:
        db.commit()
    return created


def get_template(db: Session, template_id: uuid.UUID) -> TestTemplate:
    tpl = db.get(TestTemplate, template_id)
    if not tpl:
        raise NotFoundError("测试方案模板不存在")
    return tpl


def list_templates(
    db: Session,
    skip: int = 0,
    limit: int = 20,
    is_builtin: bool | None = None,
    keyword: str | None = None,
) -> tuple[list[TestTemplate], int]:
    stmt = select(TestTemplate)
    count_stmt = select(func.count()).select_from(TestTemplate)
    if is_builtin is not None:
        stmt = stmt.where(TestTemplate.is_builtin == is_builtin)
        count_stmt = count_stmt.where(TestTemplate.is_builtin == is_builtin)
    if keyword and (keyword := keyword.strip()):
        condition = TestTemplate.name.ilike(f"%{keyword}%")
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    total = db.scalar(count_stmt) or 0
    items = list(db.scalars(stmt.order_by(TestTemplate.created_at.desc()).offset(skip).limit(limit)))
    return items, total


def create_version(db: Session, template_id: uuid.UUID, **kwargs) -> TestTemplateVersion:
    tpl = db.scalar(select(TestTemplate).where(TestTemplate.id == template_id).with_for_update())
    if not tpl:
        raise NotFoundError("测试方案模板不存在")
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


def list_versions(
    db: Session,
    template_id: uuid.UUID,
    skip: int = 0,
    limit: int = 20,
) -> tuple[list[TestTemplateVersion], int]:
    """列出模板版本，最新版本优先。"""

    # Validate the parent first so a typo does not look like an empty template.
    get_template(db, template_id)
    count = db.scalar(
        select(func.count())
        .select_from(TestTemplateVersion)
        .where(TestTemplateVersion.template_id == template_id)
    ) or 0
    items = list(
        db.scalars(
            select(TestTemplateVersion)
            .where(TestTemplateVersion.template_id == template_id)
            .order_by(TestTemplateVersion.version.desc())
            .offset(skip)
            .limit(limit)
        )
    )
    return items, count


def list_all_versions(
    db: Session,
    skip: int = 0,
    limit: int = 20,
    template_id: uuid.UUID | None = None,
    version_id: uuid.UUID | None = None,
    keyword: str | None = None,
) -> tuple[list[TestTemplateVersion], int]:
    """分页查询模板版本，按模板名称、场景或评测类型筛选。"""

    stmt = select(TestTemplateVersion).join(TestTemplate, TestTemplate.id == TestTemplateVersion.template_id)
    count_stmt = (
        select(func.count())
        .select_from(TestTemplateVersion)
        .join(TestTemplate, TestTemplate.id == TestTemplateVersion.template_id)
    )
    if template_id:
        condition = TestTemplateVersion.template_id == template_id
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    if version_id:
        condition = TestTemplateVersion.id == version_id
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    if keyword and (keyword := keyword.strip()):
        like = f"%{keyword}%"
        condition = or_(
            TestTemplate.name.ilike(like),
            TestTemplateVersion.scenario.ilike(like),
            TestTemplateVersion.test_type.ilike(like),
        )
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    total = db.scalar(count_stmt) or 0
    items = list(
        db.scalars(
            stmt.order_by(TestTemplateVersion.created_at.desc(), TestTemplateVersion.version.desc())
            .offset(skip)
            .limit(limit)
        )
    )
    return items, total


def copy_template(db: Session, source_id: uuid.UUID, new_name: str) -> TestTemplate:
    """复制模板（内置模板只能复制后修改）。"""
    get_template(db, source_id)
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
            evalscope_datasets=latest_ver.evalscope_datasets,
            input_length_preset=latest_ver.input_length_preset,
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
