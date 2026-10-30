"""结果对比业务逻辑（Phase 8）。

检查多个 Run 的可比性（数据集、Tokenizer、参数一致性），返回对比结果。
"""

import json
import uuid
from typing import Any

from sqlalchemy.orm import Session

from evalplat.core.errors import NotFoundError
from evalplat.models.run import Run
from evalplat.services.metrics_service import aggregate_run_metrics


def _json_key(value: Any) -> str:
    """Produce a stable comparison key for nested JSON values."""

    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _snapshot_dimension(snapshot: dict[str, Any], dimension: str) -> Any:
    """Return the reproducibility identity for one comparison dimension."""

    if dimension == "dataset":
        metadata = snapshot.get("dataset_snapshot")
        if isinstance(metadata, dict):
            metadata = {
                key: metadata.get(key)
                for key in (
                    "id", "dataset_id", "version", "kind", "source", "source_id",
                    "source_revision", "source_split", "source_subset", "checksum",
                    "sample_count", "language", "scoring_method", "field_mapping",
                )
            }
        return {
            "id": snapshot.get("dataset_version_id", snapshot.get("dataset_id")),
            "metadata": metadata,
        }
    if dimension == "tokenizer":
        metadata = snapshot.get("tokenizer_snapshot")
        if isinstance(metadata, dict):
            metadata = {
                key: metadata.get(key)
                for key in ("id", "resource_id", "revision", "checksum", "status")
            }
        return {"id": snapshot.get("tokenizer_id"), "metadata": metadata}
    if dimension == "generation_params":
        return snapshot.get("generation_params", snapshot.get("params", {})) or {}
    return snapshot.get(dimension)


def compare_runs(db: Session, run_ids: list[uuid.UUID]) -> dict:
    """对比多个 Run 的可比性。

    Returns:
        {
            "runs": [Run, ...],
            "comparability": "comparable" | "not_comparable",
            "differences": [{"dimension": ..., "details": ...}, ...]
        }
    """
    # 1. 加载所有 Run
    runs = []
    for rid in run_ids:
        run = db.get(Run, rid)
        if not run:
            raise NotFoundError(f"Run {rid} 不存在")
        runs.append(run)

    # 2. 检查可比性
    differences = []

    # Every value is normalized, including explicit ``None``/missing.  That
    # keeps old snapshots comparable with each other while refusing to label a
    # partially populated snapshot as strictly comparable to a new one.
    dimensions = (
        ("dataset", "数据集"),
        ("tokenizer", "Tokenizer"),
        ("messages", "messages"),
        ("test_type", "测试类型"),
        ("scenario", "场景"),
        ("evaluation_kind", "评测类型"),
        ("quality_mode", "质量模式"),
        ("input_length_preset", "输入长度预设"),
        ("load_profile", "负载配置"),
        ("request_count", "请求数量"),
        ("warmup_count", "预热请求数量"),
        ("stream", "流式模式"),
        ("timeouts", "超时配置"),
        ("limits", "限额配置"),
        ("retry_policy", "重试策略"),
        ("evalscope_datasets", "EvalScope 数据集"),
        ("dataset_args", "数据集参数"),
    )
    for dimension, label in dimensions:
        values = {
            _json_key(_snapshot_dimension(run.run_snapshot or {}, dimension))
            for run in runs
        }
        if len(values) > 1:
            differences.append({
                "dimension": dimension,
                "details": f"{label}不一致：共 {len(values)} 个不同值",
            })

    # Keep parameter-level diagnostics for the UI and backwards-compatible
    # consumers while the rest of the snapshot uses whole-object dimensions.
    parameter_names = {
        key
        for run in runs
        for key in (_snapshot_dimension(run.run_snapshot or {}, "generation_params") or {})
    }
    for key in sorted(parameter_names):
        values = {
            _json_key((_snapshot_dimension(run.run_snapshot or {}, "generation_params") or {}).get(key))
            for run in runs
        }
        if len(values) > 1:
            differences.append({
                "dimension": f"params.{key}",
                "details": f"参数 {key} 不一致：共 {len(values)} 个不同值",
            })

    comparability = "not_comparable" if differences else "comparable"

    return {
        "runs": runs,
        "comparability": comparability,
        "differences": differences,
        "metrics": {str(run.id): aggregate_run_metrics(db, run.id) for run in runs},
    }
