"""OpenAI-compatible protocol response validation helpers.

The connection probe and model synchronizer consume the same ``/v1/models``
contract. Keeping validation here prevents the two boundaries from accepting
different response shapes or silently creating malformed deployments.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit


_OPENAI_ENDPOINTS = ("/chat/completions", "/completions")


def openai_api_url(base_url: str, resource: str) -> str:
    """Build an OpenAI-compatible API URL from a legacy or normalized base URL.

    Connections created by the current API persist a host/prefix without
    ``/v1``. Older rows can still contain ``/v1`` or a complete chat endpoint;
    normalizing at the transport boundary prevents ``/v1/v1`` and
    ``/chat/completions/v1`` requests while preserving a custom path prefix.
    """

    value = str(base_url or "").strip().rstrip("/")
    if not value:
        raise ValueError("Base URL 不能为空")
    parsed = urlsplit(value)
    path = parsed.path.rstrip("/")
    for endpoint in _OPENAI_ENDPOINTS:
        if path.endswith(endpoint):
            path = path[: -len(endpoint)].rstrip("/")
            break
    if path.endswith("/v1"):
        api_root = path
    else:
        api_root = f"{path}/v1" if path else "/v1"
    resource_path = str(resource or "").strip().lstrip("/")
    if not resource_path:
        raise ValueError("API resource 不能为空")
    return urlunsplit((parsed.scheme, parsed.netloc, f"{api_root}/{resource_path}", "", ""))


def extract_model_ids(payload: Any) -> list[str]:
    """Validate a ``GET /v1/models`` payload and return unique model IDs.

    A malformed response is rejected as a whole so callers never persist a
    partial model list. Duplicate IDs are collapsed while preserving the
    provider's order.
    """

    if not isinstance(payload, Mapping):
        raise ValueError("响应顶层必须为对象")
    models = payload.get("data")
    if not isinstance(models, list):
        raise ValueError("响应 data 必须为数组")

    model_ids: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(models):
        if not isinstance(item, Mapping):
            raise ValueError(f"响应 data[{index}] 必须为对象")
        raw_id = item.get("id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            raise ValueError(f"响应 data[{index}].id 必须为非空字符串")
        model_id = raw_id.strip()
        if model_id not in seen:
            seen.add(model_id)
            model_ids.append(model_id)
    return model_ids
