"""评测引擎边界。

业务服务只依赖 ``EvaluationEngine`` 协议；EvalScope 的导入、配置构造和
返回值包装集中在本模块，避免第三方 SDK 渗透到任务编排和 API 路由。
"""

from __future__ import annotations

import asyncio
import base64
import json
import ipaddress
from multiprocessing import get_context
from queue import Empty
import pickle
import re
import socket
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping
from typing import Any, Callable, Protocol
from urllib.parse import quote

from evalplat.config import get_settings
from evalplat.core.ssrf import (
    PinnedAsyncHTTPTransport,
    PinnedHTTPTransport,
    ResolvedTarget,
)
from evalplat.services.security_service import redact_sensitive_text


class EvaluationEngineUnavailable(RuntimeError):
    """评测引擎未安装或当前运行环境不可用。"""


class EvaluationEngineConfigurationError(ValueError):
    """任务快照无法映射成评测引擎配置。"""


class EvaluationEngine(Protocol):
    """可替换的评测引擎接口。"""

    def run(self, config: Mapping[str, Any]) -> Any:
        """执行一次评测并返回引擎原始结果。"""


def extract_evalscope_sample_results(value: Any) -> list[dict[str, Any]]:
    """从 EvalScope 返回值中提取可识别的逐样本记录。

    EvalScope 不同 benchmark/版本可能使用 ``results``、``samples``、
    ``details`` 或 ``records`` 作为列表键；适配层统一这些形状，找不到
    列表时由调用方保留聚合证据并标记为 ``unmapped``。
    """

    candidate: Any = value
    if isinstance(candidate, Mapping):
        for key in ("results", "samples", "details", "records", "items"):
            if isinstance(candidate.get(key), list):
                candidate = candidate[key]
                break
        else:
            nested = candidate.get("result")
            if nested is not candidate:
                return extract_evalscope_sample_results(nested)
            return []
    if not isinstance(candidate, list):
        return []
    normalized: list[dict[str, Any]] = []
    for position, item in enumerate(candidate):
        if isinstance(item, Mapping):
            record = dict(item)
        else:
            record = {"value": item}
        raw_index = record.get("sample_index", record.get("index"))
        try:
            record["sample_index"] = int(raw_index) if raw_index is not None else position
        except (TypeError, ValueError):
            record["sample_index"] = position
        normalized.append(record)
    return normalized


@dataclass(frozen=True)
class EvalScopeEngine:
    """EvalScope 1.x adapter。

    ``run_task`` 和 ``TaskConfig`` 通过构造器注入，既便于单元测试，也避免
    在未安装 EvalScope 的管理 API 进程启动时失败。
    """

    runner: Any | None = None
    config_type: Any | None = None
    ssrf_target: ResolvedTarget | None = None
    verify: bool = True

    def _resolve(self) -> tuple[Any, Any]:
        if self.runner is not None and self.config_type is not None:
            return self.runner, self.config_type
        try:
            from evalscope import TaskConfig, run_task
        except (ImportError, ModuleNotFoundError) as exc:
            raise EvaluationEngineUnavailable(
                "EvalScope 未安装，请安装 evalplat[evalscope] 后再执行质量评测"
            ) from exc
        return run_task, TaskConfig

    def run(self, config: Mapping[str, Any]) -> Any:
        runner, config_type = self._resolve()
        task_config = config if isinstance(config, config_type) else config_type(**dict(config))
        # Some lightweight injected config classes only record constructor
        # values instead of exposing them as attributes.  Keep the runner
        # contract stable without changing normal SDK/Pydantic instances.
        if not isinstance(task_config, Mapping):
            for key, value in config.items():
                if hasattr(task_config, key):
                    continue
                try:
                    setattr(task_config, key, value)
                except (AttributeError, TypeError):
                    pass
        if self.ssrf_target is None:
            return runner(task_config)
        with _pinned_evalscope_clients(self.ssrf_target, verify=self.verify):
            return runner(task_config)


def _evalscope_process_entry(
    kind: str,
    config: dict[str, Any],
    target: ResolvedTarget | None,
    verify: bool,
    output: Any,
) -> None:
    """Run one EvalScope invocation in a killable child process.

    EvalScope exposes a synchronous public API and does not provide a reliable
    cancellation callback. A process boundary lets the parent stop the SDK
    and its HTTP loop within the platform's five-second cancellation budget.
    """

    try:
        if kind == "performance":
            adapter: Any = EvalScopePerformanceEngine(ssrf_target=target, verify=verify)
        else:
            adapter = EvalScopeEngine(ssrf_target=target, verify=verify)
        output.put({"ok": True, "result": adapter.run(config)})
    except EvaluationEngineUnavailable as exc:
        output.put({"ok": True, "result": {"status": "unavailable", "error": redact_sensitive_text(str(exc))}})
    except Exception as exc:
        output.put({"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:500]}"})


def _terminate_evalscope_process(process: Any) -> None:
    """Terminate a child and bound cleanup even if the SDK is stuck."""

    if not process.is_alive():
        process.join(timeout=0.2)
        return
    process.terminate()
    process.join(timeout=5.0)
    if process.is_alive() and hasattr(process, "kill"):
        process.kill()
        process.join(timeout=1.0)


def _run_evalscope_in_process(
    kind: str,
    config: dict[str, Any],
    *,
    target: ResolvedTarget | None,
    verify: bool,
    should_cancel: Any,
) -> dict[str, Any]:
    """Execute EvalScope with cooperative parent-side cancellation polling."""

    context = get_context("spawn")
    output = context.Queue(maxsize=1)
    process = context.Process(
        target=_evalscope_process_entry,
        args=(kind, config, target, verify, output),
        name=f"evalscope-{kind}",
        daemon=True,
    )
    try:
        process.start()
    except Exception as exc:
        output.close()
        raise EvaluationEngineUnavailable(f"无法启动 EvalScope 隔离进程: {exc}") from exc

    try:
        while True:
            if should_cancel is not None:
                try:
                    if should_cancel():
                        _terminate_evalscope_process(process)
                        cancelled: dict[str, Any] = {
                            "status": "cancelled",
                            "engine": "evalscope",
                            "kind": kind,
                        }
                        if kind == "performance":
                            # EvalScope writes each completed request before
                            # the process exits. Recover only this Run's
                            # namespaced output after termination so a cancel
                            # preserves completed evidence without mixing a
                            # shared work root with another Run.
                            output_dir = config.get("outputs_dir")
                            run_name = config.get("name")
                            if output_dir and run_name:
                                result_root = (
                                    Path(str(output_dir)).expanduser().resolve()
                                    / _safe_perf_name(run_name)
                                )
                                try:
                                    paths = _find_evalscope_perf_databases(result_root)
                                    cancelled["result_db_paths"] = [str(path) for path in paths]
                                    cancelled["records"] = extract_evalscope_perf_records(paths)
                                except (OSError, sqlite3.Error, ValueError, EvaluationEngineConfigurationError):
                                    cancelled["records"] = []
                        return cancelled
                except Exception:
                    # A transient DB/session error must not silently cancel a
                    # running benchmark; the normal engine result remains the
                    # source of truth.
                    pass
            if not process.is_alive():
                process.join(timeout=0.2)
                try:
                    payload = output.get(timeout=0.5)
                except Empty:
                    return {
                        "status": "failed",
                        "engine": "evalscope",
                        "kind": kind,
                        "error": f"EvalScope 子进程异常退出 (exitcode={process.exitcode})",
                    }
                if payload.get("ok"):
                    return payload.get("result") if isinstance(payload.get("result"), dict) else {"result": payload.get("result")}
                return {
                    "status": "failed",
                    "engine": "evalscope",
                    "kind": kind,
                    "error": redact_sensitive_text(str(payload.get("error") or "EvalScope 子进程失败")[:500]),
                }
            time.sleep(0.1)
    finally:
        if process.is_alive():
            _terminate_evalscope_process(process)
        output.close()


@contextmanager
def _pinned_evalscope_clients(target: ResolvedTarget, *, verify: bool):
    """Inject SSRF-safe sync/async clients into EvalScope's OpenAI adapter.

    EvalScope constructs both ``OpenAI`` and ``AsyncOpenAI`` from the same
    ``model_args`` mapping, which cannot carry two different client objects.
    The short-lived patch keeps that third-party detail inside this adapter,
    while the actual clients use the platform's validated DNS result and never
    follow redirects. ModelCache is disabled for the same scope so a client
    pinned to one Run cannot leak into another Run in a long-lived worker.
    """

    try:
        import httpx
        import evalscope.api.model.model as model_module
        import evalscope.models.openai_compatible as openai_module
    except (ImportError, ModuleNotFoundError) as exc:
        raise EvaluationEngineUnavailable(
            "EvalScope 的 OpenAI transport 依赖不可用，无法建立安全网络边界"
        ) from exc

    original_openai = openai_module.OpenAI
    original_async_openai = openai_module.AsyncOpenAI
    original_cache_get = model_module.ModelCache.get
    original_cache_set = model_module.ModelCache.set
    sync_clients: list[Any] = []
    async_clients: list[Any] = []

    def new_sync_client() -> Any:
        return httpx.Client(
            transport=PinnedHTTPTransport(target, verify=verify),
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(connect=15.0, read=1800.0, write=30.0, pool=30.0),
        )

    def new_async_client() -> Any:
        return httpx.AsyncClient(
            transport=PinnedAsyncHTTPTransport(target, verify=verify),
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(connect=15.0, read=1800.0, write=30.0, pool=30.0),
        )

    class PinnedOpenAI(original_openai):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["http_client"] = new_sync_client()
            kwargs.setdefault("max_retries", 0)
            super().__init__(*args, **kwargs)
            sync_clients.append(self)

    class PinnedAsyncOpenAI(original_async_openai):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["http_client"] = new_async_client()
            kwargs.setdefault("max_retries", 0)
            super().__init__(*args, **kwargs)
            async_clients.append(self)

    # Assigning classmethod descriptors explicitly keeps the original API
    # shape while forcing a fresh model/client for each EvalScope invocation.
    model_module.ModelCache.get = classmethod(lambda cls, _key: None)
    model_module.ModelCache.set = classmethod(lambda cls, _key, _model: None)
    openai_module.OpenAI = PinnedOpenAI
    openai_module.AsyncOpenAI = PinnedAsyncOpenAI
    try:
        yield
    finally:
        openai_module.OpenAI = original_openai
        openai_module.AsyncOpenAI = original_async_openai
        model_module.ModelCache.get = original_cache_get
        model_module.ModelCache.set = original_cache_set
        for client in sync_clients:
            try:
                client.close()
            except Exception:
                pass
        for client in async_clients:
            try:
                asyncio.run(client.close())
            except Exception:
                # EvalScope may have already closed an event-loop-bound client.
                pass


def _api_base_url(base_url: str) -> str:
    """将平台连接地址规范化为 OpenAI-compatible API base URL。"""

    normalized = base_url.strip().rstrip("/")
    if not normalized:
        raise EvaluationEngineConfigurationError("连接 base_url 不能为空")
    if normalized.endswith("/v1"):
        return normalized
    return f"{normalized}/v1"


def build_evalscope_config(
    snapshot: Mapping[str, Any],
    *,
    api_key: str,
    work_dir: str | Path,
) -> dict[str, Any]:
    """把不可变 Run 快照映射为 EvalScope ``TaskConfig`` 输入。

    ``evalscope_datasets`` 是平台数据集适配层写入快照的稳定字段；不直接把
    数据库 UUID 当作 EvalScope benchmark 名称，避免引擎收到不可解析的配置。
    """

    model_id = str(snapshot.get("model_id") or "").strip()
    base_url = str(snapshot.get("base_url") or "").strip()
    datasets = snapshot.get("evalscope_datasets")
    if not model_id:
        raise EvaluationEngineConfigurationError("Run 快照缺少 model_id")
    if not base_url:
        raise EvaluationEngineConfigurationError("Run 快照缺少 base_url")
    if not isinstance(datasets, list) or not datasets or not all(isinstance(item, str) and item.strip() for item in datasets):
        raise EvaluationEngineConfigurationError(
            "Run 快照缺少 evalscope_datasets，无法执行质量评测"
        )

    generation = dict(snapshot.get("generation_params") or {})
    generation.setdefault("stream", bool(snapshot.get("stream", False)))
    config: dict[str, Any] = {
        # EvalScope uses ``model`` to construct the ModelAPI. ``model_id`` is
        # only the result/report identity and is not sufficient on its own;
        # omitting it makes TaskConfig fall back to mock_llm.
        "model": model_id,
        "model_id": model_id,
        "api_url": _api_base_url(base_url),
        "api_key": api_key,
        "eval_type": "openai_api",
        "datasets": [item.strip() for item in datasets],
        "generation_config": generation,
        "work_dir": str(Path(work_dir).expanduser()),
        "eval_batch_size": int(snapshot.get("eval_batch_size", 1) or 1),
    }
    limits = snapshot.get("limits") if isinstance(snapshot.get("limits"), Mapping) else {}
    max_total_tokens = limits.get("max_total_tokens")
    if max_total_tokens is not None:
        raise EvaluationEngineConfigurationError(
            "EvalScope 任务无法安全估算并逐请求执行 max_total_tokens"
        )
    unsupported_limits = [
        key for key in _EVALSCOPE_CONNECTION_LIMIT_KEYS
        if limits.get(key) is not None
    ]
    if unsupported_limits:
        raise EvaluationEngineConfigurationError(
            "EvalScope 任务暂不支持连接级限流，"
            f"请移除: {', '.join(unsupported_limits)}"
        )
    max_requests = limits.get("max_requests", limits.get("max_request_count"))
    requested_count = snapshot.get("request_count")
    caps: list[int] = []
    for raw_cap, field_name in (
        (max_requests, "max_requests"),
        (requested_count, "request_count"),
    ):
        if raw_cap is None:
            continue
        try:
            cap = int(raw_cap)
        except (TypeError, ValueError) as exc:
            raise EvaluationEngineConfigurationError(f"{field_name} 必须为正整数") from exc
        if cap <= 0:
            raise EvaluationEngineConfigurationError(f"{field_name} 必须为正整数")
        caps.append(cap)
    if caps:
        effective_cap = min(caps)
        # TaskConfig.limit applies independently to every selected benchmark.
        # Divide the cap conservatively so multiple datasets cannot exceed it.
        if effective_cap < len(datasets):
            raise EvaluationEngineConfigurationError(
                "request_count/max_requests 小于 EvalScope benchmark 数量，无法建立安全上限"
            )
        config["limit"] = effective_cap // len(datasets)
    if snapshot.get("dataset_args"):
        config["dataset_args"] = dict(snapshot["dataset_args"])
    return config


def run_evalscope(
    snapshot: Mapping[str, Any],
    *,
    api_key: str,
    work_dir: str | Path,
    engine: EvaluationEngine | None = None,
    ssrf_target: ResolvedTarget | None = None,
    verify: bool = True,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """执行 EvalScope，并将缺失依赖转换为明确的 unavailable 结果。

    Real EvalScope invocations use a process boundary when a cancellation
    callback is supplied. Injected engines remain in-process for deterministic
    tests and may opt into the same contract with ``run_cancellable``.
    """

    config = build_evalscope_config(snapshot, api_key=api_key, work_dir=work_dir)
    selected = engine or EvalScopeEngine(ssrf_target=ssrf_target, verify=verify)
    try:
        if should_cancel is not None and engine is None:
            result = _run_evalscope_in_process(
                "quality",
                config,
                target=ssrf_target,
                verify=verify,
                should_cancel=should_cancel,
            )
        elif should_cancel is not None and hasattr(selected, "run_cancellable"):
            result = selected.run_cancellable(config, should_cancel)
        else:
            result = selected.run(config)
    except EvaluationEngineUnavailable as exc:
        return {
            "status": "unavailable",
            "engine": "evalscope",
            "error": redact_sensitive_text(str(exc), api_key),
        }
    if isinstance(result, Mapping) and result.get("status") in {"cancelled", "unavailable", "failed"}:
        return {
            "status": result.get("status"),
            "engine": "evalscope",
            "error": result.get("error"),
            "result": dict(result),
        }
    return {"status": "completed", "engine": "evalscope", "result": result}


# ---------------------------------------------------------------------------
# EvalScope performance adapter
# ---------------------------------------------------------------------------


_PERF_RESULT_COLUMNS = {
    "request",
    "start_time",
    "inter_token_latencies",
    "success",
    "response_messages",
    "completed_time",
    "latency",
    "first_chunk_latency",
    "prompt_tokens",
    "completion_tokens",
    "time_per_output_token",
    "request_id",
    "is_stream",
    "status_code",
    "error",
}
_PERF_PATCH_LOCK = threading.RLock()
_KNOWN_CHAT_ENDPOINTS = ("/chat/completions", "/completions")
_GENERATION_KEYS = {
    "frequency_penalty",
    "repetition_penalty",
    "logprobs",
    "n_choices",
    "seed",
    "stop",
    "stop_token_ids",
    "temperature",
    "top_p",
    "top_k",
    "min_tokens",
    "max_tokens",
}
_EVALSCOPE_CONNECTION_LIMIT_KEYS = (
    "max_concurrency",
    "concurrency",
    "rps",
    "max_rps",
    "rpm",
    "max_rpm",
    "tpm",
    "max_tpm",
)


def _perf_api_url(base_url: str) -> str:
    """Normalize a connection URL for EvalScope's ``--url`` argument.

    ``Arguments`` appends ``/chat/completions`` to a base URL.  Preserve an
    explicitly supplied OpenAI endpoint so a legacy connection snapshot does
    not become ``.../chat/completions/v1/chat/completions``.
    """

    normalized = base_url.strip().rstrip("/")
    if not normalized:
        raise EvaluationEngineConfigurationError("连接 base_url 不能为空")
    if any(normalized.endswith(endpoint) for endpoint in _KNOWN_CHAT_ENDPOINTS):
        return normalized
    if normalized.endswith("/v1"):
        return normalized
    return f"{normalized}/v1"


def _safe_perf_name(value: Any) -> str:
    """Return a path component that cannot escape the EvalScope work dir."""

    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "evalscope-perf"))
    value = value.strip("._")
    return value[:96] or "evalscope-perf"


def _positive_number(value: Any, field: str, *, integer: bool = False) -> int | float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise EvaluationEngineConfigurationError(f"{field} 必须是正数") from exc
    if parsed <= 0:
        raise EvaluationEngineConfigurationError(f"{field} 必须大于 0")
    if integer:
        if not parsed.is_integer():
            raise EvaluationEngineConfigurationError(f"{field} 必须是整数")
        return int(parsed)
    return parsed


def _write_perf_messages(work_dir: Path, messages: Any) -> Path:
    """Materialize one immutable JSONL sample for EvalScope ``line_by_line``."""

    if not isinstance(messages, list) or not messages:
        raise EvaluationEngineConfigurationError("性能任务缺少非空 messages")
    if not all(isinstance(item, Mapping) for item in messages):
        raise EvaluationEngineConfigurationError("性能任务 messages 必须是消息对象列表")
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / "input_messages.jsonl"
    # A single JSON-array line is understood by EvalScope's line_by_line plugin
    # as an OpenAI messages payload.  The benchmark repeats it for number=N.
    path.write_text(json.dumps(messages, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    return path


def build_evalscope_perf_config(
    snapshot: Mapping[str, Any],
    *,
    api_key: str,
    work_dir: str | Path,
    dataset_root: str | Path | None = None,
) -> dict[str, Any]:
    """Map an immutable Run snapshot to EvalScope ``perf.Arguments`` input.

    Only fields understood by EvalScope are emitted.  Platform-only values
    (limits, credential metadata, and the immutable snapshots themselves) stay
    in the Run snapshot and are never forwarded to the provider.
    """

    model_id = str(snapshot.get("model_id") or "").strip()
    base_url = str(snapshot.get("base_url") or "").strip()
    if not model_id:
        raise EvaluationEngineConfigurationError("Run 快照缺少 model_id")
    if not base_url:
        raise EvaluationEngineConfigurationError("Run 快照缺少 base_url")

    work_root = Path(work_dir).expanduser().resolve()
    run_name = _safe_perf_name(snapshot.get("run_id") or model_id)
    run_work_root = work_root / run_name
    dataset_root_path = Path(
        dataset_root if dataset_root is not None else get_settings().dataset_dir
    ).expanduser().resolve()
    dataset_snapshot = snapshot.get("dataset_snapshot")
    dataset_path = None
    if isinstance(dataset_snapshot, Mapping):
        raw_path = dataset_snapshot.get("storage_path")
        if raw_path:
            dataset_path = _resolve_perf_dataset_path(raw_path, dataset_root_path)
    if dataset_path is None and snapshot.get("dataset_path"):
        dataset_path = _resolve_perf_dataset_path(snapshot["dataset_path"], dataset_root_path)
    if dataset_path is None:
        # Match the native executor's smoke-test fallback when a draft did not
        # select a dataset or explicit messages.
        dataset_path = _write_perf_messages(
            run_work_root,
            snapshot.get("messages") or [{"role": "user", "content": "ping"}],
        )
    if not dataset_path.is_file():
        raise EvaluationEngineConfigurationError(f"性能数据集文件不存在: {dataset_path.name}")

    request_count = _positive_number(snapshot.get("request_count", 1), "request_count", integer=True)
    warmup_count = snapshot.get("warmup_count", 0)
    try:
        warmup_count = int(warmup_count or 0)
    except (TypeError, ValueError) as exc:
        raise EvaluationEngineConfigurationError("warmup_count 必须是非负整数") from exc
    if warmup_count < 0:
        raise EvaluationEngineConfigurationError("warmup_count 必须是非负整数")

    profile = snapshot.get("load_profile") or {"mode": "fixed_concurrency", "concurrency": 1}
    if not isinstance(profile, Mapping):
        raise EvaluationEngineConfigurationError("load_profile 必须是对象")
    mode = str(profile.get("mode") or "fixed_concurrency")
    if mode == "fixed_concurrency":
        parallel = _positive_number(profile.get("concurrency", 1), "concurrency", integer=True)
        open_loop = False
        rate = -1
        max_inflight = None
    elif mode == "fixed_rps":
        rate = _positive_number(profile.get("rps"), "rps")
        max_inflight = _positive_number(profile.get("max_inflight"), "max_inflight", integer=True)
        if max_inflight is None:
            raise EvaluationEngineConfigurationError("固定 RPS 必须设置 max_inflight")
        # EvalScope turns open-loop ``parallel`` into INF internally.  Keep the
        # platform cap in this private adapter and enforce it in the patched
        # strategy below.
        parallel = max_inflight
        open_loop = True
    elif mode == "staircase":
        raise EvaluationEngineConfigurationError(
            "EvalScope perf 单次 API 不支持 staircase；需由编排层按 stage 拆分 Run"
        )
    else:
        raise EvaluationEngineConfigurationError(f"不支持的负载模式: {mode}")

    generation = snapshot.get("generation_params") or {}
    if not isinstance(generation, Mapping):
        raise EvaluationEngineConfigurationError("generation_params 必须是对象")
    config: dict[str, Any] = {
        "model": model_id,
        "model_id": model_id,
        "api": "openai",
        "url": _perf_api_url(base_url),
        "api_key": api_key,
        "dataset": "line_by_line",
        "dataset_path": str(dataset_path),
        "dataset_args": dict(snapshot.get("dataset_args") or {}),
        "number": int(request_count),
        "parallel": int(parallel),
        "rate": float(rate),
        "open_loop": open_loop,
        "warmup_num": warmup_count,
        "stream": bool(snapshot.get("stream", False)),
        # Connection precheck already ran at task submission.  Avoid the
        # EvalScope perf connection probe, which otherwise creates an extra
        # provider request outside the platform's evidence model.
        "no_test_connection": True,
        "outputs_dir": str(work_root),
        "name": run_name,
        "no_timestamp": True,
    }
    limits = snapshot.get("limits") if isinstance(snapshot.get("limits"), Mapping) else {}
    max_total_tokens = limits.get("max_total_tokens")
    if max_total_tokens is not None:
        raise EvaluationEngineConfigurationError(
            "EvalScope 性能任务无法安全估算并逐请求执行 max_total_tokens"
        )
    unsupported_limits = [
        key for key in _EVALSCOPE_CONNECTION_LIMIT_KEYS
        if limits.get(key) is not None
    ]
    if unsupported_limits:
        # EvalScope owns the request loop in a child process. The platform's
        # database-backed limiter cannot observe each request, so admitting
        # the Run would make connection quotas only advisory. Reject it before
        # any provider traffic is possible.
        raise EvaluationEngineConfigurationError(
            "EvalScope 任务暂不支持连接级限流，"
            f"请移除: {', '.join(unsupported_limits)}"
        )
    max_requests = limits.get("max_requests", limits.get("max_request_count"))
    if max_requests is not None:
        try:
            max_requests = int(max_requests)
        except (TypeError, ValueError) as exc:
            raise EvaluationEngineConfigurationError("max_requests 必须为正整数") from exc
        if max_requests <= 0:
            raise EvaluationEngineConfigurationError("max_requests 必须为正整数")
        # ``number`` excludes warmup in EvalScope. Count both phases so the
        # platform's hard request cap cannot be exceeded by the SDK.
        planned_requests = int(request_count) + warmup_count
        if planned_requests > max_requests:
            raise EvaluationEngineConfigurationError(
                "EvalScope 性能任务计划请求数超过 max_requests"
            )
    for key, value in generation.items():
        if key == "max_completion_tokens" and "max_tokens" not in generation:
            config["max_tokens"] = value
        elif key in _GENERATION_KEYS:
            config[key] = value
    extra = dict(generation.get("extra_args") or {}) if isinstance(generation.get("extra_args"), Mapping) else {}
    for key, value in generation.items():
        if key not in _GENERATION_KEYS and key != "max_completion_tokens" and key != "extra_args":
            extra.setdefault(str(key), value)
    if extra:
        config["extra_args"] = extra

    timeouts = snapshot.get("timeouts") or {}
    if not isinstance(timeouts, Mapping):
        raise EvaluationEngineConfigurationError("timeouts 必须是对象")
    for source, target in (("connect", "connect_timeout"), ("read", "read_timeout"), ("total", "total_timeout")):
        value = _positive_number(timeouts.get(source), source + " timeout")
        if value is not None:
            # EvalScope's fields are integer typed; ceil avoids silently
            # shortening a fractional platform timeout.
            import math

            config[target] = int(math.ceil(float(value)))
    if mode == "fixed_rps":
        config["duration"] = profile.get("duration_seconds")
    return config


def _resolve_perf_dataset_path(raw_path: Any, dataset_root: Path) -> Path:
    """Resolve a snapshot dataset path while keeping it below ``dataset_root``."""

    candidate = Path(str(raw_path)).expanduser()
    if not candidate.is_absolute():
        candidate = dataset_root / candidate
    try:
        candidate = candidate.resolve(strict=False)
        candidate.relative_to(dataset_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise EvaluationEngineConfigurationError(
            "性能数据集文件路径不在配置的 dataset_dir 内"
        ) from exc
    return candidate


class _PinnedResolver:
    """Minimal aiohttp resolver backed only by an SSRF-validated address set."""

    def __init__(self, target: ResolvedTarget):
        self._target = target

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_UNSPEC) -> list[dict[str, Any]]:
        normalized = str(host).lower().rstrip(".")
        if normalized != self._target.hostname or int(port) != self._target.port:
            raise OSError("请求目标与已校验的 SSRF 目标不一致")
        result = []
        for address in self._target.addresses:
            try:
                parsed = ipaddress.ip_address(address)
            except ValueError as exc:
                raise OSError("SSRF 目标包含无效地址") from exc
            addr_family = socket.AF_INET6 if parsed.version == 6 else socket.AF_INET
            if family not in (socket.AF_UNSPEC, addr_family):
                continue
            result.append({
                "hostname": self._target.hostname,
                "host": address,
                "port": self._target.port,
                "family": addr_family,
                "proto": 0,
                "flags": 0,
            })
        if not result:
            raise OSError("没有可用的已校验 SSRF 地址")
        return result

    async def close(self) -> None:
        return None


def _make_pinned_perf_client(original_client: Any, target: ResolvedTarget, *, verify: bool) -> Any:
    """Create an EvalScope AioHttpClient using pinned DNS and no redirects."""

    import aiohttp

    class NoRedirectSession(aiohttp.ClientSession):
        async def _request(self, method: str, url: Any, *args: Any, **kwargs: Any) -> Any:
            kwargs["allow_redirects"] = False
            kwargs.pop("proxy", None)
            return await super()._request(method, url, *args, **kwargs)

    class PinnedAioHttpClient(original_client):
        def __init__(self, args: Any, api_plugin: Any):
            self.url = args.url
            self.headers = {
                "user-agent": "modelscope_bench",
                **self._get_runtime_headers(args.headers or {}),
            }
            self.total_timeout = args.total_timeout
            self.read_timeout = args.read_timeout
            self.connect_timeout = args.connect_timeout
            self.api_plugin = api_plugin
            try:
                limit = int(args.parallel)
            except (TypeError, ValueError):
                limit = 0
            limit = 0 if limit <= 0 else limit
            connector = aiohttp.TCPConnector(
                limit=limit,
                limit_per_host=limit,
                ttl_dns_cache=0,
                use_dns_cache=False,
                keepalive_timeout=60,
                enable_cleanup_closed=True,
                force_close=False,
                ssl=(verify if self.url.lower().startswith("https://") else False),
                resolver=_PinnedResolver(target),
            )
            timeout = aiohttp.ClientTimeout(
                total=self.total_timeout,
                connect=self.connect_timeout,
                sock_read=self.read_timeout,
            )
            self.client = NoRedirectSession(
                connector=connector,
                trust_env=False,
                timeout=timeout,
            )

    return PinnedAioHttpClient


def _make_capped_open_loop_strategy(base_strategy: Any, send_request: Any, max_inflight: int) -> Any:
    """Build an EvalScope strategy that records client-side scheduling drops."""

    def _new_strategy():
        import asyncio
        import json as _json
        import time as _time

        from evalscope.perf.utils.benchmark_util import BenchmarkData

        class CappedOpenLoopStrategy(base_strategy):
            async def _run_phase(self, requests: list[dict], is_warmup: bool, deadline: float | None = None) -> None:
                in_flight: set[asyncio.Task[Any]] = set()
                rate = float(self.args.rate if not isinstance(self.args.rate, list) else self.args.rate[0])
                anchor = _time.perf_counter()
                for index, request in enumerate(requests):
                    if rate > 0:
                        delay = anchor + ((index + 1) / rate) - _time.perf_counter()
                        if deadline is not None:
                            delay = min(delay, deadline - _time.perf_counter())
                        if delay > 0:
                            await asyncio.sleep(delay)
                    if deadline is not None and _time.perf_counter() >= deadline and not is_warmup:
                        break
                    done, pending = await asyncio.wait(in_flight, timeout=0)
                    in_flight = set(pending)
                    if done:
                        await asyncio.gather(*done, return_exceptions=True)
                    if len(in_flight) >= max_inflight:
                        if is_warmup:
                            done, pending = await asyncio.wait(in_flight, return_when=asyncio.FIRST_COMPLETED)
                            in_flight = set(pending)
                            await asyncio.gather(*done, return_exceptions=True)
                        else:
                            now = _time.perf_counter()
                            dropped = BenchmarkData(
                                request=_json.dumps(request, ensure_ascii=False),
                                start_time=now,
                                completed_time=now,
                                query_latency=0.0,
                                first_chunk_latency=0.0,
                                success=False,
                                is_stream=bool(request.get("stream", False)),
                                error="max_inflight exceeded at scheduled time",
                            )
                            await self.queue.put(dropped)
                            continue
                    in_flight.add(asyncio.create_task(send_request(request, is_warmup, self.queue, self.client, self.track_gpu_memory)))
                if in_flight:
                    await asyncio.gather(*in_flight, return_exceptions=True)

        return CappedOpenLoopStrategy

    return _new_strategy()


@contextmanager
def _pinned_evalscope_perf_clients(
    target: ResolvedTarget,
    *,
    verify: bool,
    max_inflight: int | None,
):
    """Patch EvalScope perf's network boundary for one serialized invocation."""

    try:
        import evalscope.perf.benchmark as benchmark_module
        import evalscope.perf.core.http_client as http_module
        import evalscope.perf.core.strategies.open_loop as open_loop_module
    except (ImportError, ModuleNotFoundError) as exc:
        raise EvaluationEngineUnavailable(
            "EvalScope perf 依赖不可用，无法建立安全网络边界"
        ) from exc

    with _PERF_PATCH_LOCK:
        original_client = http_module.AioHttpClient
        original_benchmark_client = benchmark_module.AioHttpClient
        original_strategy = open_loop_module.OpenLoopStrategy
        pinned_client = _make_pinned_perf_client(original_client, target, verify=verify)
        http_module.AioHttpClient = pinned_client
        benchmark_module.AioHttpClient = pinned_client
        if max_inflight is not None:
            capped = _make_capped_open_loop_strategy(
                original_strategy,
                open_loop_module._send_request_open_loop,
                max_inflight,
            )
            open_loop_module.OpenLoopStrategy = capped
            benchmark_module.OpenLoopStrategy = capped
        try:
            yield
        finally:
            http_module.AioHttpClient = original_client
            benchmark_module.AioHttpClient = original_benchmark_client
            open_loop_module.OpenLoopStrategy = original_strategy


class _SafeUnpickler(pickle.Unpickler):
    """Decode EvalScope's response pickle without importing arbitrary classes."""

    _ALLOWED = {
        ("builtins", name)
        for name in ("bool", "bytes", "dict", "float", "int", "list", "set", "str", "tuple")
    } | {("builtins", "NoneType")}

    def find_class(self, module: str, name: str) -> Any:
        if (module, name) not in self._ALLOWED:
            raise ValueError("EvalScope response pickle contains a disallowed type")
        return super().find_class(module, name)


def _decode_evalscope_pickle(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return None
    try:
        payload = base64.b64decode(value.encode("ascii"), validate=True)
        return _SafeUnpickler(__import__("io").BytesIO(payload)).load()
    except (ValueError, TypeError, UnicodeError, pickle.PickleError, EOFError):
        return None


def _json_value(value: Any) -> Any:
    """Return a JSON-safe bounded representation for persisted evidence."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(v) for v in value]
    return str(value)


def _decode_response_messages(value: Any) -> list[Any] | None:
    decoded = _decode_evalscope_pickle(value)
    if not isinstance(decoded, list):
        return None
    result = []
    for item in decoded:
        if isinstance(item, str):
            try:
                result.append(_json_value(json.loads(item)))
                continue
            except (TypeError, ValueError):
                pass
        result.append(_json_value(item))
    return result


def map_evalscope_perf_record(row: Mapping[str, Any], *, sample_index: int) -> dict[str, Any]:
    """Map one EvalScope SQLite result row to platform Request/Attempt evidence.

    EvalScope stores monotonic timestamps and serializes response chunks with a
    base64/pickle envelope.  The mapped record intentionally retains both the
    monotonic source values and normalized milliseconds; the worker can assign
    UTC persistence timestamps when inserting Request/Attempt rows.
    """

    def _float(key: str) -> float | None:
        try:
            value = row.get(key)
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    success = bool(row.get("success"))
    status_code = row.get("status_code")
    try:
        status_code = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        status_code = None
    latency = _float("latency")
    ttft = _float("first_chunk_latency")
    tpot = _float("time_per_output_token")
    try:
        itl = json.loads(row.get("inter_token_latencies") or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        itl = []
    if not isinstance(itl, list):
        itl = []
    itl = [float(value) for value in itl if isinstance(value, (int, float))]
    request_body: Any
    try:
        request_body = json.loads(row.get("request") or "null")
    except (TypeError, ValueError, json.JSONDecodeError):
        request_body = row.get("request")
    response_messages = _decode_response_messages(row.get("response_messages"))
    raw_response = json.dumps(response_messages, ensure_ascii=False, separators=(",", ":")) if response_messages is not None else str(row.get("response_messages") or "")
    raw_response = raw_response[:1_000_000]
    error = redact_sensitive_text(str(row.get("error") or "")[:500]) or None
    if success:
        error_category = None
        status = "completed"
    elif error and "max_inflight exceeded" in error:
        error_category = "schedule_dropped"
        status = "failed"
    elif status_code == 429:
        error_category = "rate_limit"
        status = "failed"
    elif status_code is not None and 500 <= status_code <= 599:
        error_category = "temporary_network"
        status = "failed"
    elif status_code is not None and 300 <= status_code <= 399:
        error_category = "redirect"
        status = "failed"
    elif error and "timeout" in error.lower():
        error_category = "timeout"
        status = "failed"
    else:
        error_category = "provider_error"
        status = "failed"
    prompt_tokens = row.get("prompt_tokens")
    completion_tokens = row.get("completion_tokens")
    try:
        prompt_tokens = int(prompt_tokens) if prompt_tokens is not None else None
    except (TypeError, ValueError):
        prompt_tokens = None
    try:
        completion_tokens = int(completion_tokens) if completion_tokens is not None else None
    except (TypeError, ValueError):
        completion_tokens = None
    usage = {
        key: value
        for key, value in {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": (prompt_tokens + completion_tokens) if prompt_tokens is not None and completion_tokens is not None else None,
        }.items()
        if value is not None
    }
    provider_request_id = None
    if response_messages:
        for message in response_messages:
            if isinstance(message, Mapping) and message.get("id"):
                provider_request_id = str(message["id"])
                break
    provider_request_id = provider_request_id or (str(row.get("request_id")) if row.get("request_id") else None)
    record: dict[str, Any] = {
        "sample_index": sample_index,
        "status": status,
        "success": success,
        "phase": "steady",
        "stream_mode": "stream" if bool(row.get("is_stream")) else "nonstream",
        "request_body": _json_value(request_body),
        "start_time_monotonic": _float("start_time"),
        "completed_time_monotonic": _float("completed_time"),
        "latency_ms": round(latency * 1000, 2) if latency is not None else None,
        "ttft_ms": round(ttft * 1000, 2) if ttft is not None and bool(row.get("is_stream")) else None,
        "tpot_ms": round(tpot * 1000, 2) if tpot is not None and bool(row.get("is_stream")) else None,
        "itl_series": {"seconds": itl, "milliseconds": [round(value * 1000, 4) for value in itl]},
        "vendor_usage": usage,
        "raw_response": raw_response,
        "extracted_fields": {"response_messages": response_messages} if response_messages is not None else {},
        "error_category": error_category,
        "error": error,
        "http_status": status_code,
        "vendor_request_id": provider_request_id,
    }
    if bool(row.get("is_stream")) and tpot and tpot > 0 and completion_tokens:
        record["avg_speed"] = round(1.0 / tpot, 4)
    else:
        record["avg_speed"] = None
    # EvalScope does not expose the platform's rolling speed fields directly;
    # derive conservative peaks from the recorded inter-token intervals.
    positive_itl = [value for value in itl if value > 0]
    record["peak_speed"] = round(1.0 / min(positive_itl), 4) if positive_itl else None
    record["peak_tps"] = record["peak_speed"]
    return record


def extract_evalscope_perf_records(paths: str | Path | list[str | Path]) -> list[dict[str, Any]]:
    """Read and normalize EvalScope ``benchmark_data.db`` result rows.

    The reader is read-only and validates the schema before querying.  It does
    not execute arbitrary pickle globals; malformed rows remain evidence with
    an opaque raw response instead of aborting the whole Run.
    """

    if isinstance(paths, (str, Path)):
        paths = [paths]
    records: list[dict[str, Any]] = []
    for raw_path in paths:
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            continue
        # Quote the absolute path before constructing a read-only SQLite URI;
        # a user-controlled ``?`` or ``#`` in a work-dir name must not change
        # the URI query and accidentally select a different file/mode.
        uri = f"file:{quote(path.as_posix())}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(result)")}
            if not _PERF_RESULT_COLUMNS.issubset(columns):
                raise EvaluationEngineConfigurationError(
                    f"EvalScope 结果数据库 schema 不兼容: {path.name}"
                )
            selected = ", ".join(sorted(_PERF_RESULT_COLUMNS))
            cursor = connection.execute(f"SELECT {selected} FROM result ORDER BY rowid")
            names = [item[0] for item in cursor.description]
            for row in cursor:
                records.append(map_evalscope_perf_record(dict(zip(names, row)), sample_index=len(records)))
    return records


def _find_evalscope_perf_databases(work_dir: str | Path) -> list[Path]:
    root = Path(work_dir).expanduser().resolve()
    if not root.exists():
        return []
    return sorted(root.rglob("benchmark_data.db"), key=lambda item: item.stat().st_mtime_ns)


@dataclass(frozen=True)
class EvalScopePerformanceEngine:
    """EvalScope perf adapter with a platform-safe network and evidence boundary."""

    runner: Any | None = None
    config_type: Any | None = None
    ssrf_target: ResolvedTarget | None = None
    verify: bool = True

    def _resolve(self) -> tuple[Any, Any]:
        if self.runner is not None:
            return self.runner, self.config_type
        try:
            from evalscope.perf.arguments import Arguments
            from evalscope.perf.main import run_perf_benchmark
        except (ImportError, ModuleNotFoundError) as exc:
            raise EvaluationEngineUnavailable(
                "EvalScope perf 未安装，请安装 evalplat[evalscope] 后再执行性能评测"
            ) from exc
        return run_perf_benchmark, Arguments

    def run(self, config: Mapping[str, Any]) -> dict[str, Any]:
        runner, config_type = self._resolve()
        args = config
        if config_type is not None and not isinstance(config, config_type):
            args = config_type(**dict(config))
        max_inflight = None
        if config.get("open_loop"):
            try:
                max_inflight = int(config.get("parallel"))
            except (TypeError, ValueError):
                max_inflight = None
        output_dir = config.get("outputs_dir")
        if self.ssrf_target is None and self.runner is None:
            raise EvaluationEngineConfigurationError(
                "EvalScope perf 必须提供已通过 SSRF 校验的 ssrf_target"
            )
        if self.runner is not None:
            result = runner(args)
        else:
            with _pinned_evalscope_perf_clients(
                self.ssrf_target, verify=self.verify, max_inflight=max_inflight
            ):
                result = runner(args)
        # ``run_perf_benchmark`` creates ``outputs_dir/name`` before its
        # per-sweep directories.  Restrict extraction to this Run-specific
        # subtree so a shared EvalScope root cannot mix records from older or
        # concurrent Runs.
        result_root = output_dir
        if output_dir and config.get("name"):
            result_root = str(Path(str(output_dir)).expanduser().resolve() / _safe_perf_name(config["name"]))
        paths = _find_evalscope_perf_databases(result_root) if result_root else []
        records = extract_evalscope_perf_records(paths)
        try:
            import evalscope

            version = getattr(evalscope, "__version__", None)
        except (ImportError, ModuleNotFoundError):
            version = None
        return {
            "status": "completed",
            "engine": "evalscope",
            "kind": "performance",
            "evalscope_version": version,
            "result": result,
            "result_db_paths": [str(path) for path in paths],
            "records": records,
        }


def run_evalscope_performance(
    snapshot: Mapping[str, Any],
    *,
    api_key: str,
    work_dir: str | Path,
    dataset_root: str | Path | None = None,
    engine: EvalScopePerformanceEngine | EvaluationEngine | None = None,
    ssrf_target: ResolvedTarget | None = None,
    verify: bool = True,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Build, execute, and normalize one EvalScope performance Run."""

    config = build_evalscope_perf_config(
        snapshot,
        api_key=api_key,
        work_dir=work_dir,
        dataset_root=dataset_root,
    )
    selected = engine or EvalScopePerformanceEngine(ssrf_target=ssrf_target, verify=verify)
    try:
        if should_cancel is not None and engine is None:
            result = _run_evalscope_in_process(
                "performance",
                config,
                target=ssrf_target,
                verify=verify,
                should_cancel=should_cancel,
            )
        elif should_cancel is not None and hasattr(selected, "run_cancellable"):
            result = selected.run_cancellable(config, should_cancel)
        else:
            result = selected.run(config)
    except EvaluationEngineUnavailable as exc:
        return {
            "status": "unavailable",
            "engine": "evalscope",
            "kind": "performance",
            "error": redact_sensitive_text(str(exc), api_key),
        }
    if isinstance(result, Mapping) and result.get("engine") == "evalscope" and result.get("kind") == "performance":
        return dict(result)
    if isinstance(result, Mapping) and result.get("status") in {"cancelled", "unavailable", "failed"}:
        return {
            "status": result.get("status"),
            "engine": "evalscope",
            "kind": "performance",
            "error": result.get("error"),
            "result": dict(result),
        }
    return {"status": "completed", "engine": "evalscope", "kind": "performance", "result": result}


# Verbose aliases keep the public boundary discoverable without duplicating
# implementation names used by the existing quality adapter.
build_evalscope_performance_config = build_evalscope_perf_config
run_evalscope_perf = run_evalscope_performance
