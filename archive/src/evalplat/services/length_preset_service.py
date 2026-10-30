"""内置性能输入长度预设。

预设只描述稳定的业务口径；具体文本由 ``tokenizer_service`` 按已冻结的
Tokenizer 生成。这样任务服务不需要了解 tiktoken 或 Transformers 的实现细节。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from evalplat.models.tokenizer import Tokenizer
from evalplat.services.tokenizer_service import count_tokens, generate_token_text


@dataclass(frozen=True)
class LengthPreset:
    key: str
    label: str
    target_input_tokens: int
    max_output_tokens: int
    requires_all_targets: bool = False


LENGTH_PRESETS: tuple[LengthPreset, ...] = (
    LengthPreset("short", "短", 256, 128),
    LengthPreset("medium", "中", 2_048, 512),
    LengthPreset("long", "长", 8_192, 1_024),
    LengthPreset("extra_long", "超长", 32_768, 2_048, requires_all_targets=True),
)

_BY_KEY = {item.key: item for item in LENGTH_PRESETS}


def get_length_preset(value: object) -> LengthPreset | None:
    """校验并返回输入长度预设；空值表示使用显式 messages/数据集。"""

    if value is None or str(value).strip() == "":
        return None
    key = str(value).strip().lower()
    try:
        return _BY_KEY[key]
    except KeyError as exc:
        allowed = ", ".join(_BY_KEY)
        raise ValueError(f"不支持的输入长度预设: {value}，可选: {allowed}") from exc


def list_length_presets() -> list[dict[str, Any]]:
    """返回前端/模板选择器可直接消费的预设目录。"""

    return [asdict(item) for item in LENGTH_PRESETS]


def build_preset_messages(
    tokenizer: Tokenizer,
    preset: str | LengthPreset,
    *,
    sample_index: int = 0,
) -> list[dict[str, str]]:
    """生成接近目标 Token 数且不同样本内容不同的单轮 messages。

    生成内容使用冻结 Tokenizer 的 token 序列，而不是按字符长度估算。消息
    封装本身也会占用少量 Token，因此用单调搜索选择最接近目标的内容长度。

    ``count_tokens`` 无法加载 Tokenizer 时会明确失败，避免把字符数冒充为
    Token 数并破坏横向比较口径。
    """

    spec = preset if isinstance(preset, LengthPreset) else get_length_preset(preset)
    if spec is None:
        raise ValueError("必须指定输入长度预设")
    if tokenizer.status != "ready":
        raise ValueError("输入长度预设要求 Tokenizer 处于 ready 状态")

    target = spec.target_input_tokens
    best_messages: list[dict[str, str]] | None = None
    best_distance: int | None = None
    # Token boundaries can make one deterministic sequence skip the exact
    # target by a token. A few large, deterministic seed offsets provide a
    # different boundary while keeping every sample reproducible and unique.
    for variant in range(4):
        seed = sample_index + variant * 1_000_003
        low = max(1, target - 128)
        high = target + 32
        while low <= high:
            content_tokens = (low + high) // 2
            content = generate_token_text(tokenizer, content_tokens, seed=seed)
            messages = [{"role": "user", "content": content}]
            measured = count_tokens(tokenizer, messages)
            if measured is None:
                raise ValueError("Tokenizer 无法计算输入 Token，不能使用长度预设")
            distance = abs(measured - target)
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_messages = messages
            if measured == target:
                return messages
            if measured < target:
                low = content_tokens + 1
            else:
                high = content_tokens - 1

    if best_messages is None:
        raise ValueError("无法生成输入长度预设")
    return best_messages
