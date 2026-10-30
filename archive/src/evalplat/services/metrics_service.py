"""Run 指标聚合：只消费已落库的 Request/Attempt 证据。"""

import uuid
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from statistics import mean
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from evalplat.core.errors import NotFoundError
from evalplat.models.attempt import Attempt
from evalplat.models.code_execution import CodeExecution
from evalplat.models.request import Request
from evalplat.models.run import Run
from evalplat.services.token_usage import normalize_usage_tokens


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile / 100
    lower, upper = int(rank), min(int(rank) + 1, len(ordered) - 1)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower), 4)


def _request_token_counts(request: Request) -> tuple[str, dict[str, Any]]:
    """Return independent vendor/local evidence and a non-double-counted primary view."""

    vendor = {
        "prompt_tokens": request.vendor_prompt_tokens,
        "cached_tokens": request.vendor_cached_tokens,
        "completion_tokens": request.vendor_completion_tokens,
        "total_tokens": request.vendor_total_tokens,
    }
    vendor_counts = normalize_usage_tokens(vendor) if any(value is not None for value in vendor.values()) else None
    local = {
        "prompt_tokens": request.local_prompt_tokens,
        "completion_tokens": request.local_completion_tokens,
        "total_tokens": request.local_total_tokens,
    }
    local_counts = normalize_usage_tokens(local) if any(value is not None for value in local.values()) else None
    if vendor_counts is not None and local_counts is not None:
        # Vendor usage is the primary/cost evidence for this request. Local
        # counts remain available for tokenizer alignment and differences but
        # must not be added a second time to overall throughput.
        return "both", {"primary": vendor_counts, "vendor": vendor_counts, "local": local_counts}
    if vendor_counts is not None:
        return "vendor", {"primary": vendor_counts, "vendor": vendor_counts, "local": None}
    if local_counts is not None:
        return "local", {"primary": local_counts, "vendor": None, "local": local_counts}
    empty = normalize_usage_tokens(None)
    return "none", {"primary": empty, "vendor": None, "local": None}


def _summary(rows: list[tuple[Request, Attempt | None]], run_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    total = len(rows)
    scheduled_dropped = sum(
        bool(attempt and attempt.error_category == "schedule_dropped")
        for _, attempt in rows
    )
    sent_rows = [
        (request, attempt)
        for request, attempt in rows
        if not (attempt and attempt.error_category == "schedule_dropped")
    ]
    sent_count = len(sent_rows)
    completed = sum(request.status == "completed" for request, _ in sent_rows)
    failed = sum(request.status == "failed" for request, _ in sent_rows)
    cancelled = sum(request.status == "cancelled" for request, _ in sent_rows)

    def values(field: str) -> list[float]:
        # A ``schedule_dropped`` row is a planned request that never reached
        # the provider.  Its synthetic Attempt has no latency/throughput
        # evidence and must not distort latency, ITL, or speed distributions.
        return [
            float(getattr(attempt, field))
            for _, attempt in sent_rows
            if attempt and getattr(attempt, field) is not None
        ]

    def stats(field: str) -> dict[str, float | None]:
        data = values(field)
        return {
            "mean": round(mean(data), 4) if data else None,
            "min": round(min(data), 4) if data else None,
            "max": round(max(data), 4) if data else None,
            "p50": _percentile(data, 50), "p90": _percentile(data, 90),
            "p95": _percentile(data, 95), "p99": _percentile(data, 99),
        }

    prompt_tokens = completion_tokens = primary_total_tokens = 0
    vendor_prompt_tokens = vendor_cached_tokens = vendor_completion_tokens = vendor_total_tokens = 0
    local_prompt_tokens = local_completion_tokens = 0
    local_total_tokens = 0
    sources: set[str] = set()
    effective_cached_tokens = 0
    token_attribution_incomplete = False
    cost_estimated = False
    for request, _ in sent_rows:
        source_name, counts_by_source = _request_token_counts(request)
        vendor_counts = counts_by_source.get("vendor")
        local_counts = counts_by_source.get("local")
        primary_counts = counts_by_source["primary"]
        if vendor_counts is not None:
            vendor_prompt = vendor_counts["prompt_tokens"] or 0
            vendor_completion = vendor_counts["completion_tokens"] or 0
            vendor_total = vendor_counts["total_tokens"]
            if vendor_total is not None and (
                vendor_counts["prompt_tokens"] is None or vendor_counts["completion_tokens"] is None
            ):
                token_attribution_incomplete = True
            vendor_prompt_tokens += vendor_prompt
            request_cached = min(vendor_counts["cached_tokens"] or 0, vendor_prompt)
            vendor_cached_tokens += request_cached
            vendor_completion_tokens += vendor_completion
            vendor_total_tokens += vendor_total if vendor_total is not None else vendor_prompt + vendor_completion
            sources.add("vendor")
        if local_counts is not None:
            local_prompt = local_counts["prompt_tokens"] or 0
            local_completion = local_counts["completion_tokens"] or 0
            local_total = local_counts["total_tokens"]
            local_prompt_tokens += local_prompt
            local_completion_tokens += local_completion
            local_total_tokens += local_total if local_total is not None else local_prompt + local_completion
            sources.add("local")
            # Local tokenizer values are an estimate only when the provider
            # did not return usage for this request.  A request can contain
            # both sources; that is still billable from vendor usage and must
            # not be marked estimated merely because local alignment evidence
            # is also present.
            if vendor_counts is None:
                cost_estimated = True

        # Overall throughput/cost uses exactly one source per request. Prefer
        # vendor when present, otherwise local; this preserves the existing
        # mixed-source semantics without counting one request twice.
        if source_name in {"vendor", "both", "local"}:
            prompt = primary_counts["prompt_tokens"] or 0
            completion = primary_counts["completion_tokens"] or 0
            primary_total = primary_counts["total_tokens"]
            prompt_tokens += prompt
            completion_tokens += completion
            primary_total_tokens += primary_total if primary_total is not None else prompt + completion
            if source_name == "both":
                effective_cached_tokens += min(vendor_counts["cached_tokens"] or 0, vendor_prompt)
            elif source_name == "vendor":
                effective_cached_tokens += min(primary_counts["cached_tokens"] or 0, prompt)
    source = next(iter(sources)) if len(sources) == 1 else "mixed" if sources else "none"
    # A provider may report only total_tokens. Keep that evidence for
    # throughput even when input/output attribution is unavailable.
    total_tokens = primary_total_tokens
    # Throughput is measured over the Run wall-clock interval. Summing request
    # latencies underestimates throughput whenever requests overlap. A
    # schedule_dropped row is a planned-but-never-sent request and must not
    # inflate the actual provider throughput.
    def as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    started_at = [as_utc(request.started_at) for request, _ in sent_rows if request.started_at]
    finished_at = [as_utc(request.finished_at) for request, _ in sent_rows if request.finished_at]
    duration_seconds = 0.0
    if started_at and finished_at:
        start = min(started_at)
        finish = max(finished_at)
        duration_seconds = max(0.0, (finish - start).total_seconds())
    if duration_seconds <= 0:
        duration_seconds = sum(
            float(attempt.latency_ms)
            for _, attempt in sent_rows
            if attempt and attempt.latency_ms
        ) / 1000
    def difference_rate(vendor_value: int, local_value: int) -> float | None:
        """Express local-vs-vendor drift without dividing by an empty baseline."""

        if vendor_value == 0:
            return None
        return round((vendor_value - local_value) / vendor_value, 6)
    itl_values: list[float] = []
    for _, attempt in sent_rows:
        if not attempt or not attempt.itl_series:
            continue
        raw_values = attempt.itl_series if isinstance(attempt.itl_series, list) else attempt.itl_series.get("values", [])
        for value in raw_values:
            try:
                itl_values.append(float(value))
            except (TypeError, ValueError):
                continue

    request_series: list[dict[str, Any]] = []
    error_categories: dict[str, int] = defaultdict(int)

    def timestamp(value: datetime | None) -> str | None:
        if value is None:
            return None
        normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return normalized.isoformat()

    for request, attempt in rows:
        error_category = attempt.error_category if attempt else None
        if error_category is None and attempt and attempt.http_status is not None and not 200 <= attempt.http_status < 300:
            error_category = f"http:{attempt.http_status}"
        if error_category is None and request.status == "failed":
            error_category = "request_failed"
        if error_category and error_category != "schedule_dropped":
            error_categories[str(error_category)] = error_categories.get(str(error_category), 0) + 1
        _, counts_by_source = _request_token_counts(request)
        counts = counts_by_source["primary"]
        request_series.append(
            {
                "request_id": request.id,
                "sample_index": request.sample_index,
                "phase": request.phase,
                "status": request.status,
                "started_at": timestamp(request.started_at or request.scheduled_at),
                "finished_at": timestamp(request.finished_at),
                "latency_ms": float(attempt.latency_ms) if attempt and attempt.latency_ms is not None else None,
                "ttft_ms": float(attempt.ttft_ms) if attempt and attempt.ttft_ms is not None else None,
                "tokens": (
                    counts["total_tokens"]
                ),
                "error_category": error_category,
                "scheduled_dropped": error_category == "schedule_dropped",
            }
        )
    request_series.sort(key=lambda item: (item["started_at"] or "", item["request_id"]))
    price = (run_snapshot or {}).get("price") or {}
    cost = None
    try:
        if price and (prompt_tokens or completion_tokens) and not token_attribution_incomplete:
            unit = str(price.get("unit") or "per_1k_tokens").lower()
            divisor = Decimal("1") if unit in {"per_token", "token"} else Decimal("1000")
            cached_tokens = min(effective_cached_tokens, prompt_tokens)
            uncached_prompt_tokens = max(0, prompt_tokens - cached_tokens)
            cached_price = price.get("cached_token_price")
            prompt_cost = Decimal(str(uncached_prompt_tokens)) / divisor * Decimal(str(price.get("input_token_price", 0)))
            if cached_tokens:
                prompt_cost += Decimal(str(cached_tokens)) / divisor * Decimal(str(cached_price if cached_price is not None else price.get("input_token_price", 0)))
            source_cost = prompt_cost + Decimal(str(completion_tokens)) / divisor * Decimal(str(price.get("output_token_price", 0)))
            exchange_rate = Decimal(str(price.get("exchange_rate", 1) or 1))
            cost = float(source_cost * exchange_rate)
            source_amount = float(source_cost)
        else:
            source_amount = None
            exchange_rate = Decimal("1")
    except (InvalidOperation, TypeError, ValueError):
        cost = None
        source_amount = None
        exchange_rate = Decimal("1")

    if price:
        source_currency = str(price.get("currency") or (run_snapshot or {}).get("currency") or "CNY")
        base_currency = str((run_snapshot or {}).get("base_currency") or "CNY")
        cost_detail = {
            "amount": round(cost, 8) if cost is not None else None,
            "currency": base_currency,
            "source_amount": round(source_amount, 8) if source_amount is not None else None,
            "source_currency": source_currency,
            "exchange_rate": float(exchange_rate),
            "estimated": cost_estimated or token_attribution_incomplete,
            "attribution_complete": not token_attribution_incomplete,
            "unit": price.get("unit") or "per_1k_tokens",
            "cached_tokens": effective_cached_tokens,
        }
    else:
        cost_detail = None

    return {
        "request_count": total,
        "sent_request_count": sent_count,
        "scheduled_dropped": scheduled_dropped,
        "completed": completed,
        "failed": failed,
        "cancelled": cancelled,
        "success_rate": completed / sent_count if sent_count else None,
        "error_rate": failed / sent_count if sent_count else None,
        "latency_ms": stats("latency_ms"),
        "ttft_ms": stats("ttft_ms"),
        "tpot_ms": stats("tpot_ms"),
        "itl_ms": {
            "mean": round(mean(itl_values), 4) if itl_values else None,
            "min": round(min(itl_values), 4) if itl_values else None,
            "max": round(max(itl_values), 4) if itl_values else None,
            "p50": _percentile(itl_values, 50), "p90": _percentile(itl_values, 90),
            "p95": _percentile(itl_values, 95), "p99": _percentile(itl_values, 99),
        },
        "avg_speed_tokens_per_second": stats("avg_speed"),
        "peak_speed_tokens_per_second": stats("peak_speed"),
        "peak_tps": stats("peak_tps"),
        "throughput_requests_per_second": round(sent_count / duration_seconds, 4) if duration_seconds else None,
        "tokens": {
            "prompt": prompt_tokens, "completion": completion_tokens, "total": total_tokens,
            "source": source,
        },
        "tokens_by_source": {
            "vendor": {
                "prompt": vendor_prompt_tokens,
                "cached": vendor_cached_tokens,
                "completion": vendor_completion_tokens,
                "total": vendor_total_tokens,
            },
            "local": {
                "prompt": local_prompt_tokens,
                "completion": local_completion_tokens,
                "total": local_total_tokens,
            },
            "difference": {
                "prompt": vendor_prompt_tokens - local_prompt_tokens,
                "cached": vendor_cached_tokens,
                "completion": vendor_completion_tokens - local_completion_tokens,
                "total": vendor_total_tokens - local_total_tokens,
            },
            "difference_rate": {
                "prompt": difference_rate(vendor_prompt_tokens, local_prompt_tokens),
                "completion": difference_rate(vendor_completion_tokens, local_completion_tokens),
                "total": difference_rate(vendor_total_tokens, local_total_tokens),
            },
        },
        "throughput_tokens_per_second": round(total_tokens / duration_seconds, 4) if duration_seconds else None,
        "throughput_tokens_per_second_by_source": {
            "vendor": round(vendor_total_tokens / duration_seconds, 4) if duration_seconds else None,
            "local": round(local_total_tokens / duration_seconds, 4) if duration_seconds else None,
        },
        "request_series": request_series,
        "error_categories": dict(error_categories),
        "cost": cost_detail,
    }


def aggregate_run_metrics(db: Session, run_id: uuid.UUID) -> dict[str, Any]:
    """按请求 phase 聚合 Run 指标；不适用指标保持 null。"""
    run = db.get(Run, run_id)
    if not run:
        raise NotFoundError("Run 不存在")
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
        .join(Request, Request.id == Attempt.request_id)
        .where(Request.run_id == run_id)
        .subquery()
    )
    latest_attempt_ids = select(ranked_attempts.c.attempt_id).where(
        ranked_attempts.c.attempt_rank == 1
    )
    rows = [
        (request, attempt)
        for request, attempt in db.execute(
            select(Request, Attempt)
            .outerjoin(
                Attempt,
                and_(
                    Attempt.request_id == Request.id,
                    Attempt.id.in_(latest_attempt_ids),
                ),
            )
            .where(Request.run_id == run_id)
            .order_by(Request.id)
        )
    ]
    grouped: dict[str, list[tuple[Request, Attempt | None]]] = defaultdict(list)
    for request, attempt in rows:
        grouped[request.phase or "unknown"].append((request, attempt))
    summary = _summary(
        [(r, a) for values in grouped.values() for r, a in values],
        run.run_snapshot,
    )
    quality_results = [
        request.quality_result
        for request, _ in rows
        if request.kind == "quality" and isinstance(request.quality_result, dict)
    ]
    scores: list[float] = []
    quality_sample_count = 0
    for item in quality_results:
        samples = item.get("samples") if isinstance(item.get("samples"), list) else []
        quality_sample_count += int(item.get("sample_count", 0) or 0) or (len(samples) or 1)
        if item.get("score") is not None:
            try:
                scores.append(float(item["score"]))
            except (TypeError, ValueError):
                pass
        for sample in samples:
            if isinstance(sample, dict) and sample.get("score") is not None:
                try:
                    scores.append(float(sample["score"]))
                except (TypeError, ValueError):
                    pass
        single_sample = item.get("sample")
        if isinstance(single_sample, dict) and single_sample.get("score") is not None:
            try:
                scores.append(float(single_sample["score"]))
            except (TypeError, ValueError):
                pass
    engines: dict[str, int] = defaultdict(int)
    for item in quality_results:
        engines[str(item.get("engine", "unknown"))] += 1
    summary["quality"] = {
        "sample_count": quality_sample_count,
        "scored_count": len(scores),
        "score_mean": round(sum(scores) / len(scores), 6) if scores else None,
        "accuracy": round(sum(score == 1.0 for score in scores) / len(scores), 6) if scores else None,
        "engines": dict(engines),
    }
    summary["code_execution"] = _code_execution_summary(db, run_id)
    return {
        "run_id": str(run_id),
        "summary": summary,
        "by_phase": {
            phase: _summary(rows, run.run_snapshot)
            for phase, rows in grouped.items()
        },
    }


def _code_execution_summary(db: Session, run_id: uuid.UUID) -> dict[str, Any]:
    """Summarize asynchronous code-worker evidence without mixing it into HTTP metrics."""

    # A Run can contain one CodeExecution per quality sample.  Aggregate the
    # status counters in SQL so monitoring/report generation does not materialize
    # the entire execution evidence table just to render six integers.
    status_counts = {
        str(status): int(count)
        for status, count in db.execute(
            select(CodeExecution.status, func.count())
            .where(CodeExecution.run_id == run_id)
            .group_by(CodeExecution.status)
        )
    }
    total = sum(status_counts.values())
    passed = status_counts.get("passed", 0)
    failed = status_counts.get("failed", 0)
    timeout = status_counts.get("timeout", 0)
    unavailable = status_counts.get("unavailable", 0)
    cancelled = status_counts.get("cancelled", 0)
    pending = status_counts.get("pending", 0) + status_counts.get("running", 0)
    evaluated = total - pending
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "timeout": timeout,
        "unavailable": unavailable,
        "cancelled": cancelled,
        "pending": pending,
        "pass_rate": passed / evaluated if evaluated else None,
    }
