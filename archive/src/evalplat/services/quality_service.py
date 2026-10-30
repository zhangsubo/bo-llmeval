"""客观质量评测器。

评分器只处理确定性规则，不执行用户上传脚本，也不调用 LLM。
"""

import re
import uuid
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evalplat.core.errors import NotFoundError
from evalplat.models.attempt import Attempt
from evalplat.models.request import Request
from evalplat.models.run import Run
from evalplat.constants import RunStatus
from evalplat.core.errors import ConflictError

SUPPORTED_METHODS = {"exact_match", "normalized_exact_match", "regex", "multiple_choice", "numeric_tolerance"}
TRANSIENT_ERROR_CATEGORIES = {"timeout", "dns_connection", "temporary_network"}


def should_retry_quality(
    *,
    http_status: int | None = None,
    error_category: str | None = None,
    attempt_index: int,
    max_retries: int = 2,
) -> bool:
    """判断质量请求是否允许再尝试一次。

    仅允许临时网络错误、超时、429 和 5xx；400/401/403/404/422 等配置错误不重试。
    ``attempt_index`` 从 1 开始，max_retries 表示额外尝试次数。
    """
    if max_retries < 0 or attempt_index < 1:
        return False
    if attempt_index > max_retries:
        return False
    transient = error_category in TRANSIENT_ERROR_CATEGORIES
    status_transient = http_status == 429 or (http_status is not None and 500 <= http_status <= 599)
    return transient or status_transient


def _output(attempt: Attempt | None) -> str:
    if not attempt:
        return ""
    fields = attempt.extracted_fields or {}
    return str(fields.get("content") or fields.get("text") or "")


def _expected(request: Request) -> str:
    payload = request.input_messages or {}
    for key in ("expected", "reference_answer", "answer"):
        value = payload.get(key)
        if value is not None:
            return str(value)
    return ""


def _normalize(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _score(method: str, expected: str, actual: str, tolerance: Decimal) -> bool:
    if method == "exact_match":
        return actual == expected
    if method == "normalized_exact_match":
        return _normalize(actual) == _normalize(expected)
    if method == "regex":
        return re.search(expected, actual) is not None
    if method == "multiple_choice":
        expected_match = re.search(r"\b([A-Z])\b", expected.upper())
        actual_match = re.search(r"\b([A-Z])\b", actual.upper())
        expected_choice = expected_match.group(1) if expected_match else _normalize(expected)[:1].upper()
        actual_choice = actual_match.group(1) if actual_match else _normalize(actual)[:1].upper()
        return bool(expected_choice) and actual_choice == expected_choice
    if method == "numeric_tolerance":
        try:
            return abs(Decimal(actual.strip()) - Decimal(expected.strip())) <= tolerance
        except (InvalidOperation, ValueError):
            return False
    raise ValueError(f"不支持的评分方法: {method}")


def score_quality_sample(
    scoring_method: str,
    expected: object,
    actual: object,
    tolerance: float = 1e-6,
) -> dict[str, object]:
    """Score one answer without touching the database.

    The execution path and the manual evaluation API share this pure scoring
    boundary, so a normal quality Run cannot finish without a deterministic
    evidence record.
    """

    if scoring_method not in SUPPORTED_METHODS:
        raise ValueError(f"不支持的评分方法: {scoring_method}")
    if tolerance < 0:
        raise ValueError("数值容差不能为负数")
    expected_text = "" if expected is None else str(expected)
    actual_text = "" if actual is None else str(actual)
    correct = _score(
        scoring_method,
        expected_text,
        actual_text,
        Decimal(str(tolerance)),
    )
    return {
        "scoring_method": scoring_method,
        "expected": expected_text,
        "actual": actual_text,
        "correct": correct,
        "score": 1.0 if correct else 0.0,
    }


def evaluate_quality(db: Session, run_id: uuid.UUID, scoring_method: str, tolerance: float = 1e-6) -> dict:
    """对 Run 下已完成的质量请求进行确定性评分。"""
    run = db.get(Run, run_id)
    if not run:
        raise NotFoundError("Run 不存在")
    if scoring_method not in SUPPORTED_METHODS:
        raise ValueError(f"不支持的评分方法: {scoring_method}")
    if tolerance < 0:
        raise ValueError("数值容差不能为负数")
    if run.status not in RunStatus.TERMINAL:
        raise ConflictError("仅终态 Run 可执行质量评分")
    # EvalScope performance Runs can contain request evidence, but that
    # evidence is not a quality sample and must never be scored here. Legacy
    # Runs without a snapshot remain compatible with the native quality path.
    if (run.run_snapshot or {}).get("evaluation_kind") == "performance":
        raise ConflictError("仅 quality Run 可执行质量评分")
    tolerance_decimal = Decimal(str(tolerance))
    requests = list(db.scalars(select(Request).where(Request.run_id == run_id).order_by(Request.id)))
    evaluated_at = datetime.now(timezone.utc).isoformat()
    details = []
    latest_attempts: dict[int, Attempt] = {}
    if requests:
        request_ids = [request.id for request in requests]
        ranked_attempts = (
            select(
                Attempt.id.label("attempt_id"),
                func.row_number()
                .over(
                    partition_by=Attempt.request_id,
                    order_by=(Attempt.attempt_index.desc(), Attempt.id.desc()),
                )
                .label("attempt_rank"),
            )
            .where(Attempt.request_id.in_(request_ids))
            .subquery()
        )
        # One query replaces the former per-request lookup while retaining the
        # same tie-breaker (highest attempt_index, then newest row ID).
        for attempt in db.scalars(
            select(Attempt)
            .join(ranked_attempts, Attempt.id == ranked_attempts.c.attempt_id)
            .where(ranked_attempts.c.attempt_rank == 1)
        ):
            latest_attempts[attempt.request_id] = attempt

    for request in requests:
        latest_attempt = latest_attempts.get(request.id)
        expected = _expected(request)
        actual = _output(latest_attempt)
        detail = score_quality_sample(scoring_method, expected, actual, float(tolerance_decimal))
        detail.update({
            "request_id": request.id,
            "status": request.status,
        })
        details.append(detail)
        request.quality_result = {
            "engine": "native",
            "mapping_status": "mapped",
            "scoring_method": scoring_method,
            "evaluated_at": evaluated_at,
            "expected": expected,
            "actual": actual,
            "correct": detail["correct"],
            "score": detail["score"],
        }
    correct = sum(item["correct"] for item in details)
    result = {
        "run_id": str(run_id),
        "scoring_method": scoring_method,
        "status": "completed",
        "sample_count": len(details),
        "correct_count": correct,
        "accuracy": correct / len(details) if details else None,
        "details": details,
        "evaluated_at": evaluated_at,
    }
    db.commit()
    return result
