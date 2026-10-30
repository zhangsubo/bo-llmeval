"""Run 指标聚合测试。"""

import uuid
from datetime import datetime, timedelta, timezone

from evalplat.models.attempt import Attempt
from evalplat.models.code_execution import CodeExecution
from evalplat.models.request import Request
from evalplat.models.run import Run
from evalplat.services.metrics_service import aggregate_run_metrics


def test_aggregate_metrics_by_phase(db):
    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed", run_snapshot={"price": {"input_token_price": "1", "output_token_price": "2"}})
    db.add(run)
    db.flush()
    for index, latency in enumerate([10, 20, 30], start=1):
        request = Request(run_id=run.id, kind="perf", phase="steady", status="completed")
        request.id = index
        db.add(request)
        db.flush()
        attempt = Attempt(request_id=request.id, attempt_index=1, latency_ms=latency, itl_series={"values": [1, 2]}, avg_speed=5, peak_speed=7, peak_tps=8)
        attempt.id = index
        db.add(attempt)
    db.commit()

    result = aggregate_run_metrics(db, run.id)

    assert result["summary"]["request_count"] == 3
    assert result["summary"]["success_rate"] == 1
    assert result["by_phase"]["steady"]["latency_ms"]["p95"] == 29
    assert result["by_phase"]["steady"]["itl_ms"]["p50"] == 1.5
    assert result["summary"]["throughput_requests_per_second"] == 50
    assert len(result["summary"]["request_series"]) == 3
    assert result["summary"]["error_categories"] == {}


def test_aggregate_metrics_includes_tokens_cost_and_throughput(db):
    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed", run_snapshot={"price": {"input_token_price": "1", "output_token_price": "2"}})
    db.add(run)
    db.flush()
    request = Request(run_id=run.id, kind="perf", phase="steady", status="completed", vendor_prompt_tokens=100, vendor_completion_tokens=50, vendor_total_tokens=150)
    request.id = 99
    db.add(request)
    db.flush()
    attempt = Attempt(request_id=request.id, attempt_index=1, latency_ms=1000)
    attempt.id = 99
    db.add(attempt)
    db.commit()

    summary = aggregate_run_metrics(db, run.id)["summary"]

    assert summary["tokens"] == {"prompt": 100, "completion": 50, "total": 150, "source": "vendor"}
    assert summary["throughput_tokens_per_second"] == 150
    assert summary["cost"]["amount"] == 0.2
    assert summary["cost"]["estimated"] is False


def test_aggregate_metrics_preserves_partial_vendor_usage(db):
    run = Run(
        task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed",
        run_snapshot={"price": {"input_token_price": "1", "output_token_price": "2"}},
    )
    db.add(run)
    db.flush()
    total_only = Request(
        id=201, run_id=run.id, kind="perf", phase="steady", status="completed",
        vendor_total_tokens=150,
    )
    split_without_total = Request(
        id=202, run_id=run.id, kind="perf", phase="steady", status="completed",
        vendor_prompt_tokens=100, vendor_completion_tokens=50,
    )
    db.add_all([total_only, split_without_total])
    db.flush()
    db.add_all([
        Attempt(id=201, request_id=201, attempt_index=1, latency_ms=1000),
        Attempt(id=202, request_id=202, attempt_index=1, latency_ms=1000),
    ])
    db.commit()

    summary = aggregate_run_metrics(db, run.id)["summary"]

    assert summary["tokens"] == {"prompt": 100, "completion": 50, "total": 300, "source": "vendor"}
    assert summary["tokens_by_source"]["vendor"]["total"] == 300
    assert summary["cost"]["amount"] is None
    assert summary["cost"]["estimated"] is True


def test_schedule_dropped_is_not_provider_failure(db):
    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed")
    db.add(run)
    db.flush()
    sent = Request(id=211, run_id=run.id, kind="perf", phase="steady", status="completed")
    dropped = Request(id=212, run_id=run.id, kind="perf", phase="steady", status="failed")
    db.add_all([sent, dropped])
    db.flush()
    db.add_all([
        Attempt(id=211, request_id=211, attempt_index=1, latency_ms=1000),
        Attempt(id=212, request_id=212, attempt_index=1, error_category="schedule_dropped"),
    ])
    db.commit()

    summary = aggregate_run_metrics(db, run.id)["summary"]

    assert summary["success_rate"] == 1
    assert summary["error_rate"] == 0
    assert summary["error_categories"] == {}


def test_schedule_dropped_does_not_pollute_latency_statistics(db):
    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed")
    db.add(run)
    db.flush()
    sent = Request(
        id=213,
        run_id=run.id,
        kind="perf",
        phase="steady",
        status="completed",
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    dropped = Request(id=214, run_id=run.id, kind="perf", phase="steady", status="failed")
    db.add_all([sent, dropped])
    db.flush()
    db.add_all([
        Attempt(id=213, request_id=sent.id, attempt_index=1, latency_ms=120, itl_series={"values": [12]}),
        Attempt(id=214, request_id=dropped.id, attempt_index=1, latency_ms=0, itl_series={"values": [0]}, error_category="schedule_dropped"),
    ])
    db.commit()

    summary = aggregate_run_metrics(db, run.id)["summary"]

    assert summary["latency_ms"] == {
        "mean": 120.0,
        "min": 120.0,
        "max": 120.0,
        "p50": 120.0,
        "p90": 120.0,
        "p95": 120.0,
        "p99": 120.0,
    }
    assert summary["itl_ms"]["mean"] == 12.0


def test_schedule_dropped_does_not_extend_throughput_window(db):
    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed")
    db.add(run)
    db.flush()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    sent = Request(
        id=215,
        run_id=run.id,
        kind="perf",
        phase="steady",
        status="completed",
        started_at=start,
        finished_at=start + timedelta(seconds=1),
    )
    dropped = Request(
        id=216,
        run_id=run.id,
        kind="perf",
        phase="steady",
        status="failed",
        started_at=start + timedelta(seconds=100),
        finished_at=start + timedelta(seconds=100),
    )
    db.add_all([sent, dropped])
    db.flush()
    db.add_all([
        Attempt(id=215, request_id=sent.id, attempt_index=1, latency_ms=1000),
        Attempt(id=216, request_id=dropped.id, attempt_index=1, error_category="schedule_dropped"),
    ])
    db.commit()

    summary = aggregate_run_metrics(db, run.id)["summary"]

    assert summary["throughput_requests_per_second"] == 1


def test_aggregate_metrics_mixes_vendor_and_local_token_sources_without_dropping_rows(db):
    run = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="completed",
        run_snapshot={"price": {"input_token_price": "1", "output_token_price": "2"}},
    )
    db.add(run)
    db.flush()
    vendor_request = Request(
        id=111,
        run_id=run.id,
        kind="perf",
        phase="steady",
        status="completed",
        vendor_prompt_tokens=100,
        vendor_completion_tokens=50,
        vendor_total_tokens=150,
    )
    local_request = Request(
        id=112,
        run_id=run.id,
        kind="perf",
        phase="steady",
        status="completed",
        local_prompt_tokens=80,
        local_completion_tokens=40,
        local_total_tokens=120,
    )
    db.add_all([vendor_request, local_request])
    db.flush()
    db.add_all([
        Attempt(id=111, request_id=111, attempt_index=1, latency_ms=1000),
        Attempt(id=112, request_id=112, attempt_index=1, latency_ms=1000),
    ])
    db.commit()

    summary = aggregate_run_metrics(db, run.id)["summary"]

    assert summary["tokens"] == {"prompt": 180, "completion": 90, "total": 270, "source": "mixed"}
    assert summary["tokens_by_source"]["vendor"]["total"] == 150
    assert summary["tokens_by_source"]["local"]["total"] == 120
    assert summary["cost"]["amount"] == 0.36
    assert summary["cost"]["estimated"] is True


def test_aggregate_metrics_keeps_both_token_sources_on_one_request(db):
    run = Run(
        task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed",
        run_snapshot={"price": {"input_token_price": "1", "output_token_price": "2"}},
    )
    db.add(run)
    db.flush()
    request = Request(
        id=113, run_id=run.id, kind="perf", phase="steady", status="completed",
        vendor_prompt_tokens=100, vendor_completion_tokens=50, vendor_total_tokens=150,
        local_prompt_tokens=80, local_completion_tokens=40, local_total_tokens=120,
    )
    db.add(request)
    db.flush()
    db.add(Attempt(id=113, request_id=113, attempt_index=1, latency_ms=1000))
    db.commit()

    summary = aggregate_run_metrics(db, run.id)["summary"]

    assert summary["tokens"] == {"prompt": 100, "completion": 50, "total": 150, "source": "mixed"}
    assert summary["tokens_by_source"]["vendor"]["total"] == 150
    assert summary["tokens_by_source"]["local"]["total"] == 120
    assert summary["tokens_by_source"]["difference"]["total"] == 30
    assert summary["tokens_by_source"]["difference_rate"]["total"] == 0.2
    assert summary["throughput_tokens_per_second"] == 150
    assert summary["throughput_tokens_per_second_by_source"] == {"vendor": 150, "local": 120}
    assert summary["cost"]["amount"] == 0.2
    assert summary["cost"]["estimated"] is False


def test_cost_uses_snapshot_unit_currency_and_exchange_rate(db):
    run = Run(
        task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed",
        run_snapshot={
            "base_currency": "CNY",
            "price": {
                "input_token_price": "1", "output_token_price": "2",
                "unit": "per_1k_tokens", "currency": "USD", "exchange_rate": "7",
            },
        },
    )
    db.add(run)
    db.flush()
    request = Request(
        run_id=run.id, kind="perf", phase="steady", status="completed",
        vendor_prompt_tokens=100, vendor_completion_tokens=50, vendor_total_tokens=150,
    )
    request.id = 100
    db.add(request)
    db.flush()
    db.add(Attempt(id=100, request_id=request.id, attempt_index=1, latency_ms=1000))
    db.commit()

    cost = aggregate_run_metrics(db, run.id)["summary"]["cost"]
    assert cost["source_currency"] == "USD"
    assert cost["currency"] == "CNY"
    assert cost["source_amount"] == 0.2
    assert cost["amount"] == 1.4


def test_cost_uses_cached_token_price_for_vendor_cached_tokens(db):
    run = Run(
        task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed",
        run_snapshot={"price": {
            "input_token_price": "1", "output_token_price": "2",
            "cached_token_price": "0.1", "unit": "per_1k_tokens",
            "currency": "CNY", "exchange_rate": "1",
        }},
    )
    db.add(run)
    db.flush()
    request = Request(
        id=101, run_id=run.id, kind="perf", phase="steady", status="completed",
        vendor_prompt_tokens=100, vendor_cached_tokens=60,
        vendor_completion_tokens=50, vendor_total_tokens=150,
    )
    db.add(request)
    db.flush()
    db.add(Attempt(id=101, request_id=request.id, attempt_index=1, latency_ms=1000))
    db.commit()

    summary = aggregate_run_metrics(db, run.id)["summary"]

    assert summary["tokens_by_source"]["vendor"]["cached"] == 60
    # (40 * 1 + 60 * .1 + 50 * 2) / 1000
    assert summary["cost"]["amount"] == 0.146


def test_throughput_uses_run_wall_clock_for_overlapping_requests(db):
    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed")
    db.add(run)
    db.flush()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in (1, 2):
        request = Request(
            run_id=run.id,
            kind="perf",
            phase="steady",
            status="completed",
            started_at=start,
            finished_at=start + timedelta(seconds=1),
        )
        request.id = index
        db.add(request)
        db.flush()
        attempt = Attempt(request_id=request.id, attempt_index=1, latency_ms=1000)
        attempt.id = index
        db.add(attempt)
    db.commit()

    summary = aggregate_run_metrics(db, run.id)["summary"]

    assert summary["throughput_requests_per_second"] == 2


def test_throughput_excludes_schedule_dropped_requests(db):
    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed")
    db.add(run)
    db.flush()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    sent = Request(
        id=301,
        run_id=run.id,
        kind="perf",
        phase="steady",
        status="completed",
        started_at=start,
        finished_at=start + timedelta(seconds=1),
    )
    dropped = Request(
        id=302,
        run_id=run.id,
        kind="perf",
        phase="steady",
        status="failed",
    )
    db.add_all([sent, dropped])
    db.flush()
    db.add_all([
        Attempt(id=301, request_id=sent.id, attempt_index=1, latency_ms=1000),
        Attempt(id=302, request_id=dropped.id, attempt_index=1, error_category="schedule_dropped"),
    ])
    db.commit()

    summary = aggregate_run_metrics(db, run.id)["summary"]

    assert summary["request_count"] == 2
    assert summary["sent_request_count"] == 1
    assert summary["scheduled_dropped"] == 1
    assert summary["throughput_requests_per_second"] == 1


def test_aggregate_metrics_includes_async_code_execution_summary(db):
    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed")
    db.add(run)
    db.flush()
    db.add_all([
        CodeExecution(run_id=run.id, sample_index=0, code="pass", status="passed"),
        CodeExecution(run_id=run.id, sample_index=1, code="pass", status="failed"),
        CodeExecution(run_id=run.id, sample_index=2, code="pass", status="pending"),
    ])
    db.commit()

    code = aggregate_run_metrics(db, run.id)["summary"]["code_execution"]

    assert code["total"] == 3
    assert code["passed"] == 1
    assert code["failed"] == 1
    assert code["pending"] == 1
    assert code["pass_rate"] == 0.5
