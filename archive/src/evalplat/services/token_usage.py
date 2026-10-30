"""供应商 usage 的宽容解析与字段规范化。"""

from collections.abc import Mapping
from typing import Any


def _non_negative_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def normalize_usage_tokens(usage: Mapping[str, Any] | None) -> dict[str, int | None]:
    """提取 token usage，并在信息充分时补齐可推导字段。

    不修改供应商原始 usage。只有同时存在 prompt 和 completion 时才从二者
    推导 total；反过来 total 加一个分项时可推导另一个分项，避免把未知值
    当成 0 参与成本计算。
    """

    raw = usage if isinstance(usage, Mapping) else {}
    prompt = _non_negative_int(raw.get("prompt_tokens", raw.get("input_tokens")))
    completion = _non_negative_int(raw.get("completion_tokens", raw.get("output_tokens")))
    total = _non_negative_int(raw.get("total_tokens"))
    cached = _non_negative_int(raw.get("cached_tokens"))
    prompt_details = raw.get("prompt_tokens_details") or raw.get("input_tokens_details")
    if cached is None and isinstance(prompt_details, Mapping):
        cached = _non_negative_int(prompt_details.get("cached_tokens"))
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    elif total is not None and prompt is not None and completion is None:
        completion = max(0, total - prompt)
    elif total is not None and completion is not None and prompt is None:
        prompt = max(0, total - completion)
    if prompt is not None and cached is not None:
        cached = min(cached, prompt)
    return {
        "prompt_tokens": prompt,
        "cached_tokens": cached,
        "completion_tokens": completion,
        "total_tokens": total,
    }
