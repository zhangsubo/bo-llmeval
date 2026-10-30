"""Runtime hard budget for one claimed Run.

Precheck catches unsafe configurations before enqueue.  This object is the
execution-time backstop for legacy snapshots, inaccurate estimates, and
concurrent scheduler threads.  It intentionally contains no database access;
the worker owns evidence persistence while this class owns atomic admission.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time
from typing import Any


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


@dataclass
class RunBudget:
    """Thread-safe admission budget for requests sent by one Run."""

    max_requests: int | None = None
    max_total_tokens: int | None = None
    max_duration_seconds: float | None = None
    started_monotonic: float = field(default_factory=time.monotonic)
    used_requests: int = 0
    reserved_tokens: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any]) -> "RunBudget":
        limits = snapshot.get("limits") if isinstance(snapshot, dict) else None
        limits = limits if isinstance(limits, dict) else {}
        return cls(
            max_requests=_positive_int(limits.get("max_requests", limits.get("max_request_count"))),
            max_total_tokens=_positive_int(limits.get("max_total_tokens")),
            max_duration_seconds=_positive_float(limits.get("max_duration_seconds")),
        )

    @property
    def enabled(self) -> bool:
        return any(
            value is not None
            for value in (self.max_requests, self.max_total_tokens, self.max_duration_seconds)
        )

    def reserve(self, estimated_tokens: int | None = None) -> tuple[bool, str | None]:
        """Atomically reserve one provider request before it is sent."""

        with self._lock:
            if (
                self.max_duration_seconds is not None
                and time.monotonic() - self.started_monotonic >= self.max_duration_seconds
            ):
                return False, "max_duration_seconds exceeded"
            if self.max_requests is not None and self.used_requests >= self.max_requests:
                return False, "max_requests exceeded"
            if self.max_total_tokens is not None:
                if estimated_tokens is None:
                    return False, "max_total_tokens 无法在运行时安全估算"
                if estimated_tokens < 0:
                    return False, "请求 Token 估算无效"
                if self.reserved_tokens + estimated_tokens > self.max_total_tokens:
                    return False, "max_total_tokens exceeded"
            self.used_requests += 1
            if estimated_tokens is not None:
                self.reserved_tokens += estimated_tokens
            return True, None

    def expired(self) -> bool:
        """Return whether the duration budget has elapsed."""

        return bool(
            self.max_duration_seconds is not None
            and time.monotonic() - self.started_monotonic >= self.max_duration_seconds
        )
