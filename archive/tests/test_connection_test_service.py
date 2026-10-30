"""连接兼容性测试的持久化证据。"""

from cryptography.fernet import Fernet
import uuid
import pytest

from evalplat.core.crypto import CryptoService
from evalplat.models.connection import Connection
from evalplat.models.supplier import Supplier
from evalplat.services import test_service
from evalplat.services import model_service
from evalplat.services.protocol_validation import openai_api_url


class _ModelsResponse:
    status_code = 200
    headers = {}
    text = "malformed"

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _ModelsClient:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, _url, headers=None):
        return self.response


class _ModelsClientFactory:
    def __init__(self, response):
        self.response = response

    def __call__(self, **_kwargs):
        return _ModelsClient(self.response)


class _AllowGuard:
    def check_url(self, _url):
        return True, "OK"


@pytest.mark.parametrize(
    ("base_url", "resource", "expected"),
    [
        ("https://api.example.com", "models", "https://api.example.com/v1/models"),
        ("https://api.example.com/v1/", "models", "https://api.example.com/v1/models"),
        ("https://api.example.com/prefix/v1", "chat/completions", "https://api.example.com/prefix/v1/chat/completions"),
        ("https://api.example.com/v1/chat/completions", "models", "https://api.example.com/v1/models"),
    ],
)
def test_openai_api_url_normalizes_legacy_connection_paths(base_url, resource, expected):
    assert openai_api_url(base_url, resource) == expected


def test_connection_probe_reports_empty_base_url_instead_of_raising():
    result = test_service.test_models_endpoint(
        "",
        "secret",
        ssrf_guard=_AllowGuard(),
        client_factory=_ModelsClientFactory(_ModelsResponse({"data": []})),
    )
    assert result.status == "error"
    assert "Base URL" in (result.error_message or "")


def test_sse_probe_requires_valid_chunk_and_done_marker():
    assert test_service._validate_sse_probe([
        'data: {"choices":[{"delta":{"content":"ok"}}]}',
        "data: [DONE]",
    ]) is None
    assert "缺少 [DONE]" in (test_service._validate_sse_probe([
        'data: {"choices":[{"delta":{"content":"ok"}}]}',
    ]) or "")


def test_sse_probe_rejects_invalid_json_and_duplicate_end():
    assert "有效 JSON" in (test_service._validate_sse_probe(["data: not-json", "data: [DONE]"]) or "")
    assert "[DONE] 后" in (test_service._validate_sse_probe([
        'data: {"choices":[]}',
        "data: [DONE]",
        "data: [DONE]",
    ]) or "")


def test_sse_probe_rejects_only_empty_choices():
    assert "有效 choices" in (test_service._validate_sse_probe([
        'data: {"choices":[]}',
        "data: [DONE]",
    ]) or "")


def test_stream_chat_error_reads_httpx_response_before_accessing_text():
    """A real streaming response raises ResponseNotRead until ``read()``."""

    class _StreamingErrorResponse:
        status_code = 429
        headers = {"content-type": "application/json"}

        def __init__(self):
            self._body = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            self._body = b'{"error":"rate limited"}'
            return self._body

        @property
        def text(self):
            if self._body is None:
                raise RuntimeError("ResponseNotRead")
            return self._body.decode()

        def iter_lines(self):
            return iter(())

    class _StreamingErrorClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def stream(self, *_args, **_kwargs):
            return _StreamingErrorResponse()

    result = test_service.test_chat_capability(
        "https://api.example.com",
        "secret",
        "model-a",
        stream=True,
        ssrf_guard=_AllowGuard(),
        client_factory=lambda **_kwargs: _StreamingErrorClient(),
    )

    assert result["status"] == "error"
    assert result["http_status"] == 429
    assert result["error_body"] == '{"error":"rate limited"}'


@pytest.mark.parametrize(
    "payload, expected",
    [
        (ValueError("not json"), "响应格式无效"),
        ([], "顶层必须为对象"),
        ({"data": {}}, "data 必须为数组"),
        ({"data": [{"id": ""}]}, "id 必须为非空字符串"),
        ({"data": ["model-a"]}, "必须为对象"),
    ],
)
def test_models_endpoint_rejects_malformed_payload(payload, expected):
    response = _ModelsResponse(payload)
    result = test_service.test_models_endpoint(
        "https://api.example.com",
        "secret",
        ssrf_guard=_AllowGuard(),
        client_factory=_ModelsClientFactory(response),
    )

    assert result.status == "error"
    assert expected in (result.error_message or "")


def test_models_endpoint_deduplicates_ids():
    response = _ModelsResponse({"data": [{"id": " model-a "}, {"id": "model-a"}, {"id": "model-b"}]})
    result = test_service.test_models_endpoint(
        "https://api.example.com",
        "secret",
        ssrf_guard=_AllowGuard(),
        client_factory=_ModelsClientFactory(response),
    )

    assert result.status == "success"
    assert result.model_count == 2
    assert result.model_id == "model-a"


def test_connection_test_persists_redacted_capability_evidence(db, monkeypatch):
    supplier = Supplier(name="测试供应商", short_name="probe", type="other")
    db.add(supplier)
    db.flush()
    crypto = CryptoService(Fernet.generate_key().decode())
    connection = Connection(
        supplier_id=supplier.id,
        name="测试连接",
        base_url="https://api.example.com",
        api_key_ciphertext=crypto.encrypt("sk-secret"),
    )
    db.add(connection)
    db.commit()

    monkeypatch.setattr(
        test_service,
        "test_models_endpoint",
        lambda *args, **kwargs: test_service.ConnectionTestResult(
            status="success", http_status=200, elapsed_ms=12.3,
            model_count=1, vendor_request_id="request-1", model_id="model-a",
        ),
    )
    monkeypatch.setattr(
        test_service,
        "test_chat_capability",
        lambda *args, **kwargs: {"status": "success", "http_status": 200, "elapsed_ms": 2.0},
    )

    result = test_service.run_connection_test(db, str(connection.id), crypto, ssrf_guard=None)

    assert result["capabilities_status"] == "success"
    refreshed = db.get(Connection, connection.id)
    assert refreshed is not None
    assert refreshed.last_test_result["capabilities"]["chat_stream"]["status"] == "success"
    assert refreshed.last_test_at is not None


def test_model_sync_rejects_malformed_payload_without_writing_models(db, monkeypatch):
    class _Response:
        status_code = 200
        text = "not-json"

        def json(self):
            raise ValueError("not json")

    class _Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _url, headers=None):
            return _Response()

    monkeypatch.setattr(model_service.httpx, "Client", _Client)
    result = model_service.sync_models(
        db,
        __import__("uuid").uuid4(),
        "https://api.example.com/",
        "secret",
        ssrf_guard=type("AllowGuard", (), {"check_url": lambda *_args: (True, "OK")})(),
    )

    assert result["total"] == 0
    assert result["added"] == 0
    assert "响应格式无效" in result["error"]


def test_model_sync_passes_tls_verification_setting(monkeypatch):
    seen = {}

    class _Response:
        status_code = 200

        def json(self):
            return {"data": []}

    class _Client:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _url, headers=None):
            return _Response()

    monkeypatch.setattr(model_service.httpx, "Client", _Client)
    guard = type("AllowGuard", (), {"check_url": lambda *_args: (True, "OK")})()
    model_service.sync_models(
        __import__("unittest").mock.MagicMock(),
        uuid.uuid4(),
        "https://api.example.com",
        "secret",
        ssrf_guard=guard,
        verify=False,
    )
    assert seen["verify"] is False
