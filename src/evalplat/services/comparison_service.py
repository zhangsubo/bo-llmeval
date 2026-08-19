"""结果对比业务逻辑（Phase 8）。

检查多个 Run 的可比性（数据集、Tokenizer、参数一致性），返回对比结果。
"""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from evalplat.core.errors import NotFoundError
from evalplat.models.run import Run


def _extract_snapshot_field(snapshot: dict | None, *keys: str) -> Any:
    """从快照中递归提取字段值，不存在返回 None。"""
    if not snapshot:
        return None
    current = snapshot
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None
    return current


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

    # 2a. 检查数据集一致性
    datasets = set()
    for run in runs:
        ds = _extract_snapshot_field(run.run_snapshot, "dataset_id")
        if ds:
            datasets.add(str(ds))
    if len(datasets) > 1:
        differences.append({
            "dimension": "dataset",
            "details": f"数据集不一致：共 {len(datasets)} 个不同数据集",
        })

    # 2b. 检查 Tokenizer 一致性
    tokenizers = set()
    for run in runs:
        tok = _extract_snapshot_field(run.run_snapshot, "tokenizer_id")
        if tok:
            tokenizers.add(str(tok))
    if len(tokenizers) > 1:
        differences.append({
            "dimension": "tokenizer",
            "details": f"Tokenizer 不一致：共 {len(tokenizers)} 个不同 Tokenizer",
        })

    # 2c. 检查关键参数一致性（temperature, max_tokens 等）
    params_keys = ["temperature", "max_tokens", "top_p"]
    for key in params_keys:
        values = set()
        for run in runs:
            val = _extract_snapshot_field(run.run_snapshot, "params", key)
            if val is not None:
                values.add(str(val))
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
    }
