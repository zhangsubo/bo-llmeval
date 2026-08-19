"""连接测试服务（PRD §8.2.4）。"""

import json
import time

import httpx

from evalplat.core.crypto import CryptoService
from evalplat.core.errors import NotFoundError
from evalplat.models.connection import Connection
from evalplat.services.connection_service import get_connection
from sqlalchemy.orm import Session


class ConnectionTestResult:
    """连接测试结果。"""

    def __init__(self, status: str, http_status: int | None = None, elapsed_ms: float | None = None,
                 ttft_ms: float | None = None, vendor_request_id: str | None = None,
                 error_body: str | None = None, error_message: str | None = None):
        self.status = status
        self.http_status = http_status
        self.elapsed_ms = elapsed_ms
        self.ttft_ms = ttft_ms
        self.vendor_request_id = vendor_request_id
        self.error_body = error_body
        self.error_message = error_message

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def test_non_stream(base_url: str, api_key: str, model_id: str,
                    timeout_connect: float = 15, timeout_read: float = 30) -> ConnectionTestResult:
    """非流式 Chat Completions 冒烟测试。"""
    url = f"{base_url}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model_id, "messages": [{"role": "user", "content": "Hello"}], "max_tokens": 5, "stream": False}

    start = time.monotonic()
    try:
        with httpx.Client(timeout=httpx.Timeout(connect=timeout_connect, read=timeout_read), follow_redirects=False) as client:
            resp = client.post(url, json=payload, headers=headers)
        elapsed_ms = (time.monotonic() - start) * 1000

        if resp.status_code == 200:
            return ConnectionTestResult(status="success", http_status=200, elapsed_ms=round(elapsed_ms, 1),
                                        vendor_request_id=resp.headers.get("x-request-id"))
        else:
            return ConnectionTestResult(status="error", http_status=resp.status_code, elapsed_ms=round(elapsed_ms, 1),
                                        error_body=resp.text[:500] if resp.text else None,
                                        vendor_request_id=resp.headers.get("x-request-id"))
    except httpx.TooManyRedirects:
        return ConnectionTestResult(status="error", error_message="3xx 重定向（平台不允许跟随重定向）")
    except httpx.ConnectTimeout:
        return ConnectionTestResult(status="error", error_message=f"连接超时 ({timeout_connect}s)")
    except httpx.ReadTimeout:
        return ConnectionTestResult(status="error", error_message=f"读取超时 ({timeout_read}s)")
    except httpx.HTTPError as e:
        return ConnectionTestResult(status="error", error_message=str(e)[:200])


def test_stream(base_url: str, api_key: str, model_id: str,
                timeout_connect: float = 15, timeout_read: float = 30) -> ConnectionTestResult:
    """流式 Chat Completions 冒烟测试。"""
    url = f"{base_url}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model_id, "messages": [{"role": "user", "content": "Hello"}], "max_tokens": 5, "stream": True}

    ttft_ms = None
    start = time.monotonic()
    try:
        with httpx.Client(timeout=httpx.Timeout(connect=timeout_connect, read=timeout_read), follow_redirects=False) as client:
            with client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    elapsed_ms = (time.monotonic() - start) * 1000
                    return ConnectionTestResult(status="error", http_status=resp.status_code,
                                                elapsed_ms=round(elapsed_ms, 1))

                for line in resp.iter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            if "error" in chunk:
                                return ConnectionTestResult(status="error", error_body=json.dumps(chunk["error"])[:500])
                            if ttft_ms is None:
                                ttft_ms = (time.monotonic() - start) * 1000
                        except json.JSONDecodeError:
                            pass

        elapsed_ms = (time.monotonic() - start) * 1000
        return ConnectionTestResult(status="success", http_status=200,
                                    ttft_ms=round(ttft_ms, 1) if ttft_ms else None,
                                    elapsed_ms=round(elapsed_ms, 1))
    except httpx.TooManyRedirects:
        return ConnectionTestResult(status="error", error_message="3xx 重定向（平台不允许跟随重定向）")
    except httpx.ConnectTimeout:
        return ConnectionTestResult(status="error", error_message=f"连接超时 ({timeout_connect}s)")
    except httpx.ReadTimeout:
        return ConnectionTestResult(status="error", error_message=f"读取超时 ({timeout_read}s)")
    except httpx.HTTPError as e:
        return ConnectionTestResult(status="error", error_message=str(e)[:200])


def run_connection_test(db: Session, connection_id: str, model_id: str, crypto: CryptoService) -> dict:
    """执行完整连接测试（非流式 + 流式）。"""
    conn = get_connection(db, connection_id)
    api_key = crypto.decrypt(conn.api_key_ciphertext)
    base_url = conn.base_url

    non_stream_result = test_non_stream(base_url, api_key, model_id)
    stream_result = test_stream(base_url, api_key, model_id)

    return {"non_stream": non_stream_result.to_dict(), "stream": stream_result.to_dict()}
