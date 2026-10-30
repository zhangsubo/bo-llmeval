"""将模板负载配置编译为可复现的请求调度计划。"""

from dataclasses import dataclass
from math import ceil
from typing import Any


@dataclass(frozen=True)
class ScheduledRequest:
    index: int
    offset_seconds: float
    stage: int | None = None


def build_schedule(profile: dict[str, Any] | None, request_count: int) -> list[ScheduledRequest]:
    """生成从 0 秒开始的调度计划，不执行睡眠或网络操作。"""
    if request_count <= 0:
        raise ValueError("request_count 必须大于 0")
    config = profile or {"mode": "fixed_concurrency", "concurrency": 1}
    mode = config.get("mode")
    if mode == "fixed_concurrency":
        concurrency = int(config.get("concurrency", 0))
        if concurrency <= 0:
            raise ValueError("concurrency 必须大于 0")
        return [ScheduledRequest(i, float(i // concurrency)) for i in range(request_count)]
    if mode == "fixed_rps":
        rps = float(config.get("rps", 0))
        if rps <= 0:
            raise ValueError("rps 必须大于 0")
        interval = 1.0 / rps
        return [ScheduledRequest(i, round(i * interval, 6)) for i in range(request_count)]
    if mode == "staircase":
        stages = config.get("stages")
        if not isinstance(stages, list) or not stages:
            raise ValueError("stages 必须为非空列表")
        schedule: list[ScheduledRequest] = []
        index = 0
        offset = 0.0
        for stage_index, stage in enumerate(stages):
            if not isinstance(stage, dict):
                raise ValueError("每个 stage 必须为对象")
            rps = float(stage.get("rps", 0))
            duration = float(stage.get("duration_seconds", 0))
            if rps <= 0 or duration <= 0:
                raise ValueError("stage 的 rps 和 duration_seconds 必须大于 0")
            stage_count = min(request_count - index, max(1, ceil(rps * duration)))
            interval = 1.0 / rps
            for _ in range(stage_count):
                schedule.append(ScheduledRequest(index, round(offset, 6), stage_index))
                index += 1
                offset += interval
                if index >= request_count:
                    return schedule
        if index < request_count:
            raise ValueError("阶梯负载总容量不足以覆盖 request_count")
        return schedule
    raise ValueError(f"不支持的负载模式: {mode}")
