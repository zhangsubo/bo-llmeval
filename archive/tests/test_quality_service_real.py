"""确定性质量评分测试。"""

import uuid
import pytest

from evalplat.models.attempt import Attempt
from evalplat.models.request import Request
from evalplat.models.run import Run
from evalplat.core.errors import ConflictError
from evalplat.services.quality_service import evaluate_quality, should_retry_quality


def test_normalized_exact_match_scores_requests(db):
    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed")
    db.add(run)
    db.flush()
    request = Request(run_id=run.id, kind="quality", status="completed", input_messages={"expected": "Paris"})
    request.id = 1
    db.add(request)
    db.flush()
    attempt = Attempt(request_id=request.id, attempt_index=1, extracted_fields={"content": "  paris\n"})
    attempt.id = 1
    db.add(attempt)
    db.commit()

    result = evaluate_quality(db, run.id, "normalized_exact_match")

    assert result["status"] == "completed"
    assert result["sample_count"] == 1
    assert result["accuracy"] == 1
    assert request.quality_result["score"] == 1.0
    assert request.quality_result["engine"] == "native"


def test_regex_and_invalid_method(db):
    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed")
    db.add(run)
    db.commit()

    with pytest.raises(ValueError):
        evaluate_quality(db, run.id, "llm_judge")


def test_quality_evaluation_rejects_performance_or_active_run(db):
    performance_run = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="completed",
        run_snapshot={"evaluation_kind": "performance"},
    )
    active_run = Run(
        task_id=uuid.uuid4(),
        model_deployment_id=uuid.uuid4(),
        status="running",
    )
    db.add_all([performance_run, active_run])
    db.commit()

    with pytest.raises(ConflictError, match="quality Run"):
        evaluate_quality(db, performance_run.id, "exact_match")
    with pytest.raises(ConflictError, match="终态 Run"):
        evaluate_quality(db, active_run.id, "exact_match")


def test_numeric_tolerance(db):
    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed")
    db.add(run)
    db.flush()
    request = Request(run_id=run.id, kind="quality", status="completed", input_messages={"expected": "3.14159"})
    request.id = 1
    db.add(request)
    db.flush()
    attempt = Attempt(request_id=request.id, attempt_index=1, extracted_fields={"content": "3.14"})
    attempt.id = 1
    db.add(attempt)
    db.commit()

    result = evaluate_quality(db, run.id, "numeric_tolerance", tolerance=0.002)

    assert result["accuracy"] == 1


def test_numeric_zero_expected_is_not_treated_as_missing(db):
    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed")
    db.add(run)
    db.flush()
    request = Request(run_id=run.id, kind="quality", status="completed", input_messages={"expected": 0})
    request.id = 2
    db.add(request)
    db.flush()
    attempt = Attempt(request_id=request.id, attempt_index=1, extracted_fields={"content": "0"})
    attempt.id = 2
    db.add(attempt)
    db.commit()

    result = evaluate_quality(db, run.id, "numeric_tolerance")

    assert result["accuracy"] == 1


def test_quality_evaluation_uses_latest_attempt(db):
    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed")
    db.add(run)
    db.flush()
    request = Request(
        run_id=run.id,
        kind="quality",
        status="completed",
        input_messages={"expected": "latest"},
        id=3,
    )
    db.add(request)
    db.flush()
    db.add_all([
        Attempt(id=30, request_id=request.id, attempt_index=1, extracted_fields={"content": "stale"}),
        Attempt(id=31, request_id=request.id, attempt_index=2, extracted_fields={"content": "latest"}),
    ])
    db.commit()

    result = evaluate_quality(db, run.id, "exact_match")

    assert result["accuracy"] == 1
    assert result["details"][0]["actual"] == "latest"


def test_quality_api_redacts_credentials(client, db):
    secret = "sk-quality-secret-12345678"
    run = Run(task_id=uuid.uuid4(), model_deployment_id=uuid.uuid4(), status="completed")
    db.add(run)
    db.flush()
    request = Request(
        run_id=run.id,
        kind="quality",
        status="completed",
        input_messages={"expected": secret},
        id=4,
    )
    db.add(request)
    db.flush()
    db.add(Attempt(id=40, request_id=request.id, attempt_index=1, extracted_fields={"content": secret}))
    db.commit()

    response = client.post(
        f"/api/v1/runs/{run.id}/evaluate",
        json={"scoring_method": "exact_match"},
    )

    assert response.status_code == 200, response.text
    assert secret not in response.text
    assert response.json()["details"][0]["actual"] == "sk-q****5678"


def test_quality_retry_policy_only_allows_transient_failures():
    assert should_retry_quality(http_status=429, attempt_index=1)
    assert should_retry_quality(http_status=503, attempt_index=2)
    assert should_retry_quality(error_category="timeout", attempt_index=1)
    assert not should_retry_quality(http_status=400, attempt_index=1)
    assert not should_retry_quality(http_status=503, attempt_index=4, max_retries=2)
