"""任务提交前预检、快照和 Run 创建测试。"""

import uuid

import pytest

from evalplat.core.errors import ConflictError
from evalplat.models.connection import Connection
from evalplat.models.dataset import Dataset, DatasetVersion
from evalplat.models.model_deployment import ModelDeployment
from evalplat.models.run import Run
from evalplat.models.supplier import Supplier
from evalplat.models.test_template import TestTemplate as TemplateModel, TestTemplateVersion as TemplateVersionModel
from evalplat.services import task_service


def _deployment(db):
    supplier = Supplier(name="供应商", short_name="supplier", type="原厂")
    db.add(supplier)
    db.flush()
    connection = Connection(
        supplier_id=supplier.id,
        name="连接",
        base_url="https://api.example.com",
        api_key_ciphertext=b"ciphertext",
    )
    db.add(connection)
    db.flush()
    deployment = ModelDeployment(
        connection_id=connection.id,
        model_id="model-a",
        source="manual",
        status="active",
    )
    db.add(deployment)
    db.flush()
    return deployment


def test_submit_freezes_snapshot_and_creates_run(db):
    deployment = _deployment(db)
    task = task_service.create_task(
        db,
        name="任务",
        run_mode="serial",
        model_deployment_ids=[deployment.id],
        generation_params={"temperature": 0.2},
    )

    submitted = task_service.submit_task(db, task.id)
    run = db.query(Run).filter(Run.task_id == task.id).one()

    assert submitted.status == "queued"
    assert submitted.precheck_result["status"] == "passed"
    assert run.run_snapshot["model_id"] == "model-a"
    assert run.run_snapshot["base_url"] == "https://api.example.com"
    assert run.run_snapshot["generation_params"] == {"temperature": 0.2}
    assert run.run_snapshot["credential_version"] == 1
    assert run.run_snapshot["evaluation_engine"] == "evalscope"


def test_task_snapshot_does_not_alias_caller_nested_configuration(db):
    deployment = _deployment(db)
    generation_params = {"temperature": 0.2}
    task = task_service.create_task(
        db,
        name="不可变任务",
        run_mode="serial",
        model_deployment_ids=[deployment.id],
        generation_params=generation_params,
    )
    generation_params["temperature"] = 0.9

    task_service.submit_task(db, task.id)
    run = db.query(Run).filter(Run.task_id == task.id).one()

    assert run.run_snapshot["generation_params"] == {"temperature": 0.2}


def test_draft_task_update_changes_configuration_snapshot(db):
    deployment = _deployment(db)
    task = task_service.create_task(
        db,
        name="可编辑草稿",
        run_mode="serial",
        model_deployment_ids=[],
        request_count=1,
    )

    task_service.update_task(
        db,
        task.id,
        model_deployment_ids=[deployment.id],
        request_count=7,
        generation_params={"temperature": 0.4},
        stream=True,
    )

    db.refresh(task)
    assert task.config_snapshot["model_deployment_ids"] == [str(deployment.id)]
    assert task.config_snapshot["request_count"] == 7
    assert task.config_snapshot["generation_params"] == {"temperature": 0.4}
    assert task.config_snapshot["stream"] is True


def test_create_task_idempotency_returns_existing_task(db):
    first = task_service.create_task(
        db, name="幂等任务", run_mode="serial", idempotency_key="request-1"
    )
    second = task_service.create_task(
        db, name="不应重复", run_mode="serial", idempotency_key="request-1"
    )
    assert second.id == first.id
    assert db.query(type(first)).count() == 1


def test_submit_rejects_missing_deployment(db):
    task = task_service.create_task(
        db,
        name="任务",
        run_mode="serial",
        model_deployment_ids=[uuid.uuid4()],
    )

    with pytest.raises(ConflictError):
        task_service.submit_task(db, task.id)

    db.refresh(task)
    assert task.status == "draft"
    assert task.precheck_result["status"] == "failed"


def test_create_task_materializes_template_defaults_into_draft_snapshot(db):
    template = TemplateModel(name="质量模板", is_builtin=False, status="active")
    db.add(template)
    db.flush()
    version = TemplateVersionModel(
        template_id=template.id,
        version=1,
        test_type="quality",
        dataset_version_id=uuid.uuid4(),
        tokenizer_id=uuid.uuid4(),
        generation_params={"temperature": 0},
        evalscope_datasets=["ifeval"],
        stream_mode="stream",
        warmup_count=2,
        load_profile={"mode": "fixed_rps", "rps": 2},
        request_count=12,
        retry_policy={"max_retries": 1},
    )
    db.add(version)
    db.commit()

    task = task_service.create_task(
        db,
        name="从模板创建",
        run_mode="serial",
        template_version_id=version.id,
        model_deployment_ids=[],
    )

    assert task.config_snapshot["evaluation_kind"] == "quality"
    assert task.config_snapshot["dataset_version_id"] == str(version.dataset_version_id)
    assert task.config_snapshot["stream"] is True
    assert task.config_snapshot["request_count"] == 12
    assert task.config_snapshot["retry_policy"] == {"max_retries": 1}
    assert task.config_snapshot["evalscope_datasets"] == ["ifeval"]


def test_evalscope_is_available_for_performance_tasks(db):
    task = task_service.create_task(
        db,
        name="性能任务",
        run_mode="serial",
        evaluation_engine="evalscope",
        evaluation_kind="performance",
    )
    assert task.config_snapshot["evaluation_engine"] == "evalscope"


def test_evalscope_task_requires_benchmark_names_at_submit(db):
    deployment = _deployment(db)
    task = task_service.create_task(
        db,
        name="EvalScope 任务",
        run_mode="serial",
        evaluation_kind="quality",
        evaluation_engine="evalscope",
        model_deployment_ids=[deployment.id],
    )

    with pytest.raises(ConflictError, match="预检"):
        task_service.submit_task(db, task.id)

    db.refresh(task)
    assert task.precheck_result["status"] == "failed"
    assert any(check["name"] == "evalscope_datasets" for check in task.precheck_result["checks"])


def test_code_quality_task_preserves_code_mode_in_snapshot(db):
    task = task_service.create_task(
        db,
        name="代码质量任务",
        run_mode="serial",
        evaluation_kind="quality",
        quality_mode="code",
        quality_samples=[{"messages": [{"role": "user", "content": "solve"}], "tests": "assert True"}],
    )

    assert task.config_snapshot["quality_mode"] == "code"


def test_precheck_enforces_request_and_duration_limits(db):
    deployment = _deployment(db)
    connection = db.get(Connection, deployment.connection_id)
    connection.limits = {"max_requests": 6, "max_duration_seconds": 1}
    connection.timeouts = {"read": 1}
    db.commit()
    task = task_service.create_task(
        db,
        name="受限任务",
        run_mode="serial",
        model_deployment_ids=[deployment.id],
        request_count=3,
    )

    with pytest.raises(ConflictError, match="预检"):
        task_service.submit_task(db, task.id)

    checks = {item["name"]: item for item in task.precheck_result["checks"]}
    assert checks["request_count_limit"]["status"] == "failed"
    assert checks["duration_limit"]["status"] == "failed"


def test_code_quality_task_rejects_evalscope_engine(db):
    with pytest.raises(ValueError, match="quality_mode=code"):
        task_service.create_task(
            db,
            name="代码 EvalScope 任务",
            run_mode="serial",
            evaluation_kind="quality",
            evaluation_engine="evalscope",
            quality_mode="code",
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"evaluation_engine": "native"},
        {"evaluation_kind": "quality", "evaluation_engine": "native", "quality_mode": "text"},
    ],
)
def test_native_engine_is_reserved_for_code_quality(db, changes):
    with pytest.raises(ValueError, match="必须使用 EvalScope"):
        task_service.create_task(
            db,
            name="禁止原生引擎",
            run_mode="serial",
            **changes,
        )


def test_code_task_uses_default_controlled_sandbox_image(db):
    deployment = _deployment(db)
    task = task_service.create_task(
        db,
        name="默认沙箱",
        run_mode="serial",
        evaluation_kind="quality",
        quality_mode="code",
        model_deployment_ids=[deployment.id],
        quality_samples=[{"messages": [{"role": "user", "content": "solve"}], "tests": "assert True"}],
    )

    submitted = task_service.submit_task(db, task.id)

    assert submitted.status == "queued"
    checks = {item["name"]: item for item in submitted.precheck_result["checks"]}
    assert checks["sandbox_image"]["status"] == "passed"


def test_submit_rejects_dataset_with_mismatched_purpose(db):
    deployment = _deployment(db)
    dataset = Dataset(name="质量题库", kind="quality")
    db.add(dataset)
    db.flush()
    version = DatasetVersion(
        dataset_id=dataset.id,
        version=1,
        status="ready",
        # The purpose check must run before file validation, so a deliberately
        # incomplete fixture still exercises the cross-entity invariant.
        storage_path=None,
    )
    db.add(version)
    db.commit()

    task = task_service.create_task(
        db,
        name="错误用途数据集",
        run_mode="serial",
        evaluation_kind="performance",
        model_deployment_ids=[deployment.id],
        dataset_version_id=version.id,
    )

    with pytest.raises(ConflictError, match="预检"):
        task_service.submit_task(db, task.id)

    db.refresh(task)
    dataset_check = next(item for item in task.precheck_result["checks"] if item["name"] == "dataset")
    assert dataset_check["status"] == "failed"
    assert "用途为 quality" in dataset_check["detail"]
