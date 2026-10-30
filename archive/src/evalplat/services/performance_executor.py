"""EvalScope 性能执行与逐请求证据落库。"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from collections.abc import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evalplat.config import get_settings
from evalplat.core.crypto import CryptoService
from evalplat.core.ssrf import SSRFGuard
from evalplat.models.attempt import Attempt
from evalplat.models.connection import Connection
from evalplat.models.request import Request
from evalplat.models.run import Run
from evalplat.models.tokenizer import Tokenizer
from evalplat.services.evaluation_engine import (
    EvaluationEngineConfigurationError,
    run_evalscope_performance,
)
from evalplat.services.security_service import redact_sensitive_text, redact_sensitive_value
from evalplat.services.tokenizer_service import count_tokens
from evalplat.services.token_usage import normalize_usage_tokens


def _new_id(db: Session, model: Any) -> int:
    """Allocate deterministic IDs for the SQLite test harness."""

    return int(db.scalar(select(func.max(model.id))) or 0) + 1


def _run_tokenizer(db: Session, run: Run) -> Tokenizer | None:
    snapshot = run.run_snapshot or {}
    raw_id = snapshot.get("tokenizer_id")
    if not raw_id:
        raw_id = (snapshot.get("tokenizer_snapshot") or {}).get("id")
    if not raw_id:
        return None
    try:
        tokenizer = db.get(Tokenizer, uuid.UUID(str(raw_id)))
    except (TypeError, ValueError):
        return None
    return tokenizer if tokenizer is not None and tokenizer.status == "ready" else None


def _response_content(value: Any) -> str:
    """Extract assistant text from EvalScope's normalized response evidence."""

    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_response_content(item) for item in value)
    if isinstance(value, dict):
        if isinstance(value.get("choices"), list):
            return _response_content(value["choices"])
        message = value.get("message")
        if isinstance(message, dict):
            return _response_content(message.get("content"))
        return _response_content(value.get("content"))
    return ""


def _record_evidence(
    db: Session,
    run: Run,
    record: dict[str, Any],
    *,
    now: datetime,
    secret: str | None,
) -> tuple[Request, Attempt]:
    latency_ms = record.get("latency_ms")
    try:
        latency = max(0.0, float(latency_ms)) if latency_ms is not None else 0.0
    except (TypeError, ValueError):
        latency = 0.0
    finished_at = now
    started_at = finished_at - timedelta(milliseconds=latency)
    body = record.get("request_body")
    input_messages = body if isinstance(body, dict) else {"messages": body}
    phase = str(record.get("phase") or "steady")
    if phase not in {"cold_start", "warmup", "steady"}:
        phase = "steady"
    status = str(record.get("status") or ("completed" if record.get("success") else "failed"))
    if status not in {"completed", "failed", "cancelled"}:
        status = "failed"
    request = Request(
        run_id=run.id,
        sample_index=record.get("sample_index"),
        kind="perf",
        phase=phase,
        stream_mode="stream" if record.get("stream_mode") == "stream" else "nonstream",
        input_messages=redact_sensitive_value(input_messages, secret),
        status=status,
        scheduled_at=started_at,
        started_at=started_at,
        finished_at=finished_at,
    )
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        request.id = _new_id(db, Request)
    usage = record.get("vendor_usage") if isinstance(record.get("vendor_usage"), dict) else {}
    normalized_usage = normalize_usage_tokens(usage)
    request.vendor_prompt_tokens = normalized_usage["prompt_tokens"]
    request.vendor_cached_tokens = normalized_usage["cached_tokens"]
    request.vendor_completion_tokens = normalized_usage["completion_tokens"]
    request.vendor_total_tokens = normalized_usage["total_tokens"]
    tokenizer = _run_tokenizer(db, run)
    if tokenizer is not None:
        messages = body.get("messages") if isinstance(body, dict) else body
        request.local_prompt_tokens = count_tokens(tokenizer, messages)
        response_messages = (record.get("extracted_fields") or {}).get("response_messages")
        completion_text = _response_content(response_messages)
        request.local_completion_tokens = count_tokens(tokenizer, completion_text)
        if request.local_prompt_tokens is not None and request.local_completion_tokens is not None:
            request.local_total_tokens = request.local_prompt_tokens + request.local_completion_tokens
    db.add(request)
    db.flush()

    raw_itl = record.get("itl_series") if isinstance(record.get("itl_series"), dict) else {}
    raw_milliseconds = raw_itl.get("milliseconds") if isinstance(raw_itl, dict) else None
    if not isinstance(raw_milliseconds, list):
        raw_milliseconds = []
    attempt = Attempt(
        request_id=request.id,
        attempt_index=1,
        started_at=started_at,
        finished_at=finished_at,
        latency_ms=latency_ms,
        ttft_ms=record.get("ttft_ms"),
        tpot_ms=record.get("tpot_ms"),
        itl_series={"values": raw_milliseconds},
        avg_speed=record.get("avg_speed"),
        peak_speed=record.get("peak_speed"),
        peak_tps=record.get("peak_tps"),
        vendor_usage=redact_sensitive_value(usage, secret),
        raw_response=redact_sensitive_text(str(record.get("raw_response") or "")[:1_000_000], secret),
        extracted_fields=redact_sensitive_value(record.get("extracted_fields") or {}, secret),
        error_category=record.get("error_category"),
        error_body=redact_sensitive_text(str(record.get("error") or "")[:500], secret) or None,
        http_status=record.get("http_status"),
        vendor_request_id=redact_sensitive_text(record.get("vendor_request_id"), secret) or None,
    )
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        attempt.id = _new_id(db, Attempt)
    db.add(attempt)
    return request, attempt


def _record_engine_failure(
    db: Session,
    run: Run,
    error: str,
    *,
    category: str = "engine_error",
    status: str = "failed",
    secret: str | None = None,
) -> tuple[Request, Attempt]:
    """Persist a failure/cancellation when EvalScope produced no rows."""

    now = datetime.now(timezone.utc)
    request = Request(
        run_id=run.id,
        kind="perf",
        phase="steady",
        stream_mode="stream" if (run.run_snapshot or {}).get("stream") else "nonstream",
        input_messages={"engine": "evalscope"},
        status=status,
        scheduled_at=now,
        started_at=now,
        finished_at=now,
    )
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        request.id = _new_id(db, Request)
    db.add(request)
    db.flush()
    attempt = Attempt(
        request_id=request.id,
        attempt_index=1,
        started_at=now,
        finished_at=now,
        error_category=category,
        error_body=redact_sensitive_text(str(error)[:500], secret) or None,
    )
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        attempt.id = _new_id(db, Attempt)
    db.add(attempt)
    db.commit()
    return request, attempt


def _run_connection_secret(
    db: Session,
    run: Run,
    crypto: CryptoService,
) -> str | None:
    """Best-effort lookup used when an adapter fails outside its normal path.

    EvalScope exceptions can include a rendered config.  That config contains
    the decrypted API key, so the outer failure handler must have the same
    request-specific secret available as the successful evidence path.
    """

    raw_id = (run.run_snapshot or {}).get("connection_id")
    if not raw_id:
        return None
    try:
        connection = db.get(Connection, uuid.UUID(str(raw_id)))
    except (TypeError, ValueError):
        return None
    if connection is None or not connection.api_key_ciphertext:
        return None
    try:
        return crypto.decrypt(connection.api_key_ciphertext)
    except Exception:
        return None


def execute_evalscope_performance(
    db: Session,
    run: Run,
    crypto: CryptoService,
    ssrf_guard: SSRFGuard,
    *,
    work_dir: str | None = None,
    engine: Any | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Execute EvalScope performance and always converge engine failures."""

    try:
        return _execute_evalscope_performance_impl(
            db,
            run,
            crypto,
            ssrf_guard,
            work_dir=work_dir,
            engine=engine,
            should_cancel=should_cancel,
        )
    except Exception as exc:
        cancelled = False
        try:
            cancelled = bool(should_cancel and should_cancel())
        except Exception:
            pass
        status = "cancelled" if cancelled else "failed"
        category = "cancelled" if cancelled else "engine_error"
        secret = _run_connection_secret(db, run, crypto)
        safe_error = redact_sensitive_text(str(exc)[:500], secret)
        _record_engine_failure(
            db,
            run,
            safe_error,
            category=category,
            status=status,
            secret=secret,
        )
        return {
            "status": status,
            "engine": "evalscope",
            "kind": "performance",
            "error": safe_error,
            "record_count": 0,
            "completed": 0,
            "failed": 0,
        }


def _execute_evalscope_performance_impl(
    db: Session,
    run: Run,
    crypto: CryptoService,
    ssrf_guard: SSRFGuard,
    *,
    work_dir: str | None = None,
    engine: Any | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Execute one EvalScope perf benchmark and persist its request evidence."""

    snapshot = run.run_snapshot or {}
    connection_id = snapshot.get("connection_id")
    if not connection_id:
        raise EvaluationEngineConfigurationError("Run 快照缺少 connection_id")
    connection = db.get(Connection, uuid.UUID(str(connection_id)))
    if not connection or connection.status != "active":
        raise ValueError("连接不存在或未启用")
    base_url = str(snapshot.get("base_url") or connection.base_url).strip().rstrip("/")
    if hasattr(ssrf_guard, "resolve_url"):
        allowed, reason, target = ssrf_guard.resolve_url(base_url)
    else:
        allowed, reason = ssrf_guard.check_url(base_url)
        target = None
    if not allowed:
        raise ValueError(f"SSRF 校验失败: {reason}")
    if not connection.api_key_ciphertext:
        raise ValueError("连接未配置 API Key")
    api_key = crypto.decrypt(connection.api_key_ciphertext)
    result = run_evalscope_performance(
        {**snapshot, "run_id": str(run.id), "base_url": base_url},
        api_key=api_key,
        work_dir=work_dir or get_settings().evalscope_dir,
        dataset_root=get_settings().dataset_dir,
        # Leave the engine unset for production runs so the evaluation
        # boundary can place real EvalScope in a killable child process. Tests
        # may inject a lightweight engine explicitly.
        engine=engine,
        ssrf_target=target,
        verify=not bool(snapshot.get("tls_skip_verify", connection.tls_skip_verify)),
        should_cancel=should_cancel,
    )
    safe_result = redact_sensitive_value(result, api_key)
    records = list(safe_result.get("records")) if isinstance(safe_result.get("records"), list) else []
    now = datetime.now(timezone.utc)
    if records:
        for record in records:
            if isinstance(record, dict):
                _record_evidence(db, run, record, now=now, secret=api_key)
        if safe_result.get("status") == "cancelled" and not any(
            isinstance(record, dict) and record.get("status") == "cancelled"
            for record in records
        ):
            # The child process may have flushed only a prefix of the
            # benchmark. Keep an explicit cancellation marker alongside those
            # completed/failed rows instead of presenting a partial run as a
            # clean completion.
            cancellation_record = {
                "status": "cancelled",
                "phase": "steady",
                "stream_mode": "stream" if snapshot.get("stream") else "nonstream",
                "request_body": {"engine": "evalscope", "cancelled": True},
                "error_category": "cancelled",
                "error": "Run 在 EvalScope 执行过程中被取消",
            }
            _record_evidence(db, run, cancellation_record, now=now, secret=api_key)
            records.append(cancellation_record)
        db.commit()
    else:
        # Preserve an explicit failed evidence row when the engine is
        # unavailable, misconfigured, or produced no SQLite result rows.
        status = "cancelled" if safe_result.get("status") == "cancelled" else "failed"
        error = str(safe_result.get("error") or "EvalScope 未返回逐请求结果")[:500]
        request = Request(
            run_id=run.id,
            kind="perf",
            phase="steady",
            stream_mode="stream" if snapshot.get("stream") else "nonstream",
            input_messages={"engine": "evalscope"},
            status=status,
            scheduled_at=now,
            started_at=now,
            finished_at=now,
        )
        if db.bind is not None and db.bind.dialect.name == "sqlite":
            request.id = _new_id(db, Request)
        db.add(request)
        db.flush()
        attempt = Attempt(
            request_id=request.id,
            attempt_index=1,
            started_at=now,
            finished_at=now,
            error_category=(
                "cancelled"
                if status == "cancelled"
                else "engine_unavailable"
                if safe_result.get("status") == "unavailable"
                else "engine_error"
            ),
            error_body=redact_sensitive_text(error, api_key),
        )
        if db.bind is not None and db.bind.dialect.name == "sqlite":
            attempt.id = _new_id(db, Attempt)
        db.add(attempt)
        db.commit()
    completed = sum(isinstance(item, dict) and item.get("status") == "completed" for item in records)
    failed = len(records) - completed
    status = "completed" if records and failed == 0 else "partially_completed" if completed else "failed"
    if safe_result.get("status") == "cancelled":
        status = "cancelled"
    if safe_result.get("status") == "unavailable":
        status = "failed"
    return {
        **safe_result,
        "status": status,
        "record_count": len(records),
        "completed": completed,
        "failed": failed,
    }
