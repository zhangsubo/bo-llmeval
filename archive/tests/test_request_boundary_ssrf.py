from evalplat.core.ssrf import SSRFGuard
from evalplat.services.model_service import sync_models
from evalplat.services.test_service import test_models_endpoint as run_models_endpoint_test


def test_connection_test_rechecks_ssrf_before_http(monkeypatch):
    called = False

    def fail_http(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("HTTP must not be attempted")

    monkeypatch.setattr("httpx.Client", fail_http)
    result = run_models_endpoint_test(
        "http://127.0.0.1", "secret", ssrf_guard=SSRFGuard()
    )

    assert result.status == "error"
    assert "SSRF" in (result.error_message or "")
    assert not called


def test_model_sync_rechecks_ssrf_before_http(db, monkeypatch):
    def fail_http(*args, **kwargs):
        raise AssertionError("HTTP must not be attempted")

    monkeypatch.setattr("httpx.Client", fail_http)
    result = sync_models(
        db,
        __import__("uuid").uuid4(),
        "http://127.0.0.1",
        "secret",
        SSRFGuard(),
    )

    assert result["total"] == 0
    assert "SSRF" in result["error"]
