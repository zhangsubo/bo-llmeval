"""内置质量数据集的来源目录和远程 provider。

本模块只描述第三方数据集和把远程记录转换成平台统一的 JSON 记录；
版本生命周期、文件发布和数据库事务由 ``dataset_service`` 负责。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class BuiltinDatasetSpec:
    key: str
    name: str
    evalscope_name: str
    source_id: str
    source: str
    split: str
    subset: str
    license: str
    language: str
    capability_tags: tuple[str, ...]
    scoring_method: str
    description: str
    revision: str | None = None


# The IDs/splits/subsets follow EvalScope 1.11.1 benchmark metadata.  A
# license string is deliberately explicit about upstream terms when the hub
# does not publish a single SPDX identifier.
BUILTIN_DATASETS: tuple[BuiltinDatasetSpec, ...] = (
    BuiltinDatasetSpec(
        key="ifeval",
        name="IFEval",
        evalscope_name="ifeval",
        source_id="opencompass/ifeval",
        source="modelscope",
        split="train",
        subset="default",
        license="Apache-2.0",
        language="en",
        capability_tags=("instruction-following", "objective"),
        scoring_method="ifeval",
        description="可验证指令遵循能力评测。",
    ),
    BuiltinDatasetSpec(
        key="ceval",
        name="C-Eval",
        evalscope_name="ceval",
        source_id="evalscope/ceval",
        source="modelscope",
        split="val",
        subset="default",
        license="See upstream dataset terms",
        language="zh",
        capability_tags=("knowledge", "multiple-choice", "chinese"),
        scoring_method="multiple_choice",
        description="覆盖中文学科知识和推理的多项选择评测。",
    ),
    BuiltinDatasetSpec(
        key="mmlu",
        name="MMLU",
        evalscope_name="mmlu",
        source_id="cais/mmlu",
        source="modelscope",
        split="test",
        subset="default",
        license="MIT",
        language="en",
        capability_tags=("knowledge", "multiple-choice"),
        scoring_method="multiple_choice",
        description="覆盖 STEM、人文和社会科学的多任务知识评测。",
    ),
    BuiltinDatasetSpec(
        key="gsm8k",
        name="GSM8K",
        evalscope_name="gsm8k",
        source_id="AI-ModelScope/gsm8k",
        source="modelscope",
        split="test",
        subset="main",
        license="MIT",
        language="en",
        capability_tags=("math", "reasoning"),
        scoring_method="numeric_tolerance",
        description="小学数学文字题和多步推理评测。",
    ),
    BuiltinDatasetSpec(
        key="mgsm",
        name="MGSM",
        evalscope_name="mgsm",
        source_id="evalscope/mgsm",
        source="modelscope",
        split="test",
        subset="default",
        license="See upstream dataset terms",
        language="multilingual",
        capability_tags=("math", "reasoning", "multilingual"),
        scoring_method="numeric_tolerance",
        description="覆盖 11 种语言的多语言数学推理评测。",
    ),
    BuiltinDatasetSpec(
        key="humaneval",
        name="HumanEval",
        evalscope_name="humaneval",
        source_id="opencompass/humaneval",
        source="modelscope",
        split="test",
        subset="openai_humaneval",
        license="MIT",
        language="en",
        capability_tags=("code-generation", "sandbox"),
        scoring_method="code",
        description="Python 函数实现和单元测试评测。",
    ),
    BuiltinDatasetSpec(
        key="mbpp",
        name="MBPP",
        evalscope_name="mbpp",
        source_id="google-research-datasets/mbpp",
        source="modelscope",
        split="test",
        subset="full",
        license="Apache-2.0",
        language="en",
        capability_tags=("code-generation", "sandbox"),
        scoring_method="code",
        description="基础 Python 编程题和测试用例评测。",
    ),
)

_BY_KEY = {item.key: item for item in BUILTIN_DATASETS}


class BuiltinDatasetProvider(Protocol):
    """将一个内置数据集解析为可迭代的原始记录。"""

    def iter_rows(self, spec: BuiltinDatasetSpec) -> Iterable[Mapping[str, Any]]:
        ...


class EvalScopeDatasetProvider:
    """使用 EvalScope 的 dataset hub 下载数据，避免平台维护第二套 hub 逻辑。"""

    def iter_rows(self, spec: BuiltinDatasetSpec) -> Iterable[Mapping[str, Any]]:
        try:
            from evalscope.api.dataset.hub import DatasetHub  # type: ignore[import-not-found]
        except (ImportError, ModuleNotFoundError) as exc:
            raise RuntimeError(
                "内置数据集下载需要安装 evalplat[evalscope] 及其数据集依赖"
            ) from exc

        hub = DatasetHub(
            data_id_or_path=spec.source_id,
            data_source=spec.source,
            revision=spec.revision,
        )
        dataset = hub.load(split=spec.split, subset=spec.subset)
        for row in dataset:
            if not isinstance(row, Mapping):
                raise ValueError("内置数据集 provider 返回了非对象记录")
            yield row


def get_builtin_dataset(key: str) -> BuiltinDatasetSpec:
    normalized = str(key or "").strip().lower()
    try:
        return _BY_KEY[normalized]
    except KeyError as exc:
        raise ValueError(f"不支持的内置数据集: {key}") from exc


def list_builtin_datasets() -> list[dict[str, Any]]:
    return [
        {
            **asdict(spec),
            "capability_tags": list(spec.capability_tags),
        }
        for spec in BUILTIN_DATASETS
    ]


def normalize_builtin_row(spec: BuiltinDatasetSpec, raw: Mapping[str, Any]) -> dict[str, Any]:
    """将不同 benchmark 的字段归一为平台 ``load_samples`` 可消费的记录。"""

    row = _json_safe(dict(raw))
    messages = row.get("messages")
    if not isinstance(messages, list):
        candidate = row.get("prompt")
        if candidate is None:
            candidate = row.get("question", row.get("text", row.get("problem")))
        if candidate is None and isinstance(row.get("input"), list):
            input_items = row["input"]
            contents = [
                item.get("content")
                for item in input_items
                if isinstance(item, Mapping) and isinstance(item.get("content"), str)
            ]
            candidate = contents[-1] if contents else None
        if candidate is not None:
            messages = [{"role": "user", "content": str(candidate)}]

    expected = row.get("expected")
    if expected is None:
        expected = row.get(
            "answer",
            row.get("answer_number", row.get("target", row.get("label", ""))),
        )

    tests = row.get("tests", row.get("test", row.get("test_code", "")))
    if not tests and isinstance(row.get("test_list"), list):
        tests = "\n".join(str(item) for item in row["test_list"])

    normalized = dict(row)
    if isinstance(messages, list):
        normalized["messages"] = messages
    normalized["expected"] = expected
    normalized["tests"] = tests if isinstance(tests, str) else str(tests or "")
    normalized["builtin_dataset"] = spec.key
    return normalized


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        return _json_safe(tolist())
    return str(value)
