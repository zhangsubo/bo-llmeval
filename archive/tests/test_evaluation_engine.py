from __future__ import annotations

import asyncio
import base64
import json
import pickle
import sqlite3

import pytest

from evalplat.services.evaluation_engine import (
    EvalScopePerformanceEngine,
    EvalScopeEngine,
    EvaluationEngineConfigurationError,
    EvaluationEngineUnavailable,
    build_evalscope_config,
    build_evalscope_perf_config,
    extract_evalscope_perf_records,
    extract_evalscope_sample_results,
    map_evalscope_perf_record,
    run_evalscope,
    run_evalscope_performance,
)


def _snapshot() -> dict:
    return {
        "model_id": "demo-model",
        "base_url": "https://vendor.example/api",
        "evalscope_datasets": ["demo_dataset"],
        "generation_params": {"temperature": 0.2},
    }


def test_build_evalscope_config_normalizes_api_base_and_preserves_generation():
    config = build_evalscope_config(_snapshot(), api_key="secret", work_dir="./data/eval")

    assert config["api_url"] == "https://vendor.example/api/v1"
    assert config["model"] == "demo-model"
    assert config["eval_type"] == "openai_api"
    assert config["datasets"] == ["demo_dataset"]
    assert config["generation_config"]["temperature"] == 0.2
    assert config["generation_config"]["stream"] is False
    assert config["api_key"] == "secret"


def test_evalscope_engine_keeps_injected_runner_compatible_without_transport():
    calls = []

    class Config:
        def __init__(self, **values):
            calls.append(values)

    engine = EvalScopeEngine(
        runner=lambda config: {"model": config.model},
        config_type=Config,
    )
    assert engine.run({"model": "demo-model"}) == {"model": "demo-model"}
    assert calls == [{"model": "demo-model"}]


def test_build_evalscope_config_rejects_missing_dataset_mapping():
    snapshot = _snapshot()
    snapshot.pop("evalscope_datasets")

    with pytest.raises(EvaluationEngineConfigurationError):
        build_evalscope_config(snapshot, api_key="secret", work_dir="./data/eval")


def test_build_evalscope_config_maps_request_cap_per_dataset():
    snapshot = {**_snapshot(), "limits": {"max_requests": 4}}
    config = build_evalscope_config(snapshot, api_key="secret", work_dir="./data/eval")
    assert config["limit"] == 4


def test_build_evalscope_config_rejects_unknown_total_token_cap():
    snapshot = {**_snapshot(), "limits": {"max_total_tokens": 100}}
    with pytest.raises(EvaluationEngineConfigurationError, match="max_total_tokens"):
        build_evalscope_config(snapshot, api_key="secret", work_dir="./data/eval")


def test_run_evalscope_uses_injected_engine_without_importing_sdk():
    calls: list[dict] = []

    class FakeEngine:
        def run(self, config):
            calls.append(config)
            return {"accuracy": 1.0}

    result = run_evalscope(_snapshot(), api_key="secret", work_dir="./data/eval", engine=FakeEngine())

    assert result == {"status": "completed", "engine": "evalscope", "result": {"accuracy": 1.0}}
    assert calls[0]["model_id"] == "demo-model"


def test_run_evalscope_honors_injected_cancellation_contract():
    class CancellableEngine:
        def run_cancellable(self, config, should_cancel):
            assert config["model_id"] == "demo-model"
            assert should_cancel() is True
            return {"status": "cancelled"}

    result = run_evalscope(
        _snapshot(),
        api_key="secret",
        work_dir="./data/eval",
        engine=CancellableEngine(),
        should_cancel=lambda: True,
    )

    assert result["status"] == "cancelled"
    assert result["engine"] == "evalscope"


def test_missing_evalscope_dependency_is_explicit():
    result = run_evalscope(
        _snapshot(), api_key="secret", work_dir="./data/eval", engine=EvalScopeEngine()
    )

    assert result["status"] == "unavailable"
    assert result["engine"] == "evalscope"


def test_evalscope_unavailable_error_redacts_api_key():
    secret = "sk-live-secret"

    class FailingEngine:
        def run(self, _config):
            raise EvaluationEngineUnavailable(f"missing dependency (api_key={secret})")

    result = run_evalscope(
        _snapshot(), api_key=secret, work_dir="./data/eval",
        engine=FailingEngine(),
    )

    assert secret not in result["error"]
    assert "[REDACTED]" in result["error"]


def test_extract_evalscope_sample_results_supports_common_shapes():
    result = extract_evalscope_sample_results({"results": [{"score": 1}, {"score": 0}]})
    assert [item["sample_index"] for item in result] == [0, 1]
    assert extract_evalscope_sample_results({"accuracy": 0.5}) == []


def test_build_evalscope_perf_config_maps_fixed_concurrency_and_messages(tmp_path):
    snapshot = {
        "run_id": "run-42",
        "model_id": "demo-model",
        "base_url": "https://vendor.example/api/",
        "messages": [{"role": "user", "content": "hello"}],
        "generation_params": {
            "temperature": 0.2,
            "max_completion_tokens": 17,
            "response_format": {"type": "json_object"},
        },
        "load_profile": {"mode": "fixed_concurrency", "concurrency": 3},
        "request_count": 8,
        "warmup_count": 2,
        "stream": True,
        "timeouts": {"connect": 1.2, "read": 3, "total": 9.1},
    }

    config = build_evalscope_perf_config(snapshot, api_key="secret", work_dir=tmp_path)

    assert config["url"] == "https://vendor.example/api/v1"
    assert config["dataset"] == "line_by_line"
    assert config["number"] == 8
    assert config["parallel"] == 3
    assert config["open_loop"] is False
    assert config["warmup_num"] == 2
    assert config["max_tokens"] == 17
    assert config["extra_args"]["response_format"] == {"type": "json_object"}
    assert config["connect_timeout"] == 2
    assert config["total_timeout"] == 10
    assert json.loads((tmp_path / "run-42" / "input_messages.jsonl").read_text())[0]["content"] == "hello"


def test_build_evalscope_perf_config_maps_fixed_rps_and_requires_cap(tmp_path):
    base = {
        "model_id": "demo-model",
        "base_url": "https://vendor.example/v1",
        "messages": [{"role": "user", "content": "hello"}],
        "request_count": 3,
        "load_profile": {"mode": "fixed_rps", "rps": 4, "max_inflight": 2},
    }
    config = build_evalscope_perf_config(base, api_key="secret", work_dir=tmp_path)
    assert config["url"] == "https://vendor.example/v1"
    assert config["open_loop"] is True
    assert config["rate"] == 4.0
    assert config["parallel"] == 2

    missing_cap = {**base, "load_profile": {"mode": "fixed_rps", "rps": 4}}
    with pytest.raises(EvaluationEngineConfigurationError, match="max_inflight"):
        build_evalscope_perf_config(missing_cap, api_key="secret", work_dir=tmp_path)


def test_build_evalscope_perf_config_rejects_dataset_path_outside_root(tmp_path):
    dataset_root = tmp_path / "datasets"
    dataset_root.mkdir()
    outside = tmp_path / "outside.jsonl"
    outside.write_text('{"messages": []}\n', encoding="utf-8")
    snapshot = {
        "model_id": "demo-model",
        "base_url": "https://vendor.example/v1",
        "dataset_snapshot": {"storage_path": str(outside)},
        "request_count": 1,
    }
    with pytest.raises(EvaluationEngineConfigurationError, match="dataset_dir"):
        build_evalscope_perf_config(
            snapshot,
            api_key="secret",
            work_dir=tmp_path / "work",
            dataset_root=dataset_root,
        )


def test_build_evalscope_perf_config_rejects_request_budget_overrun(tmp_path):
    snapshot = {
        "model_id": "demo-model",
        "base_url": "https://vendor.example",
        "messages": [{"role": "user", "content": "hello"}],
        "request_count": 3,
        "warmup_count": 2,
        "limits": {"max_requests": 4},
    }
    with pytest.raises(EvaluationEngineConfigurationError, match="max_requests"):
        build_evalscope_perf_config(snapshot, api_key="secret", work_dir=tmp_path)


def test_build_evalscope_perf_config_rejects_staircase_until_orchestrated(tmp_path):
    snapshot = {
        "model_id": "demo-model",
        "base_url": "https://vendor.example",
        "messages": [{"role": "user", "content": "hello"}],
        "load_profile": {"mode": "staircase", "stages": [{"rps": 1, "duration_seconds": 1}]},
    }
    with pytest.raises(EvaluationEngineConfigurationError, match="staircase"):
        build_evalscope_perf_config(snapshot, api_key="secret", work_dir=tmp_path)


def test_map_evalscope_perf_record_preserves_metrics_and_safe_response():
    encoded = base64.b64encode(
        pickle.dumps(['{"id":"req-7","choices":[{"message":{"content":"ok"}}]}'])
    ).decode("ascii")
    record = map_evalscope_perf_record(
        {
            "request": '{"model":"demo","messages":[{"role":"user","content":"hi"}],"stream":true}',
            "start_time": 10.0,
            "completed_time": 11.25,
            "inter_token_latencies": "[0.1, 0.2]",
            "success": 1,
            "response_messages": encoded,
            "latency": 1.25,
            "first_chunk_latency": 0.25,
            "time_per_output_token": 0.5,
            "prompt_tokens": 4,
            "completion_tokens": 2,
            "request_id": "req-7",
            "is_stream": 1,
            "status_code": 200,
            "error": None,
        },
        sample_index=9,
    )
    assert record["status"] == "completed"
    assert record["sample_index"] == 9
    assert record["latency_ms"] == 1250.0
    assert record["ttft_ms"] == 250.0
    assert record["tpot_ms"] == 500.0
    assert record["itl_series"]["milliseconds"] == [100.0, 200.0]
    assert record["vendor_usage"]["total_tokens"] == 6
    assert record["vendor_request_id"] == "req-7"
    assert record["extracted_fields"]["response_messages"][0]["id"] == "req-7"


def test_extract_evalscope_perf_records_reads_sqlite_read_only(tmp_path):
    db_path = tmp_path / "benchmark_data.db"
    with sqlite3.connect(db_path) as db:
        db.execute(
            "CREATE TABLE result (request TEXT, start_time REAL, inter_token_latencies TEXT, "
            "success INTEGER, response_messages TEXT, completed_time REAL, latency REAL, "
            "first_chunk_latency REAL, prompt_tokens INTEGER, completion_tokens INTEGER, "
            "time_per_output_token REAL, request_id TEXT, is_stream INTEGER, status_code INTEGER, error TEXT)"
        )
        db.execute(
            "INSERT INTO result VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ('{"messages":[]}', 1.0, "[]", 0, "", 2.0, 1.0, 0.0, None, None, None, None, 0, 429, "HTTP 429"),
        )
        db.commit()
    records = extract_evalscope_perf_records(db_path)
    assert len(records) == 1
    assert records[0]["error_category"] == "rate_limit"
    assert records[0]["status"] == "failed"


def test_evalscope_performance_engine_keeps_injected_runner_contract(tmp_path):
    seen = {}

    def runner(config):
        seen.update(config)
        return {"metrics": {"Total Requests": 1}}

    snapshot = {
        "model_id": "demo-model",
        "base_url": "https://vendor.example",
        "messages": [{"role": "user", "content": "hello"}],
        "load_profile": {"mode": "fixed_concurrency", "concurrency": 1},
        "request_count": 1,
    }
    result = run_evalscope_performance(
        snapshot,
        api_key="secret",
        work_dir=tmp_path,
        engine=EvalScopePerformanceEngine(runner=runner),
    )
    assert result["status"] == "completed"
    assert result["kind"] == "performance"
    assert seen["api_key"] == "secret"


def test_evalscope_performance_unavailable_error_redacts_api_key(tmp_path):
    secret = "sk-performance-secret"

    class FailingEngine:
        def run(self, _config):
            raise EvaluationEngineUnavailable(f"transport failed with {secret}")

    result = run_evalscope_performance(
        {"model_id": "demo-model", "base_url": "https://vendor.example", "messages": [{"role": "user", "content": "hello"}]},
        api_key=secret,
        work_dir=tmp_path,
        engine=FailingEngine(),
    )

    assert secret not in result["error"]
    assert "[REDACTED]" in result["error"]


def test_pinned_resolver_allows_only_original_target():
    from evalplat.core.ssrf import ResolvedTarget
    from evalplat.services.evaluation_engine import _PinnedResolver

    resolver = _PinnedResolver(ResolvedTarget("https://vendor.example", "vendor.example", 443, ("203.0.113.9",)))
    assert asyncio.run(resolver.resolve("vendor.example", 443))[0]["host"] == "203.0.113.9"
    with pytest.raises(OSError, match="已校验"):
        asyncio.run(resolver.resolve("other.example", 443))
