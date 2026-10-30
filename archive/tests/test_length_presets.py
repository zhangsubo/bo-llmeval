"""内置输入长度预设的目录、Tokenizer 和执行链路测试。"""

import uuid

import pytest

from evalplat.models.connection import Connection
from evalplat.models.model_deployment import ModelDeployment
from evalplat.models.run import Run
from evalplat.models.supplier import Supplier
from evalplat.models.tokenizer import Tokenizer
from evalplat.services.length_preset_service import (
    LENGTH_PRESETS,
    build_preset_messages,
    get_length_preset,
    list_length_presets,
)
from evalplat.services.run_worker import process_one
from evalplat.services.task_precheck_service import run_task_precheck
from evalplat.services.tokenizer_service import count_tokens, generate_token_text
from evalplat.services import task_service


def _ready_builtin(db):
    tokenizer = Tokenizer(
        name="cl100k",
        source="builtin",
        resource_id="cl100k_base",
        status="ready",
    )
    db.add(tokenizer)
    db.flush()
    return tokenizer


def _deployment(db):
    supplier = Supplier(name="供应商", short_name="preset-supplier", type="原厂")
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
        context_window=65_536,
        max_output_tokens=4_096,
    )
    db.add(deployment)
    db.flush()
    return deployment


def test_preset_catalog_has_stable_values():
    assert [(item.key, item.target_input_tokens, item.max_output_tokens) for item in LENGTH_PRESETS] == [
        ("short", 256, 128),
        ("medium", 2_048, 512),
        ("long", 8_192, 1_024),
        ("extra_long", 32_768, 2_048),
    ]
    assert [item["key"] for item in list_length_presets()] == [
        "short",
        "medium",
        "long",
        "extra_long",
    ]
    with pytest.raises(ValueError):
        get_length_preset("huge")


def test_builtin_tokenizer_generates_distinct_messages_at_target_length(db):
    tokenizer = _ready_builtin(db)
    first = build_preset_messages(tokenizer, "short", sample_index=0)
    second = build_preset_messages(tokenizer, "short", sample_index=1)

    assert first != second
    assert count_tokens(tokenizer, first) == 256
    assert count_tokens(tokenizer, second) == 256
    assert count_tokens(tokenizer, generate_token_text(tokenizer, 32, seed=2)) == 32


def test_all_builtin_presets_hit_target_with_builtin_tokenizer(db):
    tokenizer = _ready_builtin(db)
    for preset in LENGTH_PRESETS:
        messages = build_preset_messages(tokenizer, preset, sample_index=0)
        assert count_tokens(tokenizer, messages) == preset.target_input_tokens


def test_preset_requires_ready_tokenizer():
    tokenizer = Tokenizer(
        id=uuid.uuid4(), name="pending", source="builtin", resource_id="cl100k_base", status="pending"
    )
    with pytest.raises(ValueError, match="ready"):
        build_preset_messages(tokenizer, "short")


def test_task_snapshot_materializes_preset_and_default_output(db):
    deployment = _deployment(db)
    tokenizer = _ready_builtin(db)
    task = task_service.create_task(
        db,
        name="预设任务",
        run_mode="serial",
        model_deployment_ids=[deployment.id],
        tokenizer_id=tokenizer.id,
        input_length_preset="medium",
    )

    assert task.config_snapshot["input_length_preset"] == "medium"
    assert task.config_snapshot["generation_params"] == {"max_tokens": 512}

    task_service.submit_task(db, task.id)
    run = db.query(Run).filter(Run.task_id == task.id).one()
    assert run.run_snapshot["input_length_preset"] == "medium"
    assert run.run_snapshot["generation_params"] == {"max_tokens": 512}


def test_precheck_blocks_preset_without_ready_tokenizer(db):
    deployment = _deployment(db)
    checks, _ = run_task_precheck(
        db,
        {
            "model_deployment_ids": [str(deployment.id)],
            "input_length_preset": "short",
        },
        run_mode="serial",
    )
    failed = [item for item in checks if item["status"] == "failed"]
    assert any("Tokenizer" in item["detail"] for item in failed)


def test_worker_uses_per_index_preset_messages(db):
    tokenizer = _ready_builtin(db)
    run = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="queued",
        run_snapshot={
            "request_count": 2,
            "warmup_count": 0,
            "input_length_preset": "short",
            "tokenizer_id": str(tokenizer.id),
        },
    )
    db.add(run)
    db.commit()
    seen: list[tuple[int, list[dict]]] = []

    def executor(_db, _run, messages, _crypto, _guard, **kwargs):
        seen.append((kwargs["sample_index"], messages))
        return {"status": "completed", "sample_index": kwargs["sample_index"]}

    result = process_one(db, "worker-preset", object(), object(), executor=executor)

    assert result["status"] == "completed"
    assert [index for index, _ in seen] == [0, 1, 2]
    assert len({messages[0]["content"] for _, messages in seen}) == 3
    assert all(count_tokens(tokenizer, messages) == 256 for _, messages in seen)
