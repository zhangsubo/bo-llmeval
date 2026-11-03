#!/usr/bin/env python3
"""CLI benchmark: uses the same engine and result files as the Web workbench."""
from pathlib import Path
import argparse
import asyncio
import json
import os
import signal
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.benchmark import BenchmarkConfig, BenchmarkRun, Stage
from backend.config_store import DEFAULT_CONFIG_PATH
from backend.results_store import DEFAULT_RESULTS_ROOT
from backend.runner_service import TestRunManager


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=DEFAULT_CONFIG_PATH)
    p.add_argument('--profile', type=int)
    p.add_argument('--model', required=True)
    p.add_argument('--api-type', choices=['chat/completions', 'responses'], default='chat/completions')
    p.add_argument('--concurrency', type=int, default=10)
    p.add_argument('--requests', type=int, default=100)
    p.add_argument('--rps', type=float)
    p.add_argument('--duration', type=float)
    p.add_argument('--ramp-up', type=float, default=0, help='固定并发从 1 增长到目标的秒数，默认不渐增')
    p.add_argument('--stages', type=Path, help='JSON 数组，逐阶段设置 concurrency/requests/rps/duration_s')
    p.add_argument('--template', choices=['short', 'long_input', 'long_output', 'reasoning', 'tools'], default='short')
    p.add_argument('--prompt-file', type=Path)
    p.add_argument('--max-tokens', type=int, default=256)
    p.add_argument('--warmup', type=int, default=0)
    p.add_argument('--stream', action=argparse.BooleanOptionalAction, default=True)
    p.add_argument('--timeout', type=float, default=120)
    p.add_argument('--silence-timeout', type=float, default=60)
    p.add_argument('--total-timeout', type=float, default=3600)
    p.add_argument('--strict-errors', action='store_true')
    p.add_argument('--suite', choices=['performance', 'selfcheck'], default='performance')
    p.add_argument('--tokenizer-encoding', choices=['cl100k_base', 'o200k_base'], help='手动选择匹配的正文 tokenizer 以估算 TPOT')
    p.add_argument('--output', type=Path, default=DEFAULT_RESULTS_ROOT)
    a = p.parse_args()
    try:
        stages = json.loads(a.stages.read_text()) if a.stages else [Stage(concurrency=a.concurrency,
            requests=a.requests, rps=a.rps, duration_s=a.duration, ramp_up_s=a.ramp_up).model_dump()]
        config = BenchmarkConfig(model=a.model, api_type=a.api_type, stages=stages, template=a.template,
            prompt=a.prompt_file.read_text() if a.prompt_file else None, max_tokens=a.max_tokens,
            stream=a.stream, warmup=a.warmup, timeout_s=a.timeout, silence_timeout_s=a.silence_timeout,
            total_timeout_s=a.total_timeout, strict_errors=a.strict_errors, suite=a.suite, tokenizer_encoding=a.tokenizer_encoding)
        resolver = TestRunManager(a.config, a.output)
        key, base, _ = resolver._resolve_credentials({'profile_id': a.profile,
            'custom_base_url': os.environ.get('OPENAI_BASE_URL'), 'custom_api_key': os.environ.get('OPENAI_API_KEY')})
        run = BenchmarkRun(config, a.output)
        run.record['profile_id'] = a.profile or 'custom'
    except (ValueError, OSError):
        p.error('参数或凭证无效；请检查正整数并发、配置组与运行时环境变量')
    def stop(signum, frame):
        run.stop(immediate=run.stop_requested)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, lambda s, f: run.stop(True))
    print(f'结果目录：{run.folder}', flush=True)
    result = asyncio.run(run.execute(base, key))
    print(f"状态：{result['status']}；报告：{run.folder / 'report.md'}")
    return 0 if result['status'] == 'completed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
