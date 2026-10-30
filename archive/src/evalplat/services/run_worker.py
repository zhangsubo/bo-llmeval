"""普通 Run worker 的最小执行循环。"""

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import logging
import time
import threading
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from evalplat.core.crypto import CryptoService
from evalplat.core.ssrf import SSRFGuard
from evalplat.models.task import Task
from evalplat.models.run import Run
from evalplat.models.dataset import DatasetVersion
from evalplat.services.run_executor import execute_chat_request, record_dropped_request
from evalplat.services.run_queue_service import claim_next_run, finish_run, renew_lease, wait_for_code
from evalplat.services.task_lifecycle_service import mark_running, sync_from_runs
from evalplat.services.load_profile_service import build_schedule
from evalplat.services.quality_executor import execute_evalscope_run, execute_quality_samples
from evalplat.services.performance_executor import execute_evalscope_performance
from evalplat.services.dataset_service import load_samples
from evalplat.services.code_execution_service import enqueue_code_jobs
from evalplat.config import get_settings
from evalplat.services.worker_heartbeat_service import record_heartbeat
from evalplat.services.length_preset_service import build_preset_messages, get_length_preset
from evalplat.models.tokenizer import Tokenizer
from evalplat.services.run_budget import RunBudget
from evalplat.services.tokenizer_service import count_tokens
from evalplat.services.security_service import redact_sensitive_text


logger = logging.getLogger(__name__)


class _LeaseRenewer:
    """Renew one Run lease in a separate short-lived database session."""

    def __init__(self, session_factory: Callable[[], Session] | None, run: Run, worker_id: str):
        self._session_factory = session_factory
        self._run = run
        self._worker_id = worker_id
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._session_factory is None:
            return
        self._thread = threading.Thread(target=self._loop, name=f"run-lease-{self._run.id}", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(20.0):
            session = self._session_factory()  # type: ignore[misc]
            try:
                renew_lease(
                    session,
                    self._run.id,
                    self._worker_id,
                    self._run.fencing_token,
                )
                record_heartbeat(
                    session,
                    self._worker_id,
                    status="running",
                    detail=f"run:{self._run.id}",
                )
            except Exception:
                # The main execution path will surface a fencing failure when
                # it writes the final state. Avoid noisy background retries.
                return
            finally:
                session.close()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


def _recover_claimed_run(
    db: Session,
    run: Run,
    worker_id: str,
    error: BaseException,
    session_factory: Callable[[], Session] | None,
) -> None:
    """Best-effort fenced failure recovery after an execution exception.

    Provider/adapter code may fail while a transaction is in the
    ``rollback-only`` state (for example after an ``IntegrityError``).  Trying
    to call ``finish_run`` on that Session would raise a second exception and
    leave the Run running until lease expiry.  Roll back first and, in the
    production worker, use a fresh short-lived Session for the recovery write.
    """

    error_text = redact_sensitive_text(str(error)[:500]) or type(error).__name__
    try:
        db.rollback()
    except Exception:
        logger.exception("failed to rollback worker session after run error", extra={"run_id": str(run.id)})

    recovery_db = db
    owns_recovery_session = False
    if session_factory is not None:
        try:
            recovery_db = session_factory()
            owns_recovery_session = recovery_db is not db
        except Exception:
            logger.exception("failed to open run recovery session", extra={"run_id": str(run.id)})

    try:
        current = recovery_db.get(Run, run.id)
        if current is None or current.status in {
            "completed",
            "partially_completed",
            "failed",
            "cancelled",
            "interrupted_failed",
            "awaiting_code",
        }:
            return
        finish_run(
            recovery_db,
            run.id,
            worker_id,
            run.fencing_token,
            "failed",
            error_text,
        )
        sync_from_runs(recovery_db, run.task_id)
    except Exception:
        try:
            recovery_db.rollback()
        except Exception:
            pass
        logger.exception(
            "failed to converge Run after worker exception",
            extra={"run_id": str(run.id), "worker_id": worker_id},
        )
    finally:
        if owns_recovery_session:
            recovery_db.close()


def process_one(
    db: Session,
    worker_id: str,
    crypto: CryptoService,
    ssrf_guard: SSRFGuard,
    executor: Callable = execute_chat_request,
    max_concurrency: int | None = None,
    session_factory: Callable[[], Session] | None = None,
    evalscope_engine: Any | None = None,
) -> dict | None:
    """认领并处理一个 Run；无排队任务时返回 ``None``。"""
    run = claim_next_run(db, worker_id, max_concurrency=max_concurrency)
    if run is None:
        return None
    renewer = _LeaseRenewer(session_factory, run, worker_id)
    renewer.start()
    try:
        try:
            return _process_claimed(
                db,
                run,
                worker_id,
                crypto,
                ssrf_guard,
                executor,
                max_concurrency,
                session_factory,
                evalscope_engine,
            )
        except Exception as exc:
            _recover_claimed_run(db, run, worker_id, exc, session_factory)
            return {
                "run_id": str(run.id),
                "status": "failed",
                "error": redact_sensitive_text(str(exc)[:500]),
            }
    finally:
        renewer.stop()


def _process_claimed(
    db: Session,
    run: Run,
    worker_id: str,
    crypto: CryptoService,
    ssrf_guard: SSRFGuard,
    executor: Callable,
    max_concurrency: int | None,
    session_factory: Callable[[], Session] | None,
    evalscope_engine: Any | None,
) -> dict | None:
    """执行已认领 Run；外层 ``process_one`` 负责 claim 和续租。"""
    mark_running(db, run.task_id)
    if _cancel_requested(db, run.id):
        finish_run(db, run.id, worker_id, run.fencing_token, "cancelled")
        sync_from_runs(db, run.task_id)
        return {"run_id": str(run.id), "status": "cancelled"}
    snapshot = run.run_snapshot or {}
    runtime_budget = RunBudget.from_snapshot(snapshot)
    should_cancel = lambda: _cancel_requested(db, run.id) or runtime_budget.expired()
    if snapshot.get("evaluation_engine", "native") == "evalscope" and snapshot.get("evaluation_kind", "performance") == "performance":
        try:
            perf_result = execute_evalscope_performance(
                db,
                run,
                crypto,
                ssrf_guard,
                work_dir=get_settings().evalscope_dir,
                engine=evalscope_engine,
                should_cancel=should_cancel,
            )
        except Exception as exc:
            perf_result = {"status": "failed", "error": redact_sensitive_text(str(exc)[:500]), "engine": "evalscope", "kind": "performance"}
        finish_run(
            db,
            run.id,
            worker_id,
            run.fencing_token,
            perf_result.get("status", "failed"),
            perf_result.get("error"),
        )
        sync_from_runs(db, run.task_id)
        return {"run_id": str(run.id), **perf_result}
    if snapshot.get("evaluation_kind") == "quality":
        budget = runtime_budget
        samples: list[dict[str, Any]] = []
        quality_results: list[dict[str, Any]] = []
        if snapshot.get("evaluation_engine", "native") == "evalscope":
            if should_cancel():
                finish_run(db, run.id, worker_id, run.fencing_token, "cancelled")
                sync_from_runs(db, run.task_id)
                return {"run_id": str(run.id), "status": "cancelled"}
            try:
                quality_results = [
                    execute_evalscope_run(
                        db,
                        run,
                        crypto,
                        ssrf_guard,
                        work_dir=get_settings().evalscope_dir,
                        engine=evalscope_engine,
                        should_cancel=should_cancel,
                    )
                ]
            except Exception as exc:
                # EvalScope 配置/运行错误必须收敛 Run，不能让队列永久停在 running。
                quality_results = [{"status": "failed", "error": redact_sensitive_text(str(exc)[:500])}]
        else:
            try:
                samples = snapshot.get("quality_samples") or []
                if not samples and snapshot.get("dataset_version_id"):
                    dataset_version = db.get(DatasetVersion, snapshot["dataset_version_id"])
                    if dataset_version:
                        samples = load_samples(
                            dataset_version,
                            storage_root=get_settings().dataset_dir,
                            limit=int(snapshot.get("request_count", 0) or 0) or None,
                        )
                dataset_snapshot = snapshot.get("dataset_snapshot") or {}
                scoring_method = (
                    snapshot.get("scoring_method")
                    or dataset_snapshot.get("scoring_method")
                    or "exact_match"
                )
                samples = [
                    {**sample, "scoring_method": sample.get("scoring_method") or scoring_method}
                    for sample in samples
                ]
                retry_policy = snapshot.get("retry_policy") or {}
                quality_results = execute_quality_samples(
                    db,
                    run,
                    samples,
                    crypto,
                    ssrf_guard,
                    max_retries=int(retry_policy.get("max_retries", 2) or 0),
                    stream=bool(snapshot.get("stream", False)),
                    should_cancel=should_cancel,
                    budget=budget,
                )
            except Exception as exc:
                quality_results = [{"status": "failed", "error": redact_sensitive_text(str(exc)[:500])}]
        code_jobs = 0
        if snapshot.get("quality_mode") == "code" and quality_results:
            try:
                code_jobs = enqueue_code_jobs(db, run, samples, quality_results, snapshot=snapshot)
            except Exception as exc:
                # A persisted legacy snapshot may fail the current sandbox
                # policy. Roll back any partially flushed jobs, then converge
                # the claimed Run instead of leaving it stuck in ``running``.
                db.rollback()
                error = redact_sensitive_text(str(exc)[:500])
                finish_run(db, run.id, worker_id, run.fencing_token, "failed", error)
                sync_from_runs(db, run.task_id)
                return {"run_id": str(run.id), "status": "failed", "error": error}
        if code_jobs:
            # Keep the Run non-terminal until every CodeExecution has a
            # terminal result. The code worker will perform final reconciliation.
            if budget.expired():
                # Quality samples that were not admitted before the duration
                # cap have no CodeExecution row. Preserve that truncation on
                # the Run so code-worker reconciliation cannot call the
                # observed prefix a complete benchmark.
                run.error_summary = "Run runtime budget exceeded"
                db.flush()
            waiting = wait_for_code(db, run.id, worker_id, run.fencing_token)
            sync_from_runs(db, run.task_id)
            return {
                "run_id": str(waiting.id),
                "status": waiting.status,
                "code_execution_pending": code_jobs,
                "last_result": quality_results[-1] if quality_results else None,
            }
        # A native quality executor may stop between samples after an operator
        # changes the Task/Run to ``cancelling``.  In that case the returned
        # prefix can consist entirely of successful samples, but it must never
        # be reported as a completed benchmark.  Re-read the lifecycle state
        # after the executor returns so cancellation wins over result shape.
        cancel_requested = _cancel_requested(db, run.id)
        budget_expired = budget.expired()
        result = quality_results[-1] if quality_results else {
            "status": "failed",
            "error": "Run runtime budget exceeded" if budget_expired else "质量评测没有样本",
        }
        final_status = (
            "cancelled" if cancel_requested or result.get("status") == "cancelled"
            else "partially_completed" if result.get("status") == "partially_completed"
            # A duration cap can stop scheduling between two successful
            # samples. Preserve the truncation as an incomplete Run rather
            # than reporting a complete benchmark with missing samples.
            else "partially_completed" if budget_expired and quality_results and any(item.get("status") == "completed" for item in quality_results)
            else "completed" if quality_results and all(item.get("status") == "completed" for item in quality_results)
            else "partially_completed" if any(item.get("status") == "completed" for item in quality_results)
            else "failed"
        )
        if budget_expired and final_status != "cancelled" and not result.get("error"):
            result = {**result, "error": "Run runtime budget exceeded"}
        finish_run(db, run.id, worker_id, run.fencing_token, final_status, result.get("error"))
        sync_from_runs(db, run.task_id)
        return {"run_id": str(run.id), **result, "status": final_status}
    results: list[dict] = []
    try:
        request_count = int(snapshot.get("request_count", 1) or 1)
        # 新任务把冷启动/预热作为独立阶段；没有该字段的旧快照保持原有总请求数。
        if "warmup_count" in snapshot:
            warmup_count = int(snapshot.get("warmup_count", 0) or 0)
            phase_names = ["cold_start"] + ["warmup"] * warmup_count + ["steady"] * request_count
        else:
            phase_names = ["steady"] * request_count
        schedule = build_schedule(snapshot.get("load_profile"), len(phase_names))
        messages_provider = _build_messages_provider(db, snapshot)
        results.extend(
            _execute_scheduled_requests(
                db,
                run,
                schedule,
                phase_names,
                messages_provider,
                crypto,
                ssrf_guard,
                executor,
                snapshot,
                session_factory,
                budget=runtime_budget,
            )
        )
    except Exception as exc:
        results.append({"status": "failed", "error": redact_sensitive_text(str(exc)[:500])})
    result = results[-1] if results else {"status": "cancelled"}
    budget_expired = runtime_budget.expired()
    if _cancel_requested(db, run.id):
        final_status = "cancelled"
    elif not results or all(item["status"] == "failed" for item in results):
        final_status = "failed"
    elif budget_expired:
        # The scheduler may stop after a duration budget without creating a
        # synthetic request row. Never call that a complete benchmark.
        final_status = "partially_completed"
    elif all(item["status"] == "completed" for item in results):
        final_status = "completed"
    else:
        final_status = "partially_completed"
    finish_run(
        db,
        run.id,
        worker_id,
        run.fencing_token,
        final_status,
        result.get("error") or ("Run runtime budget exceeded" if budget_expired else None),
    )
    sync_from_runs(db, run.task_id)
    return {"run_id": str(run.id), **result, "status": final_status}


def _execute_scheduled_requests(
    db: Session,
    run: Run,
    schedule,
    phase_names: list[str],
    messages_provider: Callable[[int], list[dict[str, Any]]],
    crypto: CryptoService,
    ssrf_guard: SSRFGuard,
    executor: Callable,
    snapshot: dict,
    session_factory: Callable[[], Session] | None,
    budget: RunBudget | None = None,
) -> list[dict]:
    """按计划执行请求；生产 Worker 使用独立 Session 支持真实 in-flight 并发。"""

    profile = snapshot.get("load_profile") or {}
    mode = profile.get("mode", "fixed_concurrency")
    configured = profile.get("concurrency") if mode == "fixed_concurrency" else profile.get("max_inflight", 1)
    try:
        parallelism = max(1, int(configured or 1))
    except (TypeError, ValueError):
        parallelism = 1
    # 没有独立 Session 时不能跨线程使用 db；测试和自定义调用方保留串行路径。
    if session_factory is None or (parallelism == 1 and mode != "fixed_rps"):
        return _execute_sequential(
            db, run, schedule, phase_names, messages_provider, crypto, ssrf_guard, executor, snapshot, budget
        )

    results: list[dict] = []
    pending: set[Future[Any]] = set()
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=parallelism, thread_name_prefix="eval-request") as pool:
        for item in schedule:
            delay = item.offset_seconds - (time.monotonic() - started)
            if delay > 0 and not _wait_for_schedule_delay(db, run.id, delay, budget=budget):
                break
            if _cancel_requested(db, run.id):
                break
            messages = messages_provider(item.index)
            if mode == "fixed_rps":
                # open-loop 计划不因慢请求而整体漂移；达到 in-flight 上限时记录调度丢弃。
                done, pending = wait(pending, timeout=0)
                results.extend(future.result() for future in done)
                if len(pending) >= parallelism:
                    results.append(
                        record_dropped_request(
                            db,
                            run,
                            messages,
                            phase=phase_names[item.index],
                            sample_index=item.index,
                            stream=bool(snapshot.get("stream", False)),
                        )
                    )
                    continue
            while len(pending) >= parallelism:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                results.extend(future.result() for future in done)
            if budget is not None:
                estimated_tokens = _estimate_runtime_tokens(db, snapshot, messages)
                allowed, reason = budget.reserve(estimated_tokens)
                if not allowed:
                    results.append(
                        record_dropped_request(
                            db,
                            run,
                            messages,
                            phase=phase_names[item.index],
                            sample_index=item.index,
                            stream=bool(snapshot.get("stream", False)),
                            reason=reason or "Run runtime budget exceeded",
                        )
                    )
                    break
            pending.add(
                pool.submit(
                    _execute_in_session,
                    session_factory,
                    run.id,
                    phase_names[item.index],
                    item.index,
                    messages,
                    crypto,
                    ssrf_guard,
                    executor,
                    snapshot,
                )
            )
        if pending:
            done, _ = wait(pending)
            results.extend(future.result() for future in done)
    return results


def _execute_sequential(
    db: Session,
    run: Run,
    schedule,
    phase_names: list[str],
    messages_provider: Callable[[int], list[dict[str, Any]]],
    crypto: CryptoService,
    ssrf_guard: SSRFGuard,
    executor: Callable,
    snapshot: dict,
    budget: RunBudget | None = None,
) -> list[dict]:
    results: list[dict] = []
    started = time.monotonic()
    for item in schedule:
        delay = item.offset_seconds - (time.monotonic() - started)
        if delay > 0 and not _wait_for_schedule_delay(db, run.id, delay, budget=budget):
            break
        if _cancel_requested(db, run.id):
            break
        try:
            messages = messages_provider(item.index)
            if budget is not None:
                estimated_tokens = _estimate_runtime_tokens(db, snapshot, messages)
                allowed, reason = budget.reserve(estimated_tokens)
                if not allowed:
                    results.append(
                        record_dropped_request(
                            db,
                            run,
                            messages,
                            phase=phase_names[item.index],
                            sample_index=item.index,
                            stream=bool(snapshot.get("stream", False)),
                            reason=reason or "Run runtime budget exceeded",
                        )
                    )
                    break
            results.append(
                executor(
                    db,
                    run,
                    messages,
                    crypto,
                    ssrf_guard,
                    phase=phase_names[item.index],
                    sample_index=item.index,
                    stream=bool(snapshot.get("stream", False)),
                )
            )
        except Exception as exc:
            results.append({"status": "failed", "error": redact_sensitive_text(str(exc)[:500])})
    return results


def _estimate_runtime_tokens(
    db: Session,
    snapshot: dict[str, Any],
    messages: list[dict[str, Any]],
) -> int | None:
    """Conservative prompt+requested-output estimate for a Run budget."""

    limits = snapshot.get("limits") if isinstance(snapshot.get("limits"), dict) else {}
    if not any(key in limits for key in ("max_total_tokens",)):
        return None
    tokenizer = None
    tokenizer_id = snapshot.get("tokenizer_id")
    if tokenizer_id:
        try:
            tokenizer = db.get(Tokenizer, uuid.UUID(str(tokenizer_id)))
        except (TypeError, ValueError):
            tokenizer = None
    if tokenizer is None or tokenizer.status != "ready":
        return None
    prompt_tokens = count_tokens(tokenizer, messages)
    if prompt_tokens is None:
        return None
    params = snapshot.get("generation_params") if isinstance(snapshot.get("generation_params"), dict) else {}
    raw_output = params.get("max_tokens", params.get("max_completion_tokens", snapshot.get("max_output_tokens", 0)))
    try:
        output_tokens = max(0, int(raw_output or 0))
    except (TypeError, ValueError):
        output_tokens = 0
    return max(0, int(prompt_tokens)) + output_tokens


def _wait_for_schedule_delay(
    db: Session,
    run_id,
    delay: float,
    *,
    poll_seconds: float = 0.2,
    budget: RunBudget | None = None,
) -> bool:
    """Wait for an open-loop schedule while observing cancellation promptly.

    A single ``sleep(delay)`` can be minutes at low RPS and violates the
    cancellation contract.  Polling in short bounded intervals keeps the
    scheduler responsive without adding a second coordination primitive.
    """

    deadline = time.monotonic() + max(0.0, float(delay))
    while True:
        if _cancel_requested(db, run_id) or (budget is not None and budget.expired()):
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return not (_cancel_requested(db, run_id) or (budget is not None and budget.expired()))
        sleep_for = min(remaining, max(0.01, poll_seconds))
        if budget is not None and budget.max_duration_seconds is not None:
            budget_remaining = budget.max_duration_seconds - (
                time.monotonic() - budget.started_monotonic
            )
            if budget_remaining <= 0:
                return False
            sleep_for = min(sleep_for, budget_remaining)
        time.sleep(sleep_for)


def _execute_in_session(
    session_factory: Callable[[], Session],
    run_id,
    phase: str,
    sample_index: int,
    messages: list[dict],
    crypto: CryptoService,
    ssrf_guard: SSRFGuard,
    executor: Callable,
    snapshot: dict,
) -> dict:
    session = session_factory()
    try:
        run = session.get(Run, run_id)
        if run is None:
            return {"status": "failed", "error": "Run 不存在"}
        if _cancel_requested(session, run.id):
            return {"status": "cancelled", "sample_index": sample_index}
        return executor(
            session,
            run,
            messages,
            crypto,
            ssrf_guard,
            phase=phase,
            sample_index=sample_index,
            stream=bool(snapshot.get("stream", False)),
        )
    except Exception as exc:
        session.rollback()
        return {"status": "failed", "error": redact_sensitive_text(str(exc)[:500])}
    finally:
        session.close()


def _build_messages_provider(
    db: Session,
    snapshot: dict[str, Any],
) -> Callable[[int], list[dict[str, Any]]]:
    """Build the immutable input-message source for a performance Run.

    Preset messages are generated in the scheduling thread so the provider
    request workers receive an already materialized, per-index value. This
    keeps the Tokenizer out of request execution threads and guarantees that a
    dropped request records the exact same payload that would have been sent.
    """

    raw_preset = snapshot.get("input_length_preset")
    if raw_preset:
        preset = get_length_preset(raw_preset)
        if preset is None:
            raise ValueError("输入长度预设不能为空")
        tokenizer_id = snapshot.get("tokenizer_id")
        if not tokenizer_id:
            raise ValueError("输入长度预设要求配置 Tokenizer")
        try:
            tokenizer_uuid = uuid.UUID(str(tokenizer_id))
        except (TypeError, ValueError) as exc:
            raise ValueError("输入长度预设的 Tokenizer ID 格式无效") from exc
        tokenizer = db.get(Tokenizer, tokenizer_uuid)
        if tokenizer is None:
            raise ValueError("输入长度预设的 Tokenizer 不存在")
        if tokenizer.status != "ready":
            raise ValueError("输入长度预设要求 Tokenizer 处于 ready 状态")

        def _preset_messages(sample_index: int) -> list[dict[str, Any]]:
            return build_preset_messages(tokenizer, preset, sample_index=sample_index)

        return _preset_messages

    messages = snapshot.get("messages") or [{"role": "user", "content": "ping"}]
    if not isinstance(messages, list) or not messages:
        raise ValueError("性能 Run 的 messages 必须为非空数组")

    def _static_messages(_sample_index: int) -> list[dict[str, Any]]:
        return messages

    return _static_messages


def _cancel_requested(db: Session, run_id) -> bool:
    """Read fresh Run/Task state before scheduling each provider request."""

    # ``SessionLocal`` deliberately uses ``expire_on_commit=False`` so worker
    # code can keep the claimed Run snapshot around.  ``Session.get`` would
    # therefore return a stale identity-map object after an operator cancels
    # the Run in another transaction.  Select scalar columns instead of ORM
    # instances so this guard always observes the latest committed state.
    execute = getattr(db, "execute", None)
    if execute is None:
        # Small in-memory Session stubs used by callers/tests may only expose
        # ``get``.  Real SQLAlchemy sessions always take the fresh-query path.
        run = db.get(Run, run_id)
        if run is None or run.status in {"cancelling", "cancelled"}:
            return True
        if run.task_id is None:
            return False
        task = db.get(Task, run.task_id)
        return bool(task and task.status in {"cancelling", "cancelled"})
    row = execute(select(Run.status, Run.task_id).where(Run.id == run_id)).one_or_none()
    if row is None or row.status in {"cancelling", "cancelled"}:
        return True
    if row.task_id is None:
        return False
    task_status = db.scalar(select(Task.status).where(Task.id == row.task_id))
    return task_status in {"cancelling", "cancelled"}
