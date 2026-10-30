"""单请求 OpenAI-compatible 执行器。

执行器只负责 HTTP 调用和 Request/Attempt 证据落库；队列认领和任务状态由
``run_queue_service`` 负责，便于替换评测场景而不耦合调度逻辑。
"""

import time
import uuid
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evalplat.core.crypto import CryptoService
from evalplat.core.errors import ConflictError
from evalplat.core.ssrf import SSRFGuard
from evalplat.models.attempt import Attempt
from evalplat.models.connection import Connection
from evalplat.models.request import Request
from evalplat.models.run import Run
from evalplat.models.tokenizer import Tokenizer
from evalplat.services.connection_rate_limiter import (
    acquire_connection_slot,
    release_connection_slot,
)
from evalplat.services.security_service import (
    redact_sensitive_text,
    redact_sensitive_value,
)
from evalplat.services.tokenizer_service import count_tokens
from evalplat.services.token_usage import normalize_usage_tokens


logger = logging.getLogger(__name__)


def _chat_completions_url(base_url: str) -> str:
    """Build one OpenAI-compatible chat endpoint from a legacy base URL."""

    normalized = base_url.strip().rstrip("/")
    if not normalized:
        raise ValueError("连接 base_url 不能为空")
    if normalized.endswith(("/chat/completions", "/completions")):
        return normalized
    if normalized.endswith("/v1"):
        return f"{normalized}/chat/completions"
    return f"{normalized}/v1/chat/completions"


class RequestTimeoutError(TimeoutError):
    """Timeout with a PRD-visible category (first token, idle, or total)."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def record_dropped_request(
    db: Session,
    run: Run,
    messages: list[dict[str, Any]],
    *,
    phase: str,
    sample_index: int,
    stream: bool,
    reason: str = "max_inflight exceeded at scheduled time",
) -> dict[str, Any]:
    """记录 open-loop 调度丢弃，不发 HTTP 请求。"""

    now = datetime.now(timezone.utc)
    request = Request(
        run_id=run.id,
        sample_index=sample_index,
        kind="perf",
        phase=phase,
        stream_mode="stream" if stream else "nonstream",
        input_messages=redact_sensitive_value({"messages": messages}),
        status="failed",
        scheduled_at=now,
        finished_at=now,
    )
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        request.id = (db.scalar(select(func.max(Request.id))) or 0) + 1
    db.add(request)
    db.flush()
    attempt = Attempt(
        request_id=request.id,
        attempt_index=1,
        started_at=now,
        finished_at=now,
        error_category="schedule_dropped",
        error_body=reason[:500],
    )
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        attempt.id = (db.scalar(select(func.max(Attempt.id))) or 0) + 1
    db.add(attempt)
    db.commit()
    return {
        "status": "failed",
        "request_id": request.id,
        "attempt_id": attempt.id,
        "error_category": "schedule_dropped",
        "error": reason,
        "sample_index": sample_index,
    }


def _safe_response_headers(headers: Any, secret: str | None = None) -> dict[str, str]:
    """Persist only harmless response headers, never cookies or credentials."""

    allowed = {
        "content-type",
        "content-length",
        "date",
        "server",
        "retry-after",
        "x-request-id",
        "x-ratelimit-limit-requests",
        "x-ratelimit-remaining-requests",
        "x-ratelimit-reset-requests",
    }
    result: dict[str, str] = {}
    for key, value in dict(headers or {}).items():
        normalized = str(key).lower()
        if normalized in allowed:
            result[normalized] = redact_sensitive_text(value, secret)
    return result


def execute_chat_request(
    db: Session,
    run: Run,
    messages: list[dict[str, Any]],
    crypto: CryptoService,
    ssrf_guard: SSRFGuard,
    *,
    stream: bool = False,
    kind: str = "perf",
    phase: str = "steady",
    sample_index: int | None = None,
    request: Request | None = None,
    attempt_index: int = 1,
    input_messages: dict[str, Any] | None = None,
    client_factory: Callable[..., Any] = httpx.Client,
) -> dict[str, Any]:
    """执行一次 Chat Completions 请求并保存完整证据。

    性能请求不自动重试；调用方如需质量重试，应创建新的 Attempt。
    """
    if run.status != "running":
        raise ConflictError("只有 running 状态的 Run 可以执行请求")
    snapshot = run.run_snapshot or {}
    connection_id = snapshot.get("connection_id")
    model_id = snapshot.get("model_id")
    if not connection_id or not model_id:
        raise ValueError("Run 快照缺少 connection_id 或 model_id")

    connection = db.get(Connection, uuid.UUID(str(connection_id)))
    if not connection or connection.status != "active":
        raise ValueError("连接不存在或未启用")
    # Run 快照优先，避免连接编辑后改变历史任务的目标地址。
    base_url = str(snapshot.get("base_url") or connection.base_url).strip().rstrip("/")
    target = None
    if hasattr(ssrf_guard, "resolve_url"):
        allowed, reason, target = ssrf_guard.resolve_url(base_url)
    else:
        allowed, reason = ssrf_guard.check_url(base_url)
    if not allowed:
        raise ValueError(f"SSRF 校验失败: {reason}")
    api_key = crypto.decrypt(connection.api_key_ciphertext)
    tokenizer = None
    tokenizer_id = snapshot.get("tokenizer_id")
    if tokenizer_id:
        try:
            tokenizer = db.get(Tokenizer, uuid.UUID(str(tokenizer_id)))
        except (ValueError, TypeError):
            tokenizer = None

    # Request evidence is persisted below, including for callers that
    # pre-create a Request (for example quality retries).  Apply the current
    # decrypted credential at this single boundary so user-provided samples
    # cannot retain a known API key in ``input_messages``.
    safe_input_messages = redact_sensitive_value(
        input_messages or {"messages": messages}, api_key
    )

    started = datetime.now(timezone.utc)
    if request is None:
        request = Request(
            run_id=run.id,
            sample_index=sample_index,
            kind=kind,
            phase=phase,
            stream_mode="stream" if stream else "nonstream",
            input_messages=safe_input_messages,
            status="running",
            scheduled_at=started,
            started_at=started,
        )
        # SQLite has no PostgreSQL identity emulation in the test harness; production
        # PostgreSQL continues to allocate these BIGINT identities server-side.
        if db.bind is not None and db.bind.dialect.name == "sqlite":
            request.id = (db.scalar(select(func.max(Request.id))) or 0) + 1
        db.add(request)
        db.flush()
    else:
        if request.run_id != run.id:
            raise ValueError("Request 不属于当前 Run")
        request.input_messages = redact_sensitive_value(
            request.input_messages or safe_input_messages, api_key
        )
        request.status = "running"
        request.started_at = request.started_at or started
    attempt = Attempt(request_id=request.id, attempt_index=1, started_at=started)
    attempt.attempt_index = attempt_index
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        attempt.id = (db.scalar(select(func.max(Attempt.id))) or 0) + 1
    db.add(attempt)
    db.flush()

    # Reserve the connection quota only after evidence rows exist, so a denied
    # request is visible as a normal failed Request/Attempt and never reaches
    # the provider.  Limits are taken from the immutable Run snapshot.
    rate_event_id = None
    try:
        rate_decision = acquire_connection_slot(
            db,
            connection.id,
            snapshot.get("limits") if isinstance(snapshot.get("limits"), dict) else connection.limits,
            request_id=request.id,
            estimated_tokens=_estimate_reserved_tokens(db, snapshot, messages),
        )
    except Exception as exc:
        # Quota bookkeeping is part of the request boundary. If the limiter
        # itself fails, persist a normal failed attempt instead of leaving the
        # Request in ``running`` or letting the worker lose the evidence row.
        request.status = "failed"
        attempt.error_category = "connection_limit_error"
        attempt.error_body = redact_sensitive_text(str(exc)[:500], api_key)
        finished = datetime.now(timezone.utc)
        request.finished_at = finished
        attempt.finished_at = finished
        db.commit()
        return {
            "status": "failed",
            "request_id": request.id,
            "attempt_id": attempt.id,
            "error_category": attempt.error_category,
            "error": redact_sensitive_text(str(exc)[:500], api_key),
        }
    if not rate_decision.allowed:
        request.status = "failed"
        attempt.error_category = "connection_limit"
        attempt.error_body = redact_sensitive_text(
            rate_decision.reason or "connection limit exceeded", api_key
        )
        finished = datetime.now(timezone.utc)
        request.finished_at = finished
        attempt.finished_at = finished
        db.commit()
        return {
            "status": "failed",
            "request_id": request.id,
            "attempt_id": attempt.id,
            "error_category": "connection_limit",
            "error": rate_decision.reason or "connection limit exceeded",
        }
    rate_event_id = rate_decision.event_id

    url = _chat_completions_url(base_url)
    payload = {"model": model_id, "messages": messages, "stream": stream}
    payload.update(snapshot.get("generation_params") or {})
    if stream:
        stream_options = payload.get("stream_options")
        if not isinstance(stream_options, dict):
            stream_options = {}
        payload["stream_options"] = {**stream_options, "include_usage": True}
    timeout_config = snapshot.get("timeouts") or connection.timeouts or {}
    first_token_timeout = _timeout_value(timeout_config, "first_token", 300.0)
    stream_idle_timeout = _timeout_value(timeout_config, "stream_idle", 120.0)
    total_timeout = _timeout_value(timeout_config, "total", 1800.0)
    timeout = httpx.Timeout(
        connect=float(timeout_config.get("connect", 15)),
        read=float(timeout_config.get("read", stream_idle_timeout if stream else total_timeout)),
        write=float(timeout_config.get("write", 30)),
        pool=float(timeout_config.get("pool", 30)),
    )
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    monotonic_start = time.monotonic()
    try:
        verify = not bool(snapshot.get("tls_skip_verify", connection.tls_skip_verify))
        client_options: dict[str, Any] = {
            "timeout": timeout,
            "follow_redirects": False,
            "verify": verify,
            "trust_env": False,
        }
        if target is not None and hasattr(ssrf_guard, "transport_for"):
            client_options["transport"] = ssrf_guard.transport_for(target, verify=verify)
        with client_factory(**client_options) as client:
            if stream:
                body, raw_response, response = _read_stream(
                    client,
                    url,
                    payload,
                    headers,
                    monotonic_start,
                    attempt,
                    tokenizer,
                    secret=api_key,
                    first_token_timeout=first_token_timeout,
                    stream_idle_timeout=stream_idle_timeout,
                    total_timeout=total_timeout,
                )
            else:
                response = client.post(url, json=payload, headers=headers)
                if 200 <= response.status_code < 300:
                    try:
                        body = response.json()
                    except (TypeError, ValueError) as exc:
                        raise ValueError("协议错误: 响应不是有效 JSON") from exc
                else:
                    body = None
                raw_response = response.text
        elapsed_ms = round((time.monotonic() - monotonic_start) * 1000, 2)
        attempt.http_status = response.status_code
        attempt.response_headers = _safe_response_headers(response.headers, api_key)
        attempt.raw_response = redact_sensitive_text(raw_response[:1_000_000], api_key)
        attempt.vendor_request_id = (
            redact_sensitive_text(response.headers.get("x-request-id"), api_key) or None
        )
        attempt.latency_ms = elapsed_ms
        attempt.finished_at = datetime.now(timezone.utc)
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"HTTP {response.status_code}")
        _validate_chat_response(body)
        usage = body.get("usage") or {}
        normalized_usage = normalize_usage_tokens(usage)
        request.vendor_prompt_tokens = normalized_usage["prompt_tokens"]
        request.vendor_cached_tokens = normalized_usage["cached_tokens"]
        request.vendor_completion_tokens = normalized_usage["completion_tokens"]
        request.vendor_total_tokens = normalized_usage["total_tokens"]
        if tokenizer:
            request.local_prompt_tokens = count_tokens(tokenizer, messages)
            content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
            request.local_completion_tokens = count_tokens(tokenizer, content)
            if request.local_prompt_tokens is not None and request.local_completion_tokens is not None:
                request.local_total_tokens = request.local_prompt_tokens + request.local_completion_tokens
        attempt.vendor_usage = redact_sensitive_value(usage, api_key)
        message = body.get("choices", [{}])[0].get("message", {})
        tool_calls = message.get("tool_calls")
        if tool_calls is not None:
            _validate_tool_calls(tool_calls)
        extracted: dict[str, Any] = {"content": message.get("content")}
        for field in ("reasoning_content", "reasoning_tokens"):
            value = message.get(field)
            if value is None:
                value = usage.get(field)
            if value is None and field == "reasoning_tokens":
                completion_details = usage.get("completion_tokens_details")
                if isinstance(completion_details, dict):
                    value = completion_details.get("reasoning_tokens")
            if value is None:
                value = body.get(field)
            if value is not None:
                extracted[field] = value
        service_tier = body.get("service_tier") or usage.get("service_tier")
        if service_tier is not None:
            extracted["service_tier"] = service_tier
        if tool_calls is not None:
            extracted["tool_calls"] = tool_calls
        attempt.extracted_fields = redact_sensitive_value(extracted, api_key)
        metrics_usage = usage
        if stream and metrics_usage.get("completion_tokens") is None and request.local_completion_tokens is not None:
            # Some providers omit the final usage event. Keep vendor usage as-is
            # for cost accounting, but use the pinned local tokenizer for speed
            # metrics so a missing usage field does not erase TPOT/throughput.
            metrics_usage = {**metrics_usage, "completion_tokens": request.local_completion_tokens}
        _populate_stream_metrics(attempt, metrics_usage, elapsed_ms, stream=stream)
        request.status = "completed"
        result = {
            "status": "completed",
            "request_id": request.id,
            "attempt_id": attempt.id,
            "latency_ms": elapsed_ms,
            "usage": usage,
            "response": redact_sensitive_value(body, api_key),
        }
    except Exception as exc:
        request.status = "failed"
        attempt.error_category = _error_category(
            exc,
            getattr(locals().get("response"), "status_code", None),
            stream=stream,
            started=monotonic_start,
            total_timeout=total_timeout,
            attempt=attempt,
        )
        attempt.error_body = redact_sensitive_text(str(exc)[:500], api_key)
        result = {
            "status": "failed",
            "request_id": request.id,
            "attempt_id": attempt.id,
            "http_status": getattr(locals().get("response"), "status_code", None),
            "error_category": attempt.error_category,
            "error": redact_sensitive_text(str(exc)[:500], api_key),
        }
    finally:
        finished = datetime.now(timezone.utc)
        request.finished_at = finished
        attempt.finished_at = attempt.finished_at or finished
        db.commit()
        if rate_event_id is not None:
            actual_tokens = request.vendor_total_tokens or request.local_total_tokens
            try:
                release_connection_slot(
                    db,
                    rate_event_id,
                    actual_tokens=actual_tokens,
                    now=finished,
                )
            except Exception:
                # Request evidence is already committed.  A release failure
                # must not replace the provider result or leave the worker
                # response in an indeterminate state.
                logger.exception("failed to release connection rate reservation", extra={"event_id": str(rate_event_id)})
    return result


def _estimate_reserved_tokens(
    db: Session,
    snapshot: dict[str, Any],
    messages: list[dict[str, Any]],
) -> int:
    """Estimate a conservative request token reservation for TPM limits."""

    prompt_tokens = 0
    tokenizer_id = snapshot.get("tokenizer_id")
    if tokenizer_id:
        try:
            tokenizer = db.get(Tokenizer, uuid.UUID(str(tokenizer_id)))
        except (ValueError, TypeError):
            tokenizer = None
        if tokenizer is not None:
            prompt_tokens = count_tokens(tokenizer, messages) or 0
    params = snapshot.get("generation_params") or {}
    requested_output = params.get("max_tokens", params.get("max_completion_tokens"))
    if requested_output is None:
        requested_output = snapshot.get("max_output_tokens") or 0
    try:
        output_tokens = max(0, int(requested_output))
    except (TypeError, ValueError):
        output_tokens = 0
    return max(0, int(prompt_tokens)) + output_tokens


def _error_category(
    exc: Exception,
    http_status: int | None,
    *,
    stream: bool = False,
    started: float | None = None,
    total_timeout: float = 1800.0,
    attempt: Attempt | None = None,
) -> str:
    """把 transport/HTTP 异常归一化为质量重试策略可消费的类别。"""

    if http_status == 429:
        return "rate_limit"
    if http_status is not None and 500 <= http_status <= 599:
        return "temporary_network"
    if isinstance(exc, RequestTimeoutError):
        return exc.category
    if isinstance(exc, httpx.ConnectTimeout):
        return "connect_timeout"
    if isinstance(exc, httpx.TimeoutException):
        if started is not None and time.monotonic() - started >= total_timeout:
            return "total_timeout"
        if stream:
            return "first_token_timeout" if attempt is None or attempt.ttft_ms is None else "stream_idle_timeout"
        return "timeout"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, httpx.ConnectError):
        return "dns_connection"
    if http_status is not None and 300 <= http_status <= 399:
        return "redirect"
    if isinstance(exc, ValueError) and str(exc).startswith("协议错误"):
        return "protocol_error"
    if http_status is not None and 400 <= http_status <= 499:
        return "http_client_error"
    return "http_error"


def _validate_tool_calls(tool_calls: Any) -> None:
    """校验 OpenAI-compatible tool_calls 的最小结构，不限制供应商扩展字段。"""

    if not isinstance(tool_calls, list):
        raise ValueError("协议错误: tool_calls 必须为数组")
    for call in tool_calls:
        if not isinstance(call, dict) or not call.get("id") or call.get("type") != "function":
            raise ValueError("协议错误: tool_call 缺少 id 或 type=function")
        function = call.get("function")
        if not isinstance(function, dict) or not function.get("name"):
            raise ValueError("协议错误: tool_call.function 缺少 name")
        if "arguments" not in function:
            raise ValueError("协议错误: tool_call.function 缺少 arguments")
        arguments = function.get("arguments")
        if not isinstance(arguments, str):
            raise ValueError("协议错误: tool_call.function.arguments 必须为字符串")
        try:
            json.loads(arguments)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "协议错误: tool_call.function.arguments 必须为有效 JSON"
            ) from exc


def _validate_chat_response(body: Any) -> None:
    """Validate the minimum OpenAI-compatible chat response envelope."""

    if not isinstance(body, dict):
        raise ValueError("协议错误: 响应必须为 JSON 对象")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("协议错误: choices 必须为非空数组")
    first = choices[0]
    if not isinstance(first, dict):
        raise ValueError("协议错误: choices[0] 必须为对象")
    message = first.get("message")
    if not isinstance(message, dict):
        raise ValueError("协议错误: choices[0].message 必须为对象")
    content = message.get("content")
    reasoning_content = message.get("reasoning_content")
    tool_calls = message.get("tool_calls")
    if content is not None and not isinstance(content, str):
        raise ValueError("协议错误: message.content 必须为字符串或 null")
    if reasoning_content is not None and not isinstance(reasoning_content, str):
        raise ValueError("协议错误: message.reasoning_content 必须为字符串或 null")
    if (
        not tool_calls
        and (content is None or not content.strip())
        and (reasoning_content is None or not reasoning_content.strip())
    ):
        raise ValueError("协议错误: message 必须包含 content、reasoning_content 或 tool_calls")
    if tool_calls is not None:
        _validate_tool_calls(tool_calls)


def _populate_stream_metrics(
    attempt: Attempt, usage: dict[str, Any], latency_ms: float, *, stream: bool
) -> None:
    """从流式增量到达时序补齐 ITL/TPOT/速度指标。"""

    if not stream or latency_ms <= 0:
        return
    completion_tokens = usage.get("completion_tokens")
    try:
        completion_count = int(completion_tokens)
    except (TypeError, ValueError):
        return
    if completion_count <= 0:
        return
    itl_values = (attempt.itl_series or {}).get("values", []) if isinstance(attempt.itl_series, dict) else []
    timestamps = (attempt.itl_series or {}).get("timestamps", []) if isinstance(attempt.itl_series, dict) else []
    if timestamps:
        # Timestamps are produced from the configured local Tokenizer when it
        # is available, so they define output-token boundaries more accurately
        # than provider SSE chunk counts.
        completion_count = len(timestamps)
    numeric_itl = []
    for value in itl_values:
        try:
            numeric_itl.append(float(value))
        except (TypeError, ValueError):
            continue
    if timestamps and len(timestamps) >= 2:
        try:
            generation_ms = max(0.0, (float(timestamps[-1]) - float(timestamps[0])) * 1000)
        except (TypeError, ValueError):
            generation_ms = 0.0
    else:
        generation_ms = max(0.0, latency_ms - float(attempt.ttft_ms or 0))
    if generation_ms > 0:
        attempt.avg_speed = round(completion_count / (generation_ms / 1000), 4)
    if attempt.ttft_ms is not None and completion_count > 1 and generation_ms > 0:
        attempt.tpot_ms = round(generation_ms / (completion_count - 1), 4)
    if numeric_itl:
        if timestamps and len(timestamps) >= 10:
            window_speeds = []
            for start, end in zip(timestamps, timestamps[9:]):
                try:
                    duration = float(end) - float(start)
                    if duration > 0:
                        window_speeds.append(10 / duration)
                except (TypeError, ValueError):
                    continue
            if window_speeds:
                attempt.peak_speed = round(max(window_speeds), 4)
        if timestamps:
            peak_tps = 0.0
            for index, start in enumerate(timestamps):
                try:
                    start_value = float(start)
                except (TypeError, ValueError):
                    continue
                count = 0
                for end in timestamps[index:]:
                    try:
                        if float(end) - start_value <= 1.0:
                            count += 1
                        else:
                            break
                    except (TypeError, ValueError):
                        continue
                peak_tps = max(peak_tps, count)
            attempt.peak_tps = round(peak_tps, 4) if peak_tps else None
        elif numeric_itl:
            # Compatibility for old evidence rows that only have ITL values.
            attempt.peak_speed = round(1000 / min(numeric_itl), 4)
            attempt.peak_tps = attempt.peak_speed


def _read_stream(
    client: Any,
    url: str,
    payload: dict,
    headers: dict,
    started: float,
    attempt: Attempt,
    tokenizer: Tokenizer | None = None,
    *,
    secret: str | None = None,
    first_token_timeout: float = 300.0,
    stream_idle_timeout: float = 120.0,
    total_timeout: float = 1800.0,
):
    """读取 SSE 流并提取内容、usage 与 TTFT。"""
    chunks: list[str] = []
    reasoning_chunks: list[str] = []
    raw_lines: list[str] = []
    usage: dict[str, Any] = {}
    token_times: list[float] = []
    token_count = 0
    tool_calls: dict[int, dict[str, Any]] = {}
    first_token_at: float | None = None
    saw_valid_chunk = False
    saw_done = False
    last_event_at: float | None = None
    timeout_clock = started if started > 0 else None
    with client.stream("POST", url, json=payload, headers=headers) as response:
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"HTTP {response.status_code}")
        for line in response.iter_lines():
            now = time.monotonic()
            if timeout_clock is not None:
                elapsed = now - timeout_clock
                if elapsed > total_timeout:
                    raise RequestTimeoutError("total_timeout")
                if first_token_at is None and elapsed > first_token_timeout:
                    raise RequestTimeoutError("first_token_timeout")
                if last_event_at is not None and now - last_event_at > stream_idle_timeout:
                    raise RequestTimeoutError("stream_idle_timeout")
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="replace")
            raw_lines.append(line)
            # Persist incrementally so malformed/truncated streams still leave
            # the received evidence after the outer handler commits.
            attempt.raw_response = redact_sensitive_text(
                "\n".join(raw_lines)[-1_000_000:], secret
            )
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if saw_done:
                raise ValueError("协议错误: [DONE] 后仍收到数据")
            if data == "[DONE]":
                saw_done = True
                continue
            if not data:
                raise ValueError("协议错误: SSE data 不能为空")
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError as exc:
                raise ValueError("协议错误: SSE data 不是有效 JSON") from exc
            if not isinstance(chunk, dict):
                raise ValueError("协议错误: SSE chunk 必须为对象")
            choices = chunk.get("choices")
            if choices is not None and not isinstance(choices, list):
                raise ValueError("协议错误: SSE choices 必须为数组")
            saw_valid_chunk = True
            last_event_at = now
            usage.update(chunk.get("usage") or {})
            first_choice = (choices or [{}])[0]
            if not isinstance(first_choice, dict):
                raise ValueError("协议错误: SSE choices[0] 必须为对象")
            delta_payload = (first_choice.get("delta") or {})
            if not isinstance(delta_payload, dict):
                raise ValueError("协议错误: SSE delta 必须为对象")
            delta = delta_payload.get("content")
            if delta:
                chunks.append(delta)
                now = time.monotonic()
                if tokenizer is not None:
                    counted = count_tokens(tokenizer, "".join(chunks))
                    if counted is not None and counted > token_count:
                        token_times.extend([now] * (counted - token_count))
                        token_count = counted
                else:
                    token_times.append(now)
                if token_times:
                    first_token_at = first_token_at or now
            reasoning_delta = delta_payload.get("reasoning_content")
            if reasoning_delta is not None:
                if not isinstance(reasoning_delta, str):
                    raise ValueError("协议错误: SSE reasoning_content 必须为字符串")
                reasoning_chunks.append(reasoning_delta)
                if first_token_at is None and reasoning_delta.strip():
                    first_token_at = now
            for tool_delta in delta_payload.get("tool_calls") or []:
                if not isinstance(tool_delta, dict):
                    raise ValueError("协议错误: stream tool_call 必须为对象")
                try:
                    index = int(tool_delta.get("index", 0))
                except (TypeError, ValueError) as exc:
                    raise ValueError("协议错误: stream tool_call.index 无效") from exc
                call = tool_calls.setdefault(index, {"type": "function", "function": {}})
                if tool_delta.get("id"):
                    call["id"] = tool_delta["id"]
                if tool_delta.get("type"):
                    call["type"] = tool_delta["type"]
                function_delta = tool_delta.get("function") or {}
                if not isinstance(function_delta, dict):
                    raise ValueError("协议错误: stream tool_call.function 必须为对象")
                if function_delta.get("name"):
                    call["function"]["name"] = function_delta["name"]
                if function_delta.get("arguments"):
                    call["function"]["arguments"] = call["function"].get("arguments", "") + function_delta["arguments"]
    if not saw_done:
        raise ValueError("协议错误: SSE 流缺少 [DONE]")
    if not saw_valid_chunk:
        raise ValueError("协议错误: SSE 流没有有效 chunk")
    if first_token_at is not None:
        attempt.ttft_ms = round((first_token_at - started) * 1000, 2)
    if len(token_times) > 1:
        attempt.itl_series = {
            "values": [
                round((current - previous) * 1000, 4)
                for previous, current in zip(token_times, token_times[1:])
            ],
            "timestamps": [round(value - started, 6) for value in token_times],
        }
    content = "".join(chunks)
    message: dict[str, Any] = {"content": content}
    if reasoning_chunks:
        message["reasoning_content"] = "".join(reasoning_chunks)
    if tool_calls:
        message["tool_calls"] = [tool_calls[index] for index in sorted(tool_calls)]
    body = {"choices": [{"message": message}], "usage": usage}
    # Validate the reconstructed envelope as a whole. Individual deltas may
    # be structurally valid while the final tool call is still incomplete.
    _validate_chat_response(body)
    return body, "\n".join(raw_lines), response


def _timeout_value(config: Any, key: str, default: float) -> float:
    try:
        value = float(config.get(key, default))
    except (AttributeError, TypeError, ValueError):
        return default
    return value if value > 0 else default
