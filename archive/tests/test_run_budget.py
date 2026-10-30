from __future__ import annotations

import time

from evalplat.services.run_budget import RunBudget


def test_run_budget_reserves_requests_and_tokens_atomically():
    budget = RunBudget(max_requests=2, max_total_tokens=10)

    assert budget.reserve(6) == (True, None)
    assert budget.reserve(4) == (True, None)
    assert budget.reserve(1)[0] is False
    assert budget.used_requests == 2
    assert budget.reserved_tokens == 10


def test_run_budget_rejects_unknown_token_estimate_when_token_cap_is_set():
    budget = RunBudget(max_total_tokens=10)

    allowed, reason = budget.reserve(None)

    assert allowed is False
    assert "无法" in (reason or "")


def test_run_budget_expires_duration():
    budget = RunBudget(max_duration_seconds=1, started_monotonic=time.monotonic() - 2)

    assert budget.expired() is True
    assert budget.reserve(0)[0] is False
