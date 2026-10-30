"""评测任务业务逻辑。"""

import uuid
import copy
from datetime import datetime, timezone

from sqlalchemy import String, cast, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from evalplat.core.errors import ConflictError, NotFoundError
from evalplat.models.model_price import ModelPrice
from evalplat.models.connection import Connection
from evalplat.models.model_deployment import ModelDeployment
from evalplat.models.supplier import Supplier
from evalplat.models.code_execution import CodeExecution
from evalplat.models.dataset import Dataset, DatasetVersion
from evalplat.models.run import Run
from evalplat.models.task import Task
from evalplat.models.test_template import TestTemplateVersion
from evalplat.models.tokenizer import Tokenizer
from evalplat.models.system_setting import SystemSetting
from evalplat.services.audit_service import record_event
from evalplat.core.ssrf import SSRFGuard
from evalplat.services.task_precheck_service import (
    has_blocking_failure,
    merge_restrictive_policy,
    run_task_precheck,
)
from evalplat.services.length_preset_service import get_length_preset

_RUN_MODES = {"serial", "parallel"}


def _validate_evaluation_config(
    evaluation_kind: object,
    evaluation_engine: object,
    quality_mode: object,
) -> None:
    """Validate the supported evaluation engine/mode combinations."""

    if evaluation_kind not in {"performance", "quality"}:
        raise ValueError("evaluation_kind 必须为 performance 或 quality")
    if evaluation_engine not in {"native", "evalscope"}:
        raise ValueError("evaluation_engine 必须为 native 或 evalscope")
    if quality_mode not in {"text", "code"}:
        raise ValueError("quality_mode 必须为 text 或 code")
    if quality_mode == "code" and evaluation_kind != "quality":
        raise ValueError("quality_mode=code 仅支持 quality 评测任务")
    if quality_mode == "code" and evaluation_engine != "native":
        raise ValueError("quality_mode=code 仅支持 native 评测引擎")
    if evaluation_engine == "native" and not (
        evaluation_kind == "quality" and quality_mode == "code"
    ):
        raise ValueError("性能和文本质量必须使用 EvalScope 评测引擎")


def create_task(db: Session, **kwargs) -> Task:
    """创建任务（状态默认 draft）。"""
    run_mode = kwargs.get("run_mode")
    if run_mode not in _RUN_MODES:
        raise ValueError("运行模式必须为 serial 或 parallel")
    idempotency_key = kwargs.get("idempotency_key")
    if idempotency_key:
        existing = db.scalar(select(Task).where(Task.idempotency_key == idempotency_key))
        if existing is not None:
            return existing
    template_version_id = kwargs.get("template_version_id")
    if template_version_id:
        template_version = db.get(TestTemplateVersion, template_version_id)
        if not template_version:
            raise NotFoundError("模板版本不存在")
        template_values = {
            "test_type": template_version.test_type,
            "scenario": template_version.scenario,
            "dataset_version_id": template_version.dataset_version_id,
            "tokenizer_id": template_version.tokenizer_id,
            "generation_params": template_version.generation_params,
            "evalscope_datasets": template_version.evalscope_datasets,
            "input_length_preset": template_version.input_length_preset,
            "warmup_count": template_version.warmup_count,
            "load_profile": template_version.load_profile,
            "request_count": template_version.request_count,
            "retry_policy": template_version.retry_policy,
            "timeouts": template_version.timeouts,
            "limits": template_version.limits,
        }
        if template_version.stream_mode is not None:
            template_values["stream"] = template_version.stream_mode == "stream"
        # A template version defines the evaluation contract.  The request
        # schema supplies defaults for these fields, so treating every
        # present key as an explicit override makes direct API/template use
        # silently turn a code template into an invalid performance task.
        # Materialize the type-specific fields from the template itself;
        # other runtime fields retain the explicit-request-wins behavior.
        if template_version.test_type in {"quality", "code"}:
            kwargs["evaluation_kind"] = "quality"
        elif template_version.test_type == "performance":
            kwargs["evaluation_kind"] = "performance"
        if template_version.test_type == "code":
            kwargs["quality_mode"] = "code"
            kwargs["evaluation_engine"] = "native"
        elif template_version.test_type in {"quality", "performance"}:
            kwargs["quality_mode"] = "text"
            kwargs["evaluation_engine"] = "evalscope"
        for key, value in template_values.items():
            if key not in kwargs and value is not None:
                kwargs[key] = value
    # Draft 配置暂存于 JSON；只有 submit 成功时才视为不可变快照。
    deployment_ids = kwargs.pop("model_deployment_ids", []) or []
    test_type = kwargs.pop("test_type", None)
    scenario = kwargs.pop("scenario", None)
    dataset_version_id = kwargs.pop("dataset_version_id", None)
    tokenizer_id = kwargs.pop("tokenizer_id", None)
    generation_params = kwargs.pop("generation_params", None)
    input_length_preset = kwargs.pop("input_length_preset", None)
    load_profile = kwargs.pop("load_profile", None)
    request_count = kwargs.pop("request_count", 1)
    warmup_count = kwargs.pop("warmup_count", 3)
    stream = kwargs.pop("stream", False)
    retry_policy = kwargs.pop("retry_policy", None)
    timeouts = kwargs.pop("timeouts", None)
    limits = kwargs.pop("limits", None)
    messages = kwargs.pop("messages", None)
    evaluation_kind = kwargs.pop("evaluation_kind", "performance")
    quality_mode = kwargs.pop("quality_mode", "text")
    # EvalScope is the only runtime evaluator for performance/text quality.
    # Native remains reserved for the separate Docker-backed code scorer.
    evaluation_engine = kwargs.pop(
        "evaluation_engine", "native" if quality_mode == "code" else "evalscope"
    )
    quality_samples = kwargs.pop("quality_samples", None)
    evalscope_datasets = kwargs.pop("evalscope_datasets", None)
    dataset_args = kwargs.pop("dataset_args", None)
    code_sandbox_image = kwargs.pop("code_sandbox_image", None)
    code_timeout_seconds = kwargs.pop("code_timeout_seconds", None)
    code_memory_mb = kwargs.pop("code_memory_mb", None)
    code_cpus = kwargs.pop("code_cpus", None)
    code_pids_limit = kwargs.pop("code_pids_limit", None)
    _validate_evaluation_config(evaluation_kind, evaluation_engine, quality_mode)
    preset = get_length_preset(input_length_preset)
    if preset is not None:
        generation_params = copy.deepcopy(generation_params) if isinstance(generation_params, dict) else {}
        if generation_params.get("max_tokens") is None and generation_params.get("max_completion_tokens") is None:
            generation_params["max_tokens"] = preset.max_output_tokens
    draft_config = {
        "model_deployment_ids": [str(value) for value in deployment_ids],
        "test_type": test_type,
        "scenario": scenario,
        "dataset_version_id": str(dataset_version_id) if dataset_version_id else None,
        "tokenizer_id": str(tokenizer_id) if tokenizer_id else None,
        "generation_params": copy.deepcopy(generation_params),
        "input_length_preset": preset.key if preset is not None else None,
        "load_profile": copy.deepcopy(load_profile),
        "request_count": request_count,
        "warmup_count": warmup_count,
        "stream": stream,
        "retry_policy": copy.deepcopy(retry_policy),
        "timeouts": copy.deepcopy(timeouts),
        "limits": copy.deepcopy(limits),
        "messages": copy.deepcopy(messages),
        "evaluation_kind": evaluation_kind,
        "evaluation_engine": evaluation_engine,
        "quality_mode": quality_mode,
        "quality_samples": copy.deepcopy(quality_samples),
        "evalscope_datasets": copy.deepcopy(evalscope_datasets),
        "dataset_args": copy.deepcopy(dataset_args),
        "code_sandbox_image": code_sandbox_image,
        "code_timeout_seconds": code_timeout_seconds,
        "code_memory_mb": code_memory_mb,
        "code_cpus": code_cpus,
        "code_pids_limit": code_pids_limit,
    }
    kwargs["config_snapshot"] = draft_config
    task = Task(**kwargs)
    db.add(task)
    db.flush()
    record_event(db, "task_created", target_type="task", target_id=task.id, diff={"name": task.name, "run_mode": task.run_mode})
    try:
        db.commit()
    except IntegrityError:
        # A concurrent creator may win the unique idempotency race between the
        # read above and INSERT. Return that durable task to make retries safe.
        db.rollback()
        if idempotency_key:
            existing = db.scalar(select(Task).where(Task.idempotency_key == idempotency_key))
            if existing is not None:
                return existing
        raise
    db.refresh(task)
    return task


def get_task(db: Session, task_id: uuid.UUID) -> Task:
    """获取单个任务。"""
    task = db.get(Task, task_id)
    if not task:
        raise NotFoundError("任务不存在")
    return task


def list_tasks(
    db: Session,
    skip: int = 0,
    limit: int = 20,
    status_filter: str | None = None,
    keyword: str | None = None,
    supplier_id: uuid.UUID | None = None,
    model_deployment_id: uuid.UUID | None = None,
    comparison_group_id: uuid.UUID | None = None,
    template_id: uuid.UUID | None = None,
    template_version_id: uuid.UUID | None = None,
    test_type: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> tuple[list[Task], int]:
    """列出任务；筛选和分页均在数据库端执行。"""
    stmt = select(Task)
    count_stmt = select(func.count()).select_from(Task)

    if status_filter:
        stmt = stmt.where(Task.status == status_filter)
        count_stmt = count_stmt.where(Task.status == status_filter)
    if keyword and (keyword := keyword.strip()):
        like = f"%{keyword}%"
        condition = or_(
            Task.name.ilike(like),
            cast(Task.id, String).ilike(like),
        )
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    if template_version_id:
        stmt = stmt.where(Task.template_version_id == template_version_id)
        count_stmt = count_stmt.where(Task.template_version_id == template_version_id)
    if template_id:
        template_condition = exists(
            select(TestTemplateVersion.id).where(
                TestTemplateVersion.id == Task.template_version_id,
                TestTemplateVersion.template_id == template_id,
            )
        )
        stmt = stmt.where(template_condition)
        count_stmt = count_stmt.where(template_condition)
    if test_type:
        test_type_condition = exists(
            select(TestTemplateVersion.id).where(
                TestTemplateVersion.id == Task.template_version_id,
                TestTemplateVersion.test_type == test_type,
            )
        )
        stmt = stmt.where(test_type_condition)
        count_stmt = count_stmt.where(test_type_condition)
    if created_from is not None:
        stmt = stmt.where(Task.created_at >= created_from)
        count_stmt = count_stmt.where(Task.created_at >= created_from)
    if created_to is not None:
        stmt = stmt.where(Task.created_at <= created_to)
        count_stmt = count_stmt.where(Task.created_at <= created_to)

    deployment_ids: list[uuid.UUID] | None = None
    if supplier_id or model_deployment_id or comparison_group_id:
        deployment_stmt = select(ModelDeployment.id)
        if supplier_id:
            deployment_stmt = deployment_stmt.join(
                Connection, Connection.id == ModelDeployment.connection_id
            ).where(Connection.supplier_id == supplier_id)
        if model_deployment_id:
            deployment_stmt = deployment_stmt.where(ModelDeployment.id == model_deployment_id)
        if comparison_group_id:
            deployment_stmt = deployment_stmt.where(
                ModelDeployment.comparison_group_id == comparison_group_id
            )
        deployment_ids = list(db.scalars(deployment_stmt))
        if not deployment_ids:
            # Keep the query server-side while avoiding an unbounded task scan.
            impossible = Task.id.is_(None)
            stmt = stmt.where(impossible)
            count_stmt = count_stmt.where(impossible)
        else:
            run_condition = exists(
                select(Run.id).where(
                    Run.task_id == Task.id,
                    Run.model_deployment_id.in_(deployment_ids),
                )
            )
            # Draft tasks have no Run yet; their target IDs live in the draft
            # JSON. The cast keeps this fallback portable across PostgreSQL and
            # the SQLite test harness while the normal path uses indexed FKs.
            draft_conditions = [
                cast(Task.config_snapshot, String).ilike(f"%{deployment_id}%")
                for deployment_id in deployment_ids
            ]
            deployment_condition = or_(run_condition, *draft_conditions)
            stmt = stmt.where(deployment_condition)
            count_stmt = count_stmt.where(deployment_condition)

    total = db.scalar(count_stmt) or 0
    items = list(db.scalars(stmt.order_by(Task.created_at.desc()).offset(skip).limit(limit)))
    return items, total


def update_task(db: Session, task_id: uuid.UUID, **kwargs) -> Task:
    """更新任务（仅 draft 状态可改）。"""
    task = get_task(db, task_id)
    if task.status != "draft":
        raise ConflictError("仅 draft 状态的任务可编辑")
    config_keys = {
        "model_deployment_ids", "dataset_version_id", "tokenizer_id", "generation_params",
        "load_profile", "input_length_preset", "request_count", "warmup_count", "stream", "retry_policy", "messages",
        "test_type", "scenario", "timeouts", "limits",
        "evaluation_kind", "evaluation_engine", "quality_mode", "quality_samples", "evalscope_datasets",
        "dataset_args", "code_sandbox_image", "code_timeout_seconds", "code_memory_mb", "code_cpus",
        "code_pids_limit",
    }
    config = copy.deepcopy(task.config_snapshot or {})
    for key, value in kwargs.items():
        if key in {"name", "remark"}:
            setattr(task, key, value)
        elif key in config_keys:
            if key == "model_deployment_ids":
                config[key] = [str(item) for item in (value or [])]
            elif key in {"dataset_version_id", "tokenizer_id"}:
                config[key] = str(value) if value is not None else None
            else:
                config[key] = copy.deepcopy(value)
    preset = get_length_preset(config.get("input_length_preset"))
    if preset is not None:
        generation_params = config.get("generation_params")
        if not isinstance(generation_params, dict):
            generation_params = {}
            config["generation_params"] = generation_params
        if generation_params.get("max_tokens") is None and generation_params.get("max_completion_tokens") is None:
            generation_params["max_tokens"] = preset.max_output_tokens
    _validate_evaluation_config(
        config.get("evaluation_kind", "performance"),
        config.get("evaluation_engine", "evalscope"),
        config.get("quality_mode", "text"),
    )
    task.config_snapshot = config
    db.commit()
    db.refresh(task)
    return task


def submit_task(
    db: Session,
    task_id: uuid.UUID,
    ssrf_guard: SSRFGuard | None = None,
) -> Task:
    """提交任务：draft → queued。"""
    # Serialize submit/cancel/retry/report/cleanup transitions on the Task
    # row. This prevents two concurrent submits from creating duplicate Runs.
    task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None:
        raise NotFoundError("任务不存在")
    if task.status != "draft":
        raise ConflictError("仅 draft 状态的任务可提交")

    config = task.config_snapshot or {}
    _validate_evaluation_config(
        config.get("evaluation_kind", "performance"),
        config.get("evaluation_engine", "evalscope"),
        config.get("quality_mode", "text"),
    )
    # Keep all blocking rules in a dedicated service. The task service owns
    # snapshot persistence, while precheck owns validation policy.
    checks, deployment_contexts = run_task_precheck(
        db,
        config,
        run_mode=task.run_mode,
        ssrf_guard=ssrf_guard,
    )
    if config.get("evaluation_engine", "evalscope") == "evalscope" and config.get("evaluation_kind") == "quality":
        datasets = config.get("evalscope_datasets")
        if not isinstance(datasets, list) or not datasets or not all(isinstance(item, str) and item.strip() for item in datasets):
            checks.append({"name": "evalscope_datasets", "status": "failed", "detail": "必须配置至少一个 EvalScope benchmark"})

    failed = [check for check in checks if check.get("status") == "failed"]
    task.precheck_result = {
        "status": "failed" if has_blocking_failure(checks) else "passed",
        "checks": checks,
    }
    if failed:
        db.commit()
        raise ConflictError("任务预检未通过")

    if not deployment_contexts:
        checks.append({"name": "targets", "status": "failed", "detail": "至少选择一个模型部署"})
        task.precheck_result = {"status": "failed", "checks": checks}
        db.commit()
        raise ConflictError("任务必须至少选择一个模型部署")

    # Freeze one independent snapshot per Run, including the current price
    # version.  The builder is shared with manual retry so fields cannot drift
    # between the two lifecycle paths.
    for seq, (deployment, connection) in enumerate(deployment_contexts, start=1):
        run_snapshot = build_run_snapshot(db, config, deployment, connection)
        db.add(Run(task_id=task.id, model_deployment_id=deployment.id, seq=seq, run_snapshot=run_snapshot, queued_at=datetime.now(timezone.utc)))
    task.status = "queued"
    task.queued_at = datetime.now(timezone.utc)
    record_event(db, "task_submitted", target_type="task", target_id=task.id, diff={"run_count": len(deployment_contexts)})
    db.commit()
    db.refresh(task)
    return task


def build_run_snapshot(
    db: Session,
    config: dict[str, object],
    deployment: ModelDeployment,
    connection: Connection,
) -> dict[str, object]:
    """Build the immutable execution snapshot for one deployment.

    Task submission and manual retry both call this function.  Values that are
    part of the task configuration win for runtime settings; connection
    values remain the fallback for legacy drafts that did not persist them.
    Every nested value is copied so later ORM mutations cannot alter a queued
    Run's historical configuration.
    """

    latest_price = db.scalar(
        select(ModelPrice)
        .where(ModelPrice.model_deployment_id == deployment.id)
        .order_by(ModelPrice.price_version.desc())
        .limit(1)
    )
    supplier = db.get(Supplier, connection.supplier_id)
    configured_timeouts = config.get("timeouts")
    configured_limits = config.get("limits")
    # Task policies may tighten a connection, never widen its hard limits.
    # Keep the effective values in the immutable snapshot so execution and
    # precheck use exactly the same policy.
    timeouts = merge_restrictive_policy(configured_timeouts, connection.timeouts)
    limits = merge_restrictive_policy(configured_limits, connection.limits)
    return {
        **copy.deepcopy(config),
        "model_deployment_id": str(deployment.id),
        "model_id": deployment.model_id,
        "model_display_name": deployment.display_name,
        "context_window": deployment.context_window,
        "max_output_tokens": deployment.max_output_tokens,
        "supports_streaming": deployment.supports_streaming,
        "supports_non_streaming": deployment.supports_non_streaming,
        "supplier_id": str(connection.supplier_id),
        "supplier_name": supplier.name if supplier is not None else None,
        "supplier_short_name": supplier.short_name if supplier is not None else None,
        "connection_id": str(deployment.connection_id),
        "connection_name": connection.name,
        "base_url": connection.base_url,
        "region": connection.region,
        "service_tier": connection.service_tier,
        "tls_skip_verify": connection.tls_skip_verify,
        "timeouts": timeouts,
        "limits": limits,
        "credential_version": connection.credential_version,
        "price_version": latest_price.price_version if latest_price else None,
        "price": {
            "input_token_price": str(latest_price.input_token_price),
            "output_token_price": str(latest_price.output_token_price),
            "cached_token_price": str(latest_price.cached_token_price) if latest_price.cached_token_price is not None else None,
            "unit": latest_price.unit,
            "currency": latest_price.currency,
            "exchange_rate": str(latest_price.exchange_rate),
        } if latest_price else None,
        "currency": latest_price.currency if latest_price else None,
        "base_currency": _base_currency(db),
        "dataset_snapshot": _dataset_snapshot(db, config.get("dataset_version_id")),
        "tokenizer_snapshot": _tokenizer_snapshot(db, config.get("tokenizer_id")),
    }


def _dataset_snapshot(db: Session, raw_id: object) -> dict[str, object] | None:
    """Copy immutable dataset metadata into each Run snapshot."""

    if not raw_id:
        return None
    try:
        version = db.get(DatasetVersion, uuid.UUID(str(raw_id)))
    except (TypeError, ValueError):
        version = None
    if version is None:
        return None
    dataset = db.get(Dataset, version.dataset_id)
    return {
        "id": str(version.id),
        "dataset_id": str(version.dataset_id),
        "kind": dataset.kind if dataset is not None else None,
        "name": dataset.name if dataset is not None else None,
        "builtin_key": dataset.builtin_key if dataset is not None else None,
        "version": version.version,
        "source": version.source,
        "license": version.license,
        "source_id": version.source_id,
        "source_revision": version.source_revision,
        "source_split": version.source_split,
        "source_subset": version.source_subset,
        "checksum": version.checksum,
        "sample_count": version.sample_count,
        "language": version.language,
        "capability_tags": copy.deepcopy(version.capability_tags),
        "default_prompt": version.default_prompt,
        "scoring_method": version.scoring_method,
        "field_mapping": copy.deepcopy(version.field_mapping),
        "token_length_stats": copy.deepcopy(version.token_length_stats),
        "storage_path": version.storage_path,
    }


def _tokenizer_snapshot(db: Session, raw_id: object) -> dict[str, object] | None:
    """Copy tokenizer source/revision/checksum metadata for reproducibility."""

    if not raw_id:
        return None
    try:
        tokenizer = db.get(Tokenizer, uuid.UUID(str(raw_id)))
    except (TypeError, ValueError):
        tokenizer = None
    if tokenizer is None:
        return None
    return {
        "id": str(tokenizer.id),
        "name": tokenizer.name,
        "source": tokenizer.source,
        "resource_id": tokenizer.resource_id,
        "revision": tokenizer.revision,
        "checksum": tokenizer.checksum,
        "size_bytes": tokenizer.size_bytes,
        "status": tokenizer.status,
        "local_path": tokenizer.local_path,
    }


def _base_currency(db: Session) -> str:
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == "base_currency"))
    raw = row.value if row is not None else None
    if isinstance(raw, dict):
        raw = raw.get("value", raw.get("currency"))
    return str(raw).upper() if raw else "CNY"


def cancel_task(db: Session, task_id: uuid.UUID) -> Task:
    """取消任务；排队 Run 立即取消，运行中 Run 由 worker 收敛。"""
    task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None:
        raise NotFoundError("任务不存在")
    # Read and lock all active children while the parent Task lock is held.
    # A Worker can commit a queued Run as ``running`` before it refreshes the
    # parent Task; marking those children explicitly prevents a late
    # ``finish_run(completed)`` from resurrecting a task-level cancellation.
    active_runs = list(
        db.scalars(
            select(Run)
            .where(
                Run.task_id == task.id,
                Run.status.in_(
                    ("queued", "running", "cancelling", "awaiting_code")
                ),
            )
            .with_for_update()
        )
    )
    active_in_flight = any(
        run.status in {"running", "cancelling", "awaiting_code"}
        for run in active_runs
    )
    if task.status in ("draft", "queued") and not active_in_flight:
        task.status = "cancelled"
        task.finished_at = datetime.now(timezone.utc)
    elif task.status in ("draft", "queued", "running"):
        task.status = "cancelling"
    elif task.status in ("cancelling", "cancelled"):
        return task
    else:
        raise ConflictError("终态任务不可取消")
    queued_runs = [run for run in active_runs if run.status == "queued"]
    awaiting_code_runs = [run for run in active_runs if run.status == "awaiting_code"]
    for run in queued_runs:
        run.status = "cancelled"
        run.finished_at = datetime.now(timezone.utc)
    for run in active_runs:
        if run.status in {"running", "awaiting_code"}:
            run.status = "cancelling"
    # Pending code jobs belong to this Task's awaiting-code Runs. Cancel them
    # under row locks so a code worker cannot claim the same job concurrently.
    pending_code_jobs = list(
        db.scalars(
            select(CodeExecution)
            .join(Run, CodeExecution.run_id == Run.id)
            .where(Run.task_id == task.id, CodeExecution.status == "pending")
            .with_for_update(skip_locked=True)
        )
    )
    code_run_ids = {job.run_id for job in pending_code_jobs}
    for job in pending_code_jobs:
        job.status = "cancelled"
        job.error_category = "task_cancelled"
        job.error_summary = "任务已取消，代码作业未执行"
        job.finished_at = datetime.now(timezone.utc)
    record_event(
        db,
        "task_cancelled",
        target_type="task",
        target_id=task.id,
        diff={
            "cancelled_run_count": len(queued_runs),
            "cancelling_run_count": sum(
                run.status == "cancelling" for run in active_runs
            ),
        },
    )
    db.commit()
    if code_run_ids or awaiting_code_runs:
        from evalplat.services.code_execution_service import reconcile_run_after_code_execution

        for run_id in code_run_ids | {run.id for run in awaiting_code_runs}:
            reconcile_run_after_code_execution(db, run_id)
    db.refresh(task)
    return task
