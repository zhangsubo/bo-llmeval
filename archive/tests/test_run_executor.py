"""OpenAI-compatible 单请求执行器测试。"""

import uuid

from cryptography.fernet import Fernet

from evalplat.core.crypto import CryptoService
from evalplat.models.connection import Connection
from evalplat.models.connection_rate_event import ConnectionRateEvent
from evalplat.models.attempt import Attempt
from evalplat.models.request import Request
from evalplat.models.run import Run
from evalplat.models.supplier import Supplier
from evalplat.services.run_executor import _chat_completions_url, _read_stream, _validate_chat_response, _validate_tool_calls, execute_chat_request
from evalplat.services.connection_rate_limiter import acquire_connection_slot


class _Guard:
    def check_url(self, _url):
        return True, "OK"


class _Response:
    status_code = 200
    text = '{"choices":[{"message":{"content":"pong"}}],"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}}'
    headers = {"x-request-id": "req-1", "content-type": "application/json"}

    def json(self):
        return {
            "choices": [{"message": {"content": "pong"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        }


class _CachedResponse(_Response):
    text = '{"choices":[{"message":{"content":"pong"}}],"usage":{"prompt_tokens":10,"completion_tokens":1,"total_tokens":11,"prompt_tokens_details":{"cached_tokens":6}}}'

    def json(self):
        return {
            "choices": [{"message": {"content": "pong"}}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 1,
                "total_tokens": 11,
                "prompt_tokens_details": {"cached_tokens": 6},
            },
        }


class _Client:
    def __init__(self, **_kwargs):
        self.payload = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def post(self, _url, json, headers):
        self.payload = (json, headers)
        return _Response()


class _CachedClient(_Client):
    def post(self, _url, json, headers):
        self.payload = (json, headers)
        return _CachedResponse()


class _ReasoningResponse(_Response):
    text = '{"choices":[{"message":{"content":"pong","reasoning_content":"thinking"}}],"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3,"reasoning_tokens":4},"service_tier":"standard"}'

    def json(self):
        return {
            "choices": [{"message": {"content": "pong", "reasoning_content": "thinking"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3, "reasoning_tokens": 4},
            "service_tier": "standard",
        }


class _ReasoningClient(_Client):
    def post(self, _url, json, headers):
        self.payload = (json, headers)
        return _ReasoningResponse()


class _StreamResponse:
    status_code = 200
    headers = {"x-request-id": "stream-1", "content-type": "text/event-stream"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_lines(self):
        yield 'data: {"choices":[{"delta":{"content":"po"}}]}'
        yield 'data: {"choices":[{"delta":{"content":"ng"}}],"usage":{"total_tokens":3}}'
        yield "data: [DONE]"


class _StreamingClient(_Client):
    def stream(self, _method, _url, json, headers):
        self.payload = (json, headers)
        return _StreamResponse()


def test_chat_completions_url_handles_base_and_legacy_endpoint_values():
    assert _chat_completions_url("https://api.example.com") == "https://api.example.com/v1/chat/completions"
    assert _chat_completions_url("https://api.example.com/v1/") == "https://api.example.com/v1/chat/completions"
    assert _chat_completions_url("https://api.example.com/v1/chat/completions") == "https://api.example.com/v1/chat/completions"
    assert _chat_completions_url("https://api.example.com/completions") == "https://api.example.com/completions"


def test_execute_chat_request_persists_usage_and_response(db):
    supplier = Supplier(name="供应商", short_name="supplier", type="原厂")
    db.add(supplier)
    db.flush()
    key = "sk-test"
    crypto = CryptoService(Fernet.generate_key().decode())
    connection = Connection(
        supplier_id=supplier.id,
        name="连接",
        base_url="https://api.example.com",
        api_key_ciphertext=crypto.encrypt(key),
    )
    db.add(connection)
    db.flush()
    run = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="running",
        run_snapshot={"connection_id": str(connection.id), "model_id": "model-a", "generation_params": {"temperature": 0}},
    )
    db.add(run)
    db.commit()

    result = execute_chat_request(
        db,
        run,
        [{"role": "user", "content": "ping"}],
        crypto,
        _Guard(),
        client_factory=_Client,
    )

    assert result["status"] == "completed"


def test_execute_chat_request_persists_cached_usage(db):
    supplier = Supplier(name="缓存供应商", short_name="cached", type="原厂")
    db.add(supplier)
    db.flush()
    crypto = CryptoService(Fernet.generate_key().decode())
    connection = Connection(
        supplier_id=supplier.id,
        name="缓存连接",
        base_url="https://api.example.com",
        api_key_ciphertext=crypto.encrypt("sk-test"),
    )
    db.add(connection)
    db.flush()
    run = Run(
        task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="running",
        run_snapshot={"connection_id": str(connection.id), "model_id": "model-a"},
    )
    db.add(run)
    db.commit()

    result = execute_chat_request(
        db, run, [{"role": "user", "content": "ping"}], crypto, _Guard(), client_factory=_CachedClient
    )

    request = db.query(Request).filter(Request.id == result["request_id"]).one()
    assert request.vendor_cached_tokens == 6


def test_execute_chat_request_extracts_reasoning_and_service_tier(db):
    supplier = Supplier(name="推理供应商", short_name="reasoning", type="原厂")
    db.add(supplier)
    db.flush()
    crypto = CryptoService(Fernet.generate_key().decode())
    connection = Connection(
        supplier_id=supplier.id,
        name="推理连接",
        base_url="https://api.example.com",
        api_key_ciphertext=crypto.encrypt("sk-test"),
    )
    db.add(connection)
    db.flush()
    run = Run(
        task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="running",
        run_snapshot={"connection_id": str(connection.id), "model_id": "model-a"},
    )
    db.add(run)
    db.commit()

    result = execute_chat_request(
        db, run, [{"role": "user", "content": "ping"}], crypto, _Guard(), client_factory=_ReasoningClient
    )

    attempt = db.query(Attempt).filter(Attempt.id == result["attempt_id"]).one()
    assert attempt.extracted_fields["reasoning_content"] == "thinking"
    assert attempt.extracted_fields["reasoning_tokens"] == 4
    assert attempt.extracted_fields["service_tier"] == "standard"


def test_execute_stream_records_ttft(db):
    supplier = Supplier(name="供应商", short_name="supplier", type="原厂")
    db.add(supplier)
    db.flush()
    crypto = CryptoService(Fernet.generate_key().decode())
    connection = Connection(
        supplier_id=supplier.id,
        name="连接",
        base_url="https://api.example.com",
        api_key_ciphertext=crypto.encrypt("sk-test"),
    )
    db.add(connection)
    db.flush()
    run = Run(
        task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="running",
        run_snapshot={"connection_id": str(connection.id), "model_id": "model-a"},
    )
    db.add(run)
    db.commit()

    result = execute_chat_request(
        db, run, [{"role": "user", "content": "ping"}], crypto, _Guard(),
        stream=True, client_factory=_StreamingClient,
    )

    assert result["status"] == "completed"
    refreshed = db.get(Run, run.id)
    assert refreshed is not None
    attempt = db.query(Attempt).one()
    assert attempt.ttft_ms is not None
    assert attempt.itl_series is not None
    assert len(attempt.itl_series["values"]) == 1


def test_tool_call_shape_is_validated_and_unknown_fields_are_allowed():
    _validate_tool_calls([
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "lookup", "arguments": "{}"},
            "vendor_extension": {"trace": "x"},
        }
    ])

    import pytest
    with pytest.raises(ValueError):
        _validate_tool_calls([{"id": "call-1", "type": "function", "function": {}}])
    with pytest.raises(ValueError, match="arguments 必须为有效 JSON"):
        _validate_tool_calls([
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "lookup", "arguments": "{not-json"},
            }
        ])


def test_chat_response_requires_non_empty_choices_and_message():
    import pytest

    with pytest.raises(ValueError, match="choices 必须为非空数组"):
        _validate_chat_response({"choices": []})
    with pytest.raises(ValueError, match="message 必须包含"):
        _validate_chat_response({"choices": [{"message": {"content": None}}]})
    with pytest.raises(ValueError, match="message 必须包含"):
        _validate_chat_response({"choices": [{"message": {"content": ""}}]})

    _validate_chat_response({"choices": [{"message": {"content": "", "reasoning_content": "thinking"}}]})


def test_stream_parser_merges_tool_call_deltas():
    class Response:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def iter_lines(self):
            yield 'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","type":"function","function":{"name":"lookup","arguments":"{\\"q\\":"}}]}}]}'
            yield 'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"x\\"}"}}]}}]}'
            yield "data: [DONE]"

    class Client:
        def stream(self, *_args, **_kwargs):
            return Response()

    body, _, _ = _read_stream(Client(), "https://example.test/v1/chat/completions", {}, {}, 0.0, Attempt())

    tool_call = body["choices"][0]["message"]["tool_calls"][0]
    assert tool_call["id"] == "call-1"
    assert tool_call["function"]["name"] == "lookup"
    assert tool_call["function"]["arguments"] == '{"q":"x"}'


def test_stream_parser_rejects_incomplete_tool_call_after_reconstruction():
    class Response:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def iter_lines(self):
            yield 'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"type":"function","function":{"arguments":"{}"}}]}}]}'
            yield "data: [DONE]"

    class Client:
        def stream(self, *_args, **_kwargs):
            return Response()

    import pytest

    with pytest.raises(ValueError, match="tool_call 缺少 id"):
        _read_stream(Client(), "https://example.test/v1/chat/completions", {}, {}, 0.0, Attempt())


def test_stream_http_error_is_not_treated_as_completed():
    class Response:
        status_code = 503
        headers = {"content-type": "text/event-stream"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def iter_lines(self):
            yield 'data: {"error":"temporary"}'

    class Client:
        def stream(self, *_args, **_kwargs):
            return Response()

    import pytest

    with pytest.raises(RuntimeError, match="HTTP 503"):
        _read_stream(Client(), "https://example.test/v1/chat/completions", {}, {}, 0.0, Attempt())


def test_stream_redirect_is_not_treated_as_completed():
    class Response:
        status_code = 302
        headers = {"location": "https://other.example"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def iter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"pong"}}]}'

    class Client:
        def stream(self, *_args, **_kwargs):
            return Response()

    import pytest

    with pytest.raises(RuntimeError, match="HTTP 302"):
        _read_stream(Client(), "https://example.test/v1/chat/completions", {}, {}, 0.0, Attempt())


def test_nonstream_redirect_is_recorded_as_failure(db):
    class RedirectResponse(_Response):
        status_code = 302
        text = '{"choices":[{"message":{"content":"redirected"}}]}'

    class RedirectClient(_Client):
        def post(self, _url, json, headers):
            self.payload = (json, headers)
            return RedirectResponse()

    supplier = Supplier(name="重定向供应商", short_name="redirect", type="原厂")
    db.add(supplier)
    db.flush()
    crypto = CryptoService(Fernet.generate_key().decode())
    connection = Connection(
        supplier_id=supplier.id,
        name="重定向连接",
        base_url="https://api.example.com",
        api_key_ciphertext=crypto.encrypt("sk-test"),
    )
    db.add(connection)
    db.flush()
    run = Run(
        task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="running",
        run_snapshot={"connection_id": str(connection.id), "model_id": "model-a"},
    )
    db.add(run)
    db.commit()

    result = execute_chat_request(
        db, run, [{"role": "user", "content": "ping"}], crypto, _Guard(),
        client_factory=RedirectClient,
    )

    assert result["status"] == "failed"
    assert result["error_category"] == "redirect"


def test_stream_protocol_errors_are_explicit():
    import pytest

    class Response:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def iter_lines(self):
            yield "data: {not-json}"

    class Client:
        def stream(self, *_args, **_kwargs):
            return Response()

    with pytest.raises(ValueError, match="SSE data 不是有效 JSON"):
        _read_stream(Client(), "https://example.test/v1/chat/completions", {}, {}, 0.0, Attempt())

    class DoneOnlyResponse(Response):
        def iter_lines(self):
            yield "data: [DONE]"

    class DoneOnlyClient(Client):
        def stream(self, *_args, **_kwargs):
            return DoneOnlyResponse()

    with pytest.raises(ValueError, match="没有有效 chunk"):
        _read_stream(DoneOnlyClient(), "https://example.test/v1/chat/completions", {}, {}, 0.0, Attempt())


def test_execute_chat_request_obeys_connection_concurrency_limit(db):
    supplier = Supplier(name="限流供应商", short_name="rate", type="原厂")
    db.add(supplier)
    db.flush()
    crypto = CryptoService(Fernet.generate_key().decode())
    connection = Connection(
        supplier_id=supplier.id,
        name="限流连接",
        base_url="https://api.example.com",
        api_key_ciphertext=crypto.encrypt("sk-test"),
        limits={"max_concurrency": 1},
    )
    db.add(connection)
    db.flush()
    run = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="running",
        run_snapshot={"connection_id": str(connection.id), "model_id": "model-a"},
    )
    db.add(run)
    db.commit()
    held = acquire_connection_slot(db, connection.id, connection.limits)
    called = False

    class NeverCalledClient(_Client):
        def post(self, *_args, **_kwargs):
            nonlocal called
            called = True
            return _Response()

    result = execute_chat_request(
        db,
        run,
        [{"role": "user", "content": "ping"}],
        crypto,
        _Guard(),
        client_factory=NeverCalledClient,
    )

    assert held.allowed is True
    assert result["status"] == "failed"
    assert result["error_category"] == "connection_limit"
    assert called is False


def test_execute_chat_request_releases_connection_slot_with_actual_tokens(db):
    supplier = Supplier(name="释放供应商", short_name="release", type="原厂")
    db.add(supplier)
    db.flush()
    key = "sk-test"
    crypto = CryptoService(Fernet.generate_key().decode())
    connection = Connection(
        supplier_id=supplier.id,
        name="释放连接",
        base_url="https://api.example.com",
        api_key_ciphertext=crypto.encrypt(key),
        limits={"max_concurrency": 1, "tpm": 100},
    )
    db.add(connection)
    db.flush()
    run = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="running",
        run_snapshot={"connection_id": str(connection.id), "model_id": "model-a"},
    )
    db.add(run)
    db.commit()

    result = execute_chat_request(
        db,
        run,
        [{"role": "user", "content": "ping"}],
        crypto,
        _Guard(),
        client_factory=_Client,
    )

    event = db.query(ConnectionRateEvent).one()
    assert result["status"] == "completed"
    assert event.status == "released"
    assert event.actual_tokens == 3


def test_limiter_failure_is_persisted_as_failed_request(db, monkeypatch):
    supplier = Supplier(name="限流异常供应商", short_name="rate-error", type="原厂")
    db.add(supplier)
    db.flush()
    crypto = CryptoService(Fernet.generate_key().decode())
    connection = Connection(
        supplier_id=supplier.id,
        name="限流异常连接",
        base_url="https://api.example.com",
        api_key_ciphertext=crypto.encrypt("sk-test"),
        limits={"max_concurrency": 1},
    )
    db.add(connection)
    db.flush()
    run = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="running",
        run_snapshot={"connection_id": str(connection.id), "model_id": "model-a"},
    )
    db.add(run)
    db.commit()
    monkeypatch.setattr(
        "evalplat.services.run_executor.acquire_connection_slot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("limiter unavailable")),
    )

    result = execute_chat_request(
        db,
        run,
        [{"role": "user", "content": "ping"}],
        crypto,
        _Guard(),
        client_factory=_Client,
    )

    request = db.query(Request).filter(Request.run_id == run.id).one()
    attempt = db.query(Attempt).filter(Attempt.request_id == request.id).one()
    assert result["status"] == "failed"
    assert request.status == "failed"
    assert attempt.error_category == "connection_limit_error"
