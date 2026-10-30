"""质量样本执行器：每个样本一条 Request，重试追加 Attempt。"""

from __future__ import annotations

import uuid
import logging
from collections.abc import Callable
from datetime import datetime, timezone
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evalplat.core.crypto import CryptoService
from evalplat.core.ssrf import SSRFGuard
from evalplat.models.attempt import Attempt
from evalplat.models.connection import Connection
from evalplat.models.request import Request
from evalplat.models.run import Run
from evalplat.models.tokenizer import Tokenizer
from evalplat.services.evaluation_engine import (
    EvaluationEngineConfigurationError,
    extract_evalscope_sample_results,
    run_evalscope,
)
from evalplat.services.quality_service import score_quality_sample, should_retry_quality
from evalplat.services.run_budget import RunBudget
from evalplat.services.security_service import (
    redact_sensitive_text,
    redact_sensitive_value,
)
from evalplat.services.run_executor import execute_chat_request
from evalplat.services.tokenizer_service import count_tokens
from evalplat.services.token_usage import normalize_usage_tokens


logger = logging.getLogger(__name__)


def _sqlite_id(db: Session, model: Any) -> int:
    """Allocate deterministic IDs for the SQLite test harness."""

    return int(db.scalar(select(func.max(model.id))) or 0) + 1


def _quality_secret(db: Session, run: Run, crypto: CryptoService) -> str | None:
    """Best-effort lookup of the current connection secret for evidence redaction."""

    connection_id = (run.run_snapshot or {}).get("connection_id")
    if not connection_id:
        return None
    try:
        connection = db.get(Connection, uuid.UUID(str(connection_id)))
    except (TypeError, ValueError):
        return None
    if connection is None or not connection.api_key_ciphertext:
        return None
    try:
        return crypto.decrypt(connection.api_key_ciphertext)
    except Exception:
        # A malformed/rotated credential must not prevent the request from
        # reaching the normal execution error path; regex redaction remains.
        return None


def _estimate_quality_sample_tokens(
    db: Session,
    snapshot: dict[str, Any],
    sample: dict[str, Any],
) -> int | None:
    """Conservatively estimate one provider attempt for a quality sample.

    A hard total-token cap is only meaningful when both the prompt and the
    requested output have a deterministic upper bound. Returning ``None``
    keeps ``RunBudget`` fail-closed for legacy or incomplete snapshots.
    """

    tokenizer_id = snapshot.get("tokenizer_id")
    if not tokenizer_id:
        return None
    try:
        tokenizer = db.get(Tokenizer, uuid.UUID(str(tokenizer_id)))
    except (TypeError, ValueError):
        return None
    if tokenizer is None or tokenizer.status != "ready":
        return None
    messages = sample.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    prompt_tokens = count_tokens(tokenizer, messages)
    if prompt_tokens is None:
        return None
    snapshot_params = snapshot.get("generation_params")
    params = dict(snapshot_params) if isinstance(snapshot_params, dict) else {}
    sample_params = sample.get("generation_params")
    if isinstance(sample_params, dict):
        params.update(sample_params)
    raw_output = params.get("max_tokens", params.get("max_completion_tokens"))
    if raw_output is None:
        raw_output = snapshot.get("max_output_tokens")
    try:
        output_tokens = int(raw_output)
    except (TypeError, ValueError):
        return None
    if output_tokens < 0:
        return None
    return max(0, int(prompt_tokens)) + output_tokens


def _persist_quality_failure(
    db: Session,
    request: Request,
    result: dict[str, Any] | None,
    error: Exception,
    *,
    should_cancel: Callable[[], bool] | None,
    error_category: str,
) -> dict[str, Any]:
    """Converge an executor/scorer exception into durable request evidence.

    The HTTP boundary normally catches provider failures itself. This helper
    covers failures before that boundary (bad snapshots, connection lookup,
    scoring errors, or injected executor bugs) so a pre-created Request can
    never remain ``pending`` without an Attempt.
    """

    try:
        cancelled = bool(should_cancel and should_cancel())
    except Exception:
        cancelled = False
    if not db.is_active:
        try:
            db.rollback()
        except Exception:
            pass
        # PostgreSQL identity columns are GENERATED ALWAYS; a rolled-back
        # preallocated ID must not be reused on the replacement INSERT.
        if db.bind is not None and db.bind.dialect.name != "sqlite":
            request.id = None
    db.add(request)
    now = datetime.now(timezone.utc)
    request.status = "cancelled" if cancelled else "failed"
    request.started_at = request.started_at or now
    request.finished_at = now
    db.flush()

    attempt: Attempt | None = None
    raw_attempt_id = (result or {}).get("attempt_id") if isinstance(result, dict) else None
    try:
        attempt = db.get(Attempt, int(raw_attempt_id)) if raw_attempt_id is not None else None
        if attempt is not None and attempt.request_id != request.id:
            # Never mutate evidence belonging to another Request when an
            # adapter returns a stale or malformed attempt_id.
            attempt = None
    except (TypeError, ValueError):
        attempt = None
    if attempt is None:
        latest_index = db.scalar(
            select(func.max(Attempt.attempt_index)).where(Attempt.request_id == request.id)
        ) or 0
        attempt = Attempt(
            request_id=request.id,
            attempt_index=int(latest_index) + 1,
            started_at=request.started_at,
            finished_at=now,
        )
        if db.bind is not None and db.bind.dialect.name == "sqlite":
            attempt.id = _sqlite_id(db, Attempt)
        db.add(attempt)
    attempt.finished_at = now
    attempt.error_category = "cancelled" if cancelled else error_category
    attempt.error_body = redact_sensitive_text(str(error)[:500]) or None
    db.commit()
    return {
        **(result or {}),
        "status": request.status,
        "request_id": request.id,
        "attempt_id": attempt.id,
        "error_category": attempt.error_category,
        "error": redact_sensitive_text(str(error)[:500]),
    }


def execute_quality_sample(
    db: Session,
    run: Run,
    sample: dict[str, Any],
    crypto: CryptoService,
    ssrf_guard: SSRFGuard,
    *,
    max_retries: int = 2,
    stream: bool = False,
    should_cancel: Callable[[], bool] | None = None,
    budget: RunBudget | None = None,
    executor: Callable[..., dict[str, Any]] = execute_chat_request,
) -> dict[str, Any]:
    """执行一个质量样本，并在同一 Request 下追加有限重试 Attempt。"""

    if max_retries < 0:
        raise ValueError("max_retries 不能为负数")
    messages = sample.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("质量样本缺少非空 messages")
    expected = sample.get("expected", sample.get("answer", sample.get("reference_answer", "")))
    # The HTTP executor reapplies the exact connection secret at its boundary.
    # Keep this pre-created evidence redacted as well for injected executors or
    # failures that happen before the HTTP call can run.
    safe_input_messages = redact_sensitive_value(
        {**sample, "messages": messages, "expected": expected},
        _quality_secret(db, run, crypto),
    )
    started_request = Request(
        run_id=run.id,
        sample_index=sample.get("sample_index"),
        kind="quality",
        phase="steady",
        stream_mode="stream" if stream else "nonstream",
        input_messages=safe_input_messages,
        status="pending",
    )
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        started_request.id = (db.scalar(select(func.max(Request.id))) or 0) + 1
    db.add(started_request)
    db.flush()

    result: dict[str, Any] = {"status": "failed", "request_id": started_request.id}
    for attempt_index in range(1, max_retries + 2):
        if should_cancel and should_cancel():
            return _persist_quality_failure(
                db,
                started_request,
                result,
                RuntimeError("任务已取消"),
                should_cancel=should_cancel,
                error_category="cancelled",
            )
        if budget is not None:
            estimated_tokens = _estimate_quality_sample_tokens(
                db, run.run_snapshot or {}, sample
            )
            allowed, reason = budget.reserve(estimated_tokens)
            if not allowed:
                return _persist_quality_failure(
                    db,
                    started_request,
                    result,
                    RuntimeError(reason or "Run runtime budget exceeded"),
                    should_cancel=should_cancel,
                    error_category="run_budget",
                )
        try:
            result = executor(
                db,
                run,
                messages,
                crypto,
                ssrf_guard,
                stream=stream,
                kind="quality",
                phase="steady",
                sample_index=sample.get("sample_index"),
                request=started_request,
                attempt_index=attempt_index,
                input_messages=safe_input_messages,
            )
        except Exception as exc:
            return _persist_quality_failure(
                db,
                started_request,
                result,
                exc,
                should_cancel=should_cancel,
                error_category="quality_executor_error",
            )
        if result.get("status") == "completed":
            response = result.get("response")
            actual = ""
            if isinstance(response, dict):
                choices = response.get("choices")
                if isinstance(choices, list) and choices:
                    message = choices[0].get("message") if isinstance(choices[0], dict) else None
                    if isinstance(message, dict):
                        actual = message.get("content") or ""
            scoring_method = str(sample.get("scoring_method") or "exact_match")
            try:
                score = score_quality_sample(
                    scoring_method,
                    expected,
                    actual,
                    float(sample.get("tolerance", 1e-6) or 0),
                )
            except Exception as exc:
                return _persist_quality_failure(
                    db,
                    started_request,
                    result,
                    exc,
                    should_cancel=should_cancel,
                    error_category="scoring_error",
                )
            started_request.quality_result = {
                "engine": "native",
                "mapping_status": "mapped",
                **score,
            }
            result["quality_result"] = dict(started_request.quality_result)
            db.commit()
            break
        if not should_retry_quality(
            http_status=result.get("http_status"),
            error_category=result.get("error_category"),
            attempt_index=attempt_index,
            max_retries=max_retries,
        ):
            break
    # The built-in HTTP executor persists its own Request/Attempt rows.  An
    # adapter may only return a failure payload, however; close the pre-created
    # Request and create durable failure evidence instead of leaving it pending.
    if result.get("status") in {"failed", "cancelled"}:
        attempt_count = db.scalar(
            select(func.count()).select_from(Attempt).where(Attempt.request_id == started_request.id)
        ) or 0
        if attempt_count == 0:
            failure = RuntimeError(result.get("error") or "质量样本执行失败")
            return _persist_quality_failure(
                db,
                started_request,
                result,
                failure,
                should_cancel=should_cancel,
                error_category=(
                    "cancelled"
                    if result.get("status") == "cancelled"
                    else str(result.get("error_category") or "quality_executor_error")
                ),
            )
        if started_request.status in {"pending", "running"}:
            # An adapter may have created an Attempt but forgotten to close the
            # parent Request. Preserve that Attempt and only converge the
            # missing Request lifecycle fields; do not create a duplicate row.
            started_request.status = "cancelled" if result.get("status") == "cancelled" else "failed"
            started_request.started_at = started_request.started_at or datetime.now(timezone.utc)
            started_request.finished_at = datetime.now(timezone.utc)
            db.commit()
    return result


def execute_quality_samples(
    db: Session,
    run: Run,
    samples: list[dict[str, Any]],
    crypto: CryptoService,
    ssrf_guard: SSRFGuard,
    should_cancel: Callable[[], bool] | None = None,
    budget: RunBudget | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """按数据集顺序执行质量样本，返回每条样本的最后一次结果。"""

    results: list[dict[str, Any]] = []
    for index, sample in enumerate(samples):
        if should_cancel and should_cancel():
            break
        results.append(
            execute_quality_sample(
                db,
                run,
                {**sample, "sample_index": index},
                crypto,
                ssrf_guard,
                should_cancel=should_cancel,
                budget=budget,
                **kwargs,
            )
        )
        if results[-1].get("status") == "cancelled":
            break
    return results


def execute_evalscope_run(
    db: Session,
    run: Run,
    crypto: CryptoService,
    ssrf_guard: SSRFGuard,
    *,
    work_dir: str,
    engine: Any | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """通过 EvalScope 执行 benchmark，并按 sample 落库质量证据。"""

    snapshot = run.run_snapshot or {}
    connection_id = snapshot.get("connection_id")
    if not connection_id:
        raise ValueError("Run 快照缺少 connection_id")
    api_key = None
    try:
        connection = db.get(Connection, uuid.UUID(str(connection_id)))
        if not connection or connection.status != "active":
            raise ValueError("连接不存在或未启用")
        # 地址属于不可变 Run 快照；当前连接只提供凭据和启用状态。
        base_url = str(snapshot.get("base_url") or connection.base_url).strip().rstrip("/")
        target = None
        if hasattr(ssrf_guard, "resolve_url"):
            allowed, reason, target = ssrf_guard.resolve_url(base_url)
        else:
            allowed, reason = ssrf_guard.check_url(base_url)
        if not allowed:
            raise ValueError(f"SSRF 校验失败: {reason}")
        if not connection.api_key_ciphertext:
            raise ValueError("连接未配置 API Key")
        api_key = crypto.decrypt(connection.api_key_ciphertext)
        result = run_evalscope(
            {**snapshot, "base_url": base_url},
            api_key=api_key,
            work_dir=work_dir,
            # Leave the engine unset for production runs so the evaluation
            # boundary can place real EvalScope in a killable child process.
            engine=engine,
            ssrf_target=target,
            verify=not bool(snapshot.get("tls_skip_verify", connection.tls_skip_verify)),
            should_cancel=should_cancel,
        )
    except (EvaluationEngineConfigurationError, ValueError, TypeError):
        logger.exception("EvalScope quality configuration failed", extra={"run_id": str(run.id)})
        result = {"status": "failed", "engine": "evalscope", "error": "EvalScope 配置或输入无效"}
    except Exception:
        # SDK/transport exceptions are converted to evidence below so the
        # worker never leaves the Run in a non-terminal state.
        logger.exception("EvalScope quality execution failed", extra={"run_id": str(run.id)})
        result = {"status": "failed", "engine": "evalscope", "error": "EvalScope 执行失败"}

    safe_result = redact_sensitive_value(result, api_key)
    now = datetime.now(timezone.utc)
    # Some engines return a partial ``result`` together with ``cancelled`` or
    # ``failed``. Persist those rows as evidence instead of replacing them
    # with one synthetic failure marker.
    if safe_result.get("status") in {"completed", "cancelled", "failed"}:
        aggregate = safe_result.get("result")
        sample_results = extract_evalscope_sample_results(aggregate)
        if sample_results:
            request_ids: list[int] = []
            attempt_ids: list[int] = []
            safe_aggregate = redact_sensitive_value(aggregate, api_key)
            for position, sample_result in enumerate(sample_results):
                sample_index = sample_result.get("sample_index", position)
                try:
                    sample_index = int(sample_index)
                except (TypeError, ValueError):
                    sample_index = position
                raw_status = str(sample_result.get("status") or "").lower()
                sample_status = (
                    "cancelled"
                    if raw_status == "cancelled"
                    else "failed"
                    if raw_status in {"failed", "error"} or sample_result.get("success") is False
                    else "completed"
                )
                sample_input = sample_result.get("messages") or sample_result.get("request")
                if not isinstance(sample_input, (dict, list)):
                    sample_input = {
                        "engine": "evalscope",
                        "datasets": snapshot.get("evalscope_datasets"),
                        "sample_index": sample_index,
                    }
                request = Request(
                    run_id=run.id,
                    sample_index=sample_index,
                    kind="quality",
                    phase="steady",
                    stream_mode="stream" if snapshot.get("stream") else "nonstream",
                    input_messages=redact_sensitive_value(sample_input, api_key),
                    status=sample_status,
                    scheduled_at=now,
                    started_at=now,
                    finished_at=now,
                    quality_result={
                        "engine": "evalscope",
                        "mapping_status": "mapped",
                        "sample_count": 1,
                        "sample": redact_sensitive_value(sample_result, api_key),
                        # Keep aggregate evidence on the first sample only to
                        # avoid duplicating potentially large benchmark output.
                        "aggregate": safe_aggregate if position == 0 else None,
                    },
                )
                if db.bind is not None and db.bind.dialect.name == "sqlite":
                    request.id = int(db.scalar(select(func.max(Request.id))) or 0) + 1
                db.add(request)
                db.flush()
                usage = sample_result.get("usage") or sample_result.get("vendor_usage") or {}
                if not isinstance(usage, dict):
                    usage = {}
                normalized_usage = normalize_usage_tokens(usage)
                request.vendor_prompt_tokens = normalized_usage["prompt_tokens"]
                request.vendor_cached_tokens = normalized_usage["cached_tokens"]
                request.vendor_completion_tokens = normalized_usage["completion_tokens"]
                request.vendor_total_tokens = normalized_usage["total_tokens"]
                attempt = Attempt(
                    request_id=request.id,
                    attempt_index=1,
                    started_at=now,
                    finished_at=now,
                    vendor_usage=redact_sensitive_value(usage, api_key),
                    extracted_fields={"evalscope": redact_sensitive_value(sample_result, api_key)},
                    raw_response=json.dumps(
                        redact_sensitive_value(sample_result, api_key),
                        ensure_ascii=False,
                        default=str,
                    )[:1_000_000],
                )
                if sample_status != "completed":
                    attempt.error_category = sample_result.get("error_category") or "engine_sample_failed"
                    attempt.error_body = redact_sensitive_text(
                        str(sample_result.get("error") or "EvalScope sample failed")[:500],
                        api_key,
                    )
                if db.bind is not None and db.bind.dialect.name == "sqlite":
                    attempt.id = int(db.scalar(select(func.max(Attempt.id))) or 0) + 1
                db.add(attempt)
                request_ids.append(request.id)
                attempt_ids.append(attempt.id)
            db.commit()
            completed = sum(
                item.get("status") not in {"failed", "error", "cancelled"}
                and item.get("success") is not False
                for item in sample_results
            )
            overall_status = (
                "cancelled"
                if safe_result.get("status") == "cancelled"
                else "completed"
                if completed == len(sample_results)
                else "partially_completed"
                if completed
                else "failed"
            )
            return {
                **safe_result,
                "status": overall_status,
                "request_id": request_ids[0],
                "attempt_id": attempt_ids[0],
                "request_ids": request_ids,
                "attempt_ids": attempt_ids,
                "sample_count": len(sample_results),
                "completed": completed,
            }
        # An aggregate score without sample-level records cannot be reconciled
        # with the platform's Request/sample evidence model.
        error = "EvalScope 返回结果缺少逐样本记录，无法建立可追溯质量证据"
        status = "failed"
        category = "result_unmapped"
        quality_result = {
            "engine": "evalscope",
            "mapping_status": "unmapped",
            "sample_count": 0,
            "aggregate": redact_sensitive_value(aggregate, api_key),
        }
    else:
        status = "cancelled" if safe_result.get("status") == "cancelled" else "failed"
        category = "cancelled" if status == "cancelled" else (
            "engine_unavailable" if safe_result.get("status") == "unavailable" else "engine_error"
        )
        error = str(safe_result.get("error", "EvalScope unavailable"))[:500]
        quality_result = {
            "engine": "evalscope",
            "mapping_status": "unavailable" if safe_result.get("status") == "unavailable" else "failed",
            "sample_count": 0,
            "aggregate": redact_sensitive_value(safe_result, api_key),
        }

    request = Request(
        run_id=run.id,
        kind="quality",
        phase="steady",
        stream_mode="stream" if snapshot.get("stream") else "nonstream",
        input_messages={"engine": "evalscope", "datasets": snapshot.get("evalscope_datasets")},
        status=status,
        scheduled_at=now,
        started_at=now,
        finished_at=now,
        quality_result=quality_result,
    )
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        request.id = int(db.scalar(select(func.max(Request.id))) or 0) + 1
    db.add(request)
    db.flush()
    attempt = Attempt(
        request_id=request.id,
        attempt_index=1,
        started_at=now,
        finished_at=now,
        error_category=category,
        error_body=redact_sensitive_text(error, api_key),
        extracted_fields={"evalscope": quality_result.get("aggregate")},
        raw_response=json.dumps(quality_result.get("aggregate"), ensure_ascii=False, default=str)[:1_000_000],
    )
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        attempt.id = int(db.scalar(select(func.max(Attempt.id))) or 0) + 1
    db.add(attempt)
    db.commit()
    return {
        **safe_result,
        "status": status,
        "request_id": request.id,
        "attempt_id": attempt.id,
        "sample_count": 0,
    }
