"""Schema 断言测试：验证全部模型定义正确。

直接检查 Base.metadata（ORM 层），不依赖任何数据库引擎。
覆盖：表数量、列名、索引、唯一约束、CHECK 约束。
"""

import uuid
from datetime import datetime, timezone

from evalplat.db.base import Base

# 导入全部模型，触发 Base.metadata 注册
import evalplat.models  # noqa: F401


def test_model_count():
    """Base.metadata 应包含当前所有业务表。"""
    assert len(Base.metadata.tables) == 21, (
        f"期望 21 张表，实际 {len(Base.metadata.tables)}: {set(Base.metadata.tables.keys())}"
    )


def test_all_tables_exist():
    """全部业务表应存在于 metadata 中。"""
    expected = {
        "suppliers", "connections", "model_deployments", "comparison_groups",
        "model_prices", "tokenizers", "datasets", "dataset_versions",
        "test_templates", "test_template_versions", "tasks", "runs",
        "requests", "attempts", "notes", "audit_events", "system_settings",
        "reports", "code_executions", "connection_rate_events", "cleanup_batches",
    }
    assert expected.issubset(set(Base.metadata.tables.keys())), \
        f"缺失表: {expected - set(Base.metadata.tables.keys())}"


def _cols(table_name: str) -> set[str]:
    return {c.name for c in Base.metadata.tables[table_name].columns}


def test_supplier_columns():
    assert {"id", "name", "short_name", "type", "status", "remark",
            "created_at", "updated_at"}.issubset(_cols("suppliers"))


def test_connection_columns():
    expected = {
        "id", "supplier_id", "name", "region", "service_tier",
        "base_url", "api_key_ciphertext", "credential_version",
        "tls_skip_verify", "timeouts", "limits", "status", "remark",
        "created_at", "updated_at",
    }
    assert expected.issubset(_cols("connections")), f"缺失列: {expected - _cols('connections')}"


def test_model_deployment_columns():
    expected = {
        "id", "connection_id", "model_id", "display_name",
        "comparison_group_id", "context_window", "max_output_tokens",
        "supports_streaming", "supports_non_streaming", "tokenizer_id",
        "model_version", "source", "status", "remark",
        "created_at", "updated_at",
    }
    assert expected.issubset(_cols("model_deployments")), f"缺失列: {expected - _cols('model_deployments')}"


def test_model_price_columns():
    expected = {
        "id", "model_deployment_id", "input_token_price", "output_token_price",
        "cached_token_price", "unit", "currency", "exchange_rate",
        "effective_from", "price_version", "source",
        "created_at", "updated_at",
    }
    assert expected.issubset(_cols("model_prices")), f"缺失列: {expected - _cols('model_prices')}"


def test_request_columns():
    expected = {
        "id", "run_id", "sample_index", "session_id", "turn_index",
        "kind", "phase", "stream_mode", "input_messages", "status",
        "scheduled_at", "started_at", "finished_at", "quality_result",
        "vendor_prompt_tokens", "vendor_completion_tokens", "vendor_total_tokens",
        "local_prompt_tokens", "local_completion_tokens", "local_total_tokens",
        "created_at", "updated_at",
    }
    assert expected.issubset(_cols("requests")), f"缺失列: {expected - _cols('requests')}"


def test_attempt_columns():
    expected = {
        "id", "request_id", "attempt_index", "started_at", "finished_at",
        "latency_ms", "ttft_ms", "tpot_ms", "itl_series",
        "avg_speed", "peak_speed", "peak_tps",
        "vendor_usage", "raw_response", "extracted_fields",
        "error_category", "error_body", "http_status",
        "vendor_request_id", "response_headers",
        "created_at", "updated_at",
    }
    assert expected.issubset(_cols("attempts")), f"缺失列: {expected - _cols('attempts')}"


def test_template_version_columns_include_evalscope_datasets():
    assert "evalscope_datasets" in _cols("test_template_versions")


def test_system_setting_columns():
    assert {"id", "key", "value", "updated_at"}.issubset(_cols("system_settings"))


def test_report_columns():
    assert {"id", "task_id", "version", "status", "summary_path", "details_path", "snapshot", "error", "created_at", "updated_at"}.issubset(_cols("reports"))


def test_cleanup_batch_columns():
    assert {"id", "cutoff_at", "statuses", "status", "manifest", "planned_counts", "deleted_counts", "failed_items", "error", "started_at", "finished_at", "created_at", "updated_at"}.issubset(_cols("cleanup_batches"))


def test_indexes():
    """关键索引存在。"""
    def idx_names(table_name: str) -> set[str]:
        return {idx.name for idx in Base.metadata.tables[table_name].indexes}

    assert "ix_model_deployments_connection_id" in idx_names("model_deployments")
    assert "ix_model_deployments_comparison_group_id" in idx_names("model_deployments")
    assert "ix_tasks_status_queued" in idx_names("tasks")
    assert "ix_requests_run_id" in idx_names("requests")
    assert "ix_attempts_request_id" in idx_names("attempts")
    assert "ix_reports_task_id" in idx_names("reports")
    assert "ix_cleanup_batches_status_cutoff" in idx_names("cleanup_batches")
    assert "ix_notes_target" in idx_names("notes")
    assert "ix_audit_events_occurred_at" in idx_names("audit_events")
    assert "ix_audit_events_target" in idx_names("audit_events")


def test_unique_constraints():
    """唯一约束存在。"""
    def uq_names(table_name: str) -> set[str]:
        return {uq.name for uq in Base.metadata.tables[table_name].constraints
                if hasattr(uq, 'name') and uq.name}

    assert "uq_model_prices_deployment_version" in uq_names("model_prices")
    assert "uq_system_settings_key" in uq_names("system_settings")
    assert "uq_reports_task_version" in uq_names("reports")

    # tasks: idempotency_key unique (Column-level unique=True)
    task_cols = Base.metadata.tables["tasks"].columns
    assert task_cols["idempotency_key"].unique is True


def test_check_constraints():
    """关键 CHECK 约束存在。"""

    # supplier status CHECK (命名约定展开为 ck_suppliers_status)
    supplier_cks = {c.name for c in Base.metadata.tables["suppliers"].constraints
                    if hasattr(c, 'sqltext')}
    assert "ck_suppliers_status" in supplier_cks

    # connection status CHECK
    conn_cks = {c.name for c in Base.metadata.tables["connections"].constraints
                if hasattr(c, 'sqltext')}
    assert "ck_connections_status" in conn_cks

    # task status + run_mode CHECK
    task_cks = {c.name for c in Base.metadata.tables["tasks"].constraints
                if hasattr(c, 'sqltext')}
    assert "ck_tasks_status" in task_cks
    assert "ck_tasks_run_mode" in task_cks


def test_foreign_keys():
    """关键外键存在。"""
    def fk_cols(table_name: str) -> dict[str, str]:
        """返回 {列名: 被引用表名}"""
        result = {}
        for fk in Base.metadata.tables[table_name].foreign_keys:
            result[fk.parent.name] = fk.column.table.name
        return result

    assert fk_cols("connections")["supplier_id"] == "suppliers"
    assert fk_cols("model_deployments")["connection_id"] == "connections"
    assert fk_cols("model_prices")["model_deployment_id"] == "model_deployments"
    assert fk_cols("dataset_versions")["dataset_id"] == "datasets"
    assert fk_cols("test_template_versions")["template_id"] == "test_templates"
    assert fk_cols("requests")["run_id"] == "runs"
    assert fk_cols("attempts")["request_id"] == "requests"
    assert fk_cols("reports")["task_id"] == "tasks"


def test_resource_paths_are_serialized_without_host_prefix():
    from evalplat.schemas.dataset import DatasetVersionRead
    from evalplat.schemas.tokenizer import TokenizerRead

    dataset = DatasetVersionRead(
        id=uuid.uuid4(), dataset_id=uuid.uuid4(), version=1, source="upload",
        license=None, checksum=None, sample_count=1, language=None,
        capability_tags=None, default_prompt=None, scoring_method=None,
        field_mapping=None, token_length_stats=None, status="ready",
        storage_path="/srv/evalplat/data/datasets/sample.jsonl",
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    tokenizer = TokenizerRead(
        id=uuid.uuid4(), name="tok", source="upload", resource_id=None,
        revision=None, checksum=None, size_bytes=1, status="ready", error=None,
        local_path="/srv/evalplat/data/tokenizers/tok", created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    assert dataset.model_dump()["storage_path"] == "sample.jsonl"
    assert tokenizer.model_dump()["local_path"] == "tok"


def test_resource_paths_are_serialized_without_windows_host_prefix():
    from evalplat.schemas.dataset import DatasetVersionRead
    from evalplat.schemas.tokenizer import TokenizerRead

    dataset = DatasetVersionRead(
        id=uuid.uuid4(), dataset_id=uuid.uuid4(), version=1, source="upload",
        license=None, checksum=None, sample_count=1, language=None,
        capability_tags=None, default_prompt=None, scoring_method=None,
        field_mapping=None, token_length_stats=None, status="ready",
        storage_path=r"C:\\srv\\evalplat\\data\\datasets\\sample.jsonl",
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    tokenizer = TokenizerRead(
        id=uuid.uuid4(), name="tok", source="upload", resource_id=None,
        revision=None, checksum=None, size_bytes=1, status="ready", error=None,
        local_path=r"C:\\srv\\evalplat\\data\\tokenizers\\tok",
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )

    assert dataset.model_dump()["storage_path"] == "sample.jsonl"
    assert tokenizer.model_dump()["local_path"] == "tok"
