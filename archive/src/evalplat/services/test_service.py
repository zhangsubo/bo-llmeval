"""连接测试服务（PRD §8.2.4）。

使用 GET /v1/models 测试连通性，无需指定 model_id。
"""

import time
import json
from datetime import datetime, timezone

import httpx

from evalplat.core.crypto import CryptoService
from evalplat.core.ssrf import SSRFGuard
from evalplat.services.connection_service import get_connection
from evalplat.services.protocol_validation import extract_model_ids, openai_api_url
from evalplat.services.security_service import redact_sensitive_text
from sqlalchemy.orm import Session


class ConnectionTestResult:
    """连接测试结果。"""

    def __init__(self, status: str, http_status: int | None = None, elapsed_ms: float | None = None,
                 model_count: int | None = None, vendor_request_id: str | None = None,
                 error_body: str | None = None, error_message: str | None = None,
                 capabilities: dict[str, dict] | None = None,
                 model_id: str | None = None,
                 redirect_location: str | None = None):
        self.status = status
        self.http_status = http_status
        self.elapsed_ms = elapsed_ms
        self.model_count = model_count
        self.vendor_request_id = vendor_request_id
        self.error_body = error_body
        self.error_message = error_message
        self.capabilities = capabilities
        self.model_id = model_id
        self.redirect_location = redirect_location

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def _redirect_fields(response: object, secret: str | None = None) -> dict[str, str]:
    """Return a redacted redirect diagnostic without following it."""

    headers = getattr(response, "headers", {}) or {}
    location = headers.get("location")
    result = {"error_message": "3xx 重定向（平台不允许跟随重定向）"}
    if location:
        result["redirect_location"] = redact_sensitive_text(str(location)[:500], secret)
    return result


def test_models_endpoint(base_url: str, api_key: str,
                         timeout_connect: float = 15, timeout_read: float = 30,
                         ssrf_guard: SSRFGuard | None = None,
                         secret: str | None = None,
                         verify: bool = True,
                         client_factory=httpx.Client) -> ConnectionTestResult:
    """调用 GET /v1/models 测试连通性。"""
    guard = ssrf_guard or SSRFGuard()
    target = None
    if hasattr(guard, "resolve_url"):
        allowed, reason, target = guard.resolve_url(base_url)
    else:
        allowed, reason = guard.check_url(base_url)
    if not allowed:
        return ConnectionTestResult(status="error", error_message=f"SSRF 校验失败: {reason}")
    try:
        url = openai_api_url(base_url, "models")
    except ValueError as exc:
        return ConnectionTestResult(
            status="error",
            error_message=redact_sensitive_text(str(exc), secret),
        )
    headers = {"Authorization": f"Bearer {api_key}"}

    start = time.monotonic()
    try:
        client_options: dict[str, object] = {
            "timeout": httpx.Timeout(timeout_connect, read=timeout_read),
            "follow_redirects": False,
            "verify": verify,
            "trust_env": False,
        }
        if target is not None and hasattr(guard, "transport_for"):
            client_options["transport"] = guard.transport_for(target, verify=verify)
        with client_factory(**client_options) as client:
            resp = client.get(url, headers=headers)
        elapsed_ms = (time.monotonic() - start) * 1000

        if resp.status_code == 200:
            try:
                model_ids = extract_model_ids(resp.json())
            except (TypeError, ValueError) as exc:
                response_text = getattr(resp, "text", "")
                return ConnectionTestResult(
                    status="error",
                    http_status=200,
                    elapsed_ms=round(elapsed_ms, 1),
                    error_body=redact_sensitive_text(response_text[:500], secret) if response_text else None,
                    error_message=f"模型目录响应格式无效: {exc}",
                )
            return ConnectionTestResult(
                status="success", http_status=200, elapsed_ms=round(elapsed_ms, 1),
                model_count=len(model_ids),
                vendor_request_id=redact_sensitive_text(resp.headers.get("x-request-id"), secret) or None,
                capabilities=None,
                model_id=model_ids[0] if model_ids else None,
            )
        else:
            redirect_location = None
            error_message = None
            if 300 <= resp.status_code < 400:
                redirect_location = (resp.headers.get("location") if resp.headers else None)
                error_message = "3xx 重定向（平台不允许跟随重定向）"
            return ConnectionTestResult(
                status="error", http_status=resp.status_code, elapsed_ms=round(elapsed_ms, 1),
                error_body=redact_sensitive_text(resp.text[:500], secret) if resp.text else None,
                vendor_request_id=redact_sensitive_text(resp.headers.get("x-request-id"), secret) or None,
                error_message=error_message,
                redirect_location=redact_sensitive_text(str(redirect_location)[:500], secret) if redirect_location else None,
            )
    except httpx.TooManyRedirects:
        return ConnectionTestResult(status="error", error_message="3xx 重定向（平台不允许跟随重定向）")
    except httpx.ConnectTimeout:
        return ConnectionTestResult(status="error", error_message=f"连接超时 ({timeout_connect}s)")
    except httpx.ReadTimeout:
        return ConnectionTestResult(status="error", error_message=f"读取超时 ({timeout_read}s)")
    except httpx.HTTPError as e:
        return ConnectionTestResult(
            status="error", error_message=redact_sensitive_text(str(e)[:200], secret)
        )
    except (TypeError, ValueError, AttributeError) as exc:
        return ConnectionTestResult(
            status="error",
            error_message=f"模型目录响应格式无效: {redact_sensitive_text(str(exc)[:200], secret)}",
        )


def test_chat_capability(
    base_url: str,
    api_key: str,
    model_id: str,
    *,
    stream: bool,
    ssrf_guard: SSRFGuard | None = None,
    secret: str | None = None,
    verify: bool = True,
    timeout_connect: float = 15,
    timeout_read: float = 30,
    client_factory=httpx.Client,
) -> dict:
    """发送一次最小 Chat Completions 探测，验证流式或非流式能力。"""

    guard = ssrf_guard or SSRFGuard()
    target = None
    if hasattr(guard, "resolve_url"):
        allowed, reason, target = guard.resolve_url(base_url)
    else:
        allowed, reason = guard.check_url(base_url)
    if not allowed:
        return {"status": "error", "error_message": f"SSRF 校验失败: {reason}"}

    try:
        url = openai_api_url(base_url, "chat/completions")
    except ValueError as exc:
        return {"status": "error", "error_message": redact_sensitive_text(str(exc), secret)}
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "stream": stream,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    options: dict[str, object] = {
        "timeout": httpx.Timeout(timeout_connect, read=timeout_read),
        "follow_redirects": False,
        "verify": verify,
        "trust_env": False,
    }
    if target is not None and hasattr(guard, "transport_for"):
        options["transport"] = guard.transport_for(target, verify=verify)
    started = time.monotonic()
    try:
        with client_factory(**options) as client:
            if stream:
                with client.stream("POST", url, json=payload, headers=headers) as response:
                    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
                    if not 200 <= response.status_code < 300:
                        # ``client.stream`` does not read the body eagerly;
                        # accessing ``response.text`` before consuming it
                        # raises ``ResponseNotRead`` on real httpx responses.
                        # Consume the bounded error body explicitly so a
                        # provider 4xx/5xx is persisted as test evidence.
                        read_body = getattr(response, "read", None)
                        if callable(read_body):
                            try:
                                read_body()
                            except (RuntimeError, httpx.HTTPError):
                                pass
                        error_text = ""
                        try:
                            error_text = response.text
                        except (RuntimeError, httpx.ResponseNotRead):
                            error_text = ""
                        result = {
                            "status": "error",
                            "http_status": response.status_code,
                            "elapsed_ms": elapsed_ms,
                            "error_body": redact_sensitive_text(error_text[:500], secret) if error_text else None,
                        }
                        if 300 <= response.status_code < 400:
                            result.update(_redirect_fields(response, secret))
                        return result
                    content_type = str(response.headers.get("content-type", "")).lower()
                    if content_type and "text/event-stream" not in content_type:
                        return {
                            "status": "error",
                            "http_status": response.status_code,
                            "elapsed_ms": elapsed_ms,
                            "vendor_request_id": redact_sensitive_text(response.headers.get("x-request-id"), secret) or None,
                            "error_message": "SSE 协议错误: Content-Type 必须为 text/event-stream",
                        }
                    protocol_error = _validate_sse_probe(response.iter_lines())
                    status = "success" if protocol_error is None else "error"
                    result = {
                        "status": status,
                        "http_status": response.status_code,
                        "elapsed_ms": elapsed_ms,
                        "vendor_request_id": redact_sensitive_text(response.headers.get("x-request-id"), secret) or None,
                    }
                    if status != "success":
                        result["error_message"] = protocol_error
                    return result

            response = client.post(url, json=payload, headers=headers)
            elapsed_ms = round((time.monotonic() - started) * 1000, 1)
            if not 200 <= response.status_code < 300:
                result = {
                    "status": "error",
                    "http_status": response.status_code,
                    "elapsed_ms": elapsed_ms,
                    "error_body": redact_sensitive_text(response.text[:500], secret),
                }
                if 300 <= response.status_code < 400:
                    result.update(_redirect_fields(response, secret))
                return result
            data = response.json()
            choices = data.get("choices") if isinstance(data, dict) else None
            status = "success" if isinstance(choices, list) and choices else "error"
            result = {
                "status": status,
                "http_status": response.status_code,
                "elapsed_ms": elapsed_ms,
                "vendor_request_id": redact_sensitive_text(response.headers.get("x-request-id"), secret) or None,
            }
            if status != "success":
                result["error_message"] = "非流式响应缺少 choices"
            return result
    except httpx.TooManyRedirects:
        return {"status": "error", "error_message": "3xx 重定向（平台不允许跟随重定向）"}
    except httpx.ConnectTimeout:
        return {"status": "error", "error_message": f"连接超时 ({timeout_connect}s)"}
    except httpx.ReadTimeout:
        return {"status": "error", "error_message": f"读取超时 ({timeout_read}s)"}
    except httpx.HTTPError as exc:
        return {
            "status": "error",
            "error_message": redact_sensitive_text(str(exc)[:200], secret),
        }
    except (ValueError, TypeError, AttributeError) as exc:
        return {
            "status": "error",
            "error_message": redact_sensitive_text(str(exc)[:200], secret),
        }


def run_connection_test(
    db: Session, connection_id: str, crypto: CryptoService, ssrf_guard: SSRFGuard | None = None
) -> dict:
    """执行连接测试：模型目录、非流式和流式 Chat Completions。"""
    conn = get_connection(db, connection_id)
    if conn.status != "active":
        return _persist_test_result(db, conn, {"status": "error", "error_message": "连接未启用"})
    if not conn.api_key_ciphertext:
        return _persist_test_result(db, conn, {"status": "error", "error_message": "连接未配置 API Key"})
    api_key = crypto.decrypt(conn.api_key_ciphertext)
    base_url = conn.base_url

    result = test_models_endpoint(
        base_url,
        api_key,
        ssrf_guard=ssrf_guard,
        secret=api_key,
        verify=not conn.tls_skip_verify,
    )
    response = result.to_dict()
    if result.status != "success":
        return _persist_test_result(db, conn, response)

    # 选用目录中的第一个模型做最小能力探测；模型目录为空时明确报告
    # not_tested，而不是把“无法探测”误报为协议兼容。
    model_id = response.pop("model_id", None)
    if not model_id:
        response["capabilities"] = {
            "chat_nonstream": {"status": "not_tested", "reason": "模型目录为空"},
            "chat_stream": {"status": "not_tested", "reason": "模型目录为空"},
        }
        return _persist_test_result(db, conn, response)
    capabilities = {}
    for stream, key in ((False, "chat_nonstream"), (True, "chat_stream")):
        capabilities[key] = test_chat_capability(
            base_url,
            api_key,
            model_id,
            stream=stream,
            ssrf_guard=ssrf_guard,
            secret=api_key,
            verify=not conn.tls_skip_verify,
        )
    response["capabilities"] = capabilities
    response["capabilities_status"] = (
        "success"
        if all(item.get("status") == "success" for item in capabilities.values())
        else "partial"
    )
    return _persist_test_result(db, conn, response)


def _persist_test_result(db: Session, conn, result: dict) -> dict:
    """Persist one redacted connection probe as the latest audit evidence."""

    conn.last_test_result = dict(result)
    conn.last_test_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(conn)
    return result


def _validate_sse_probe(lines) -> str | None:
    """Validate the small SSE response used by connection smoke tests.

    A single ``data:`` line is not evidence of stream compatibility: the
    payload must be valid JSON, contain a non-empty object, terminate exactly
    once with ``[DONE]``, and never emit data after termination.
    """

    saw_chunk = False
    saw_choice = False
    saw_done = False
    for raw_line in lines:
        line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else str(raw_line)
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if saw_done:
            return "SSE 协议错误: [DONE] 后仍收到数据"
        if data == "[DONE]":
            saw_done = True
            continue
        if not data:
            return "SSE 协议错误: data 事件不能为空"
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            return "SSE 协议错误: data 不是有效 JSON"
        if not isinstance(chunk, dict) or not chunk:
            return "SSE 协议错误: chunk 必须是非空对象"
        choices = chunk.get("choices")
        if choices is not None and not isinstance(choices, list):
            return "SSE 协议错误: choices 必须为数组"
        if isinstance(choices, list) and choices:
            first = choices[0]
            if not isinstance(first, dict):
                return "SSE 协议错误: choices[0] 必须为对象"
            delta = first.get("delta")
            if delta is not None and not isinstance(delta, dict):
                return "SSE 协议错误: delta 必须为对象"
            saw_choice = True
        saw_chunk = True
    if not saw_done:
        return "SSE 协议错误: 流缺少 [DONE]"
    if not saw_chunk or not saw_choice:
        return "SSE 协议错误: 流没有有效 choices chunk"
    return None
