"""Single-machine asynchronous benchmark engine shared by CLI and Web.

SSE deltas are chunks, never token timing. Credentials and response content are
kept out of persisted records; request-level timing evidence is append-only.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import fcntl
import json
import math
import os
from pathlib import Path
import platform
import re
import resource
import csv
import statistics
import time
import uuid
from functools import lru_cache
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

TEMPLATES = {
    'short': '请用一句话说明什么是机器学习。',
    'long_input': ('资料：性能测试需要记录负载、延迟、吞吐量和错误率。\n' * 256) + '请总结上述资料。',
    'long_output': '请详细解释软件测试的主要类型，按章节举例说明。',
    'reasoning': '有三个箱子，分别标记苹果、橙子、混合，标签全部错误。只能取一个水果，如何确定所有箱子内容？',
    'tools': '请调用 lookup_city 查询北京的天气。',
}
TOOL_SCHEMA = {'type': 'object', 'properties': {'city': {'type': 'string'}}, 'required': ['city'], 'additionalProperties': False}
METRICS = ('ttft_ms', 'first_reasoning_ms', 'first_tool_ms', 'e2e_ms', 'max_silence_ms', 'tpot_estimated_ms')


class Stage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    concurrency: StrictInt = Field(default=10, ge=1)
    requests: StrictInt = Field(default=100, ge=1)
    duration_s: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    rps: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    ramp_up_s: float = Field(default=0, ge=0, allow_inf_nan=False)


class BenchmarkConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    api_type: Literal['chat/completions', 'responses'] = 'chat/completions'
    model: str = Field(min_length=1, max_length=256)
    template: Literal['short', 'long_input', 'long_output', 'reasoning', 'tools'] = 'short'
    prompt: str | None = Field(default=None, max_length=262144)
    stream: bool = True
    max_tokens: StrictInt = Field(default=256, ge=1)
    temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    stages: list[Stage] = Field(default_factory=lambda: [Stage()], min_length=1, max_length=100)
    warmup: StrictInt = Field(default=0, ge=0, le=1000)
    timeout_s: float = Field(default=120, gt=0, allow_inf_nan=False)
    first_response_timeout_s: float = Field(default=60, gt=0, allow_inf_nan=False)
    silence_timeout_s: float = Field(default=60, gt=0, allow_inf_nan=False)
    total_timeout_s: float = Field(default=3600, gt=0, allow_inf_nan=False)
    strict_errors: bool = False
    tokenizer_encoding: Literal['cl100k_base', 'o200k_base'] | None = None
    suite: Literal['performance', 'selfcheck'] = 'performance'
    error_rate_stop: float | None = Field(default=None, gt=0, le=1, allow_inf_nan=False)
    error_rate_min_samples: StrictInt = Field(default=20, ge=1)

    @field_validator('model')
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError('model 不可为空')
        return value.strip()

    @model_validator(mode='after')
    def limits(self):
        if sum(s.requests for s in self.stages) > 1_000_000:
            raise ValueError('单任务请求上限为 1000000，请拆分任务')
        return self

    def body(self):
        prompt = self.prompt if self.prompt is not None else TEMPLATES[self.template]
        body = {'model': self.model, 'stream': self.stream}
        if self.api_type == 'chat/completions':
            body.update(messages=[{'role': 'user', 'content': prompt}], max_tokens=self.max_tokens)
            if self.stream:
                body['stream_options'] = {'include_usage': True}
        else:
            body.update(input=prompt, max_output_tokens=self.max_tokens)
        if self.temperature is not None:
            body['temperature'] = self.temperature
        if self.template == 'tools':
            function = {'name': 'lookup_city', 'description': '查询城市天气', 'parameters': TOOL_SCHEMA}
            body['tools'] = ([{'type': 'function', 'function': function}] if self.api_type == 'chat/completions'
                             else [{'type': 'function', **function}])
            body['tool_choice'] = 'required'
        return body


def atomic_json(path: Path, value):
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as f:
            os.chmod(temp, 0o600)
            json.dump(value, f, ensure_ascii=False, allow_nan=False)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def redact_evidence(value, key):
    if isinstance(value, str):
        if key:
            value = value.replace(key, '[REDACTED]')
        value = re.sub(r'https?://[^\s"<>]+', '[URL omitted]', value)
        return re.sub(r'(?i)Bearer\s+[^\s"<>]+|\bsk-[A-Za-z0-9_-]+', '[REDACTED]', value)
    if isinstance(value, list):
        return [redact_evidence(v, key) for v in value]
    if isinstance(value, dict):
        return {redact_evidence(k, key): redact_evidence(v, key) for k, v in value.items()}
    return value


def distribution(values, missing=0):
    values = sorted(v for v in values if isinstance(v, (float, int)) and math.isfinite(v))
    n = len(values)
    return {'count': n, 'missing_count': missing, 'mean': statistics.fmean(values) if n else None,
            **{f'p{p}': values[math.ceil(p / 100 * n) - 1] if n else None for p in (50, 95, 99)},
            'max': values[-1] if n else None, 'p99_low_sample': n < 100}


@lru_cache(maxsize=2)
def tokenizer(name):
    import tiktoken
    return tiktoken.get_encoding(name)


def cancel_once(task):
    # Repeated cancellation can interrupt response cleanup and lose a request row.
    if not task.done() and not task.cancelling():
        task.cancel()


class StreamMeasurement:
    """Bounded per-request parser. Keeps only timing and tool arguments in RAM."""
    def __init__(self, api_type, capture_text=False):
        self.api_type = api_type
        self.ttft = self.first_reasoning = self.first_tool = self.last_text = None
        self.intervals = []
        self.events = []
        self.event_count = 0
        self.text_chars = 0
        self.tools = {}
        self.finished = {}
        self.done = False
        self.truncated = False
        self.usage = None
        self.reasoning_seen = False
        self.last_progress = 0.0
        self.text_parts = [] if capture_text else None

    def text(self, value, at):
        if not isinstance(value, str) or not value:
            return
        if self.ttft is None:
            self.ttft = at
        if self.last_text is not None:
            self.intervals.append(at - self.last_text)
        self.last_text = at
        self.last_progress = at / 1000
        self.text_chars += len(value)
        if self.text_parts is not None:
            self.text_parts.append(value)

    def tool(self, index, name='', arguments='', at=0, replace=False):
        if self.first_tool is None:
            self.first_tool = at
        self.last_progress = at / 1000
        entry = self.tools.setdefault(str(index), {'name': '', 'arguments': ''})
        if name:
            entry['name'] = name if replace else entry['name'] + name
        if arguments:
            entry['arguments'] = arguments if replace else entry['arguments'] + arguments
        if sum(len(t['arguments']) for t in self.tools.values()) > 262144:
            raise ValueError('tool_arguments_too_large')

    def feed(self, payload, name, at):
        self.event_count += 1
        if len(self.events) < 256:
            self.events.append({'at_ms': at, 'event': str(name)[:100]})
        if payload == '[DONE]':
            if self.api_type != 'chat/completions' or not self.finished or any(v is None for v in self.finished.values()):
                raise ValueError('missing_finish_reason')
            self.done = True
            return
        v = json.loads(payload)
        if not isinstance(v, dict):
            raise ValueError('invalid_event')
        kind = v.get('type') or name
        if v.get('error') or kind in ('error', 'response.failed'):
            raise ValueError('stream_error')
        if self.api_type == 'chat/completions':
            if isinstance(v.get('usage'), dict):
                self.usage = v['usage']
            choices = v.get('choices', [])
            if not isinstance(choices, list):
                raise ValueError('invalid_choices')
            for c in choices:
                idx = c.get('index', 0)
                self.finished[idx] = c.get('finish_reason') or self.finished.get(idx)
                self.truncated |= c.get('finish_reason') == 'length'
                d = c.get('delta') or {}
                self.text(d.get('content') or d.get('refusal'), at)
                if d.get('reasoning_content') or d.get('reasoning'):
                    self.reasoning_seen = True
                    if self.first_reasoning is None:
                        self.first_reasoning = at
                for tool in d.get('tool_calls') or []:
                    fn = tool.get('function') or {}
                    self.tool(f"{idx}:{tool.get('index', 0)}", fn.get('name', ''), fn.get('arguments', ''), at)
        else:
            if kind in ('response.output_text.delta', 'response.refusal.delta'):
                self.text(v.get('delta'), at)
            elif kind in ('response.reasoning_text.delta', 'response.reasoning_summary_text.delta'):
                if v.get('delta'):
                    self.reasoning_seen = True
                    if self.first_reasoning is None:
                        self.first_reasoning = at
            elif kind == 'response.output_item.added':
                item = v.get('item') or {}
                if item.get('type') == 'function_call':
                    self.tool(item.get('id', v.get('output_index')), item.get('name', ''), item.get('arguments', ''), at)
            elif kind == 'response.function_call_arguments.delta':
                self.tool(v.get('item_id', v.get('output_index')), arguments=v.get('delta', ''), at=at)
            elif kind in ('response.completed', 'response.incomplete'):
                final = v.get('response')
                if not isinstance(final, dict):
                    raise ValueError('invalid_final_response')
                self.consume_final(final, at, streaming=True)
                self.done = True
                self.truncated |= kind == 'response.incomplete'

    def consume_final(self, value, at, streaming=False):
        if value.get('error'):
            raise ValueError('response_error')
        self.usage = value.get('usage') if isinstance(value.get('usage'), dict) else self.usage
        if self.api_type == 'chat/completions':
            choices = value.get('choices')
            if not isinstance(choices, list) or not choices:
                raise ValueError('invalid_choices')
            for c in choices:
                if not c.get('finish_reason'):
                    raise ValueError('missing_finish_reason')
                m = c.get('message') or {}
                self.text(m.get('content') or m.get('refusal'), at)
                self.truncated |= c.get('finish_reason') == 'length'
                for i, t in enumerate(m.get('tool_calls') or []):
                    fn = t.get('function') or {}
                    self.tool(f"{c.get('index', 0)}:{i}", fn.get('name', ''), fn.get('arguments', ''), at)
        else:
            if value.get('status') not in ('completed', 'incomplete'):
                raise ValueError('invalid_response_status')
            self.truncated |= value.get('status') == 'incomplete'
            output = value.get('output')
            if not isinstance(output, list):
                raise ValueError('invalid_output')
            for i, item in enumerate(output):
                if item.get('type') == 'function_call':
                    self.tool(item.get('id', i), item.get('name', ''), item.get('arguments', ''), at, replace=True)
                elif not streaming and item.get('type') == 'message':
                    for block in item.get('content') or []:
                        self.text(block.get('text') or block.get('refusal'), at)
        self.done = True

    def result(self, config):
        tools_valid = None
        if self.tools:
            tools_valid = True
            for t in self.tools.values():
                try:
                    a = json.loads(t['arguments'])
                    if config.template == 'tools':
                        tools_valid &= (t['name'] == 'lookup_city' and isinstance(a, dict)
                                        and set(a) == {'city'} and isinstance(a['city'], str))
                    else:
                        tools_valid &= isinstance(a, dict) and bool(t['name'])
                except (ValueError, TypeError):
                    tools_valid = False
        # User-selected tokenizer estimates BODY token count, never mixed reasoning usage.
        body_tokens = None
        estimate = None
        if config.tokenizer_encoding and self.text_parts:
            body_tokens = len(tokenizer(config.tokenizer_encoding).encode(''.join(self.text_parts), disallowed_special=()))
            if config.stream and body_tokens > 1 and self.ttft is not None and self.intervals:
                estimate = (self.last_text - self.ttft) / (body_tokens - 1)
        # Exact token timestamps are not observable in ordinary OpenAI SSE.
        return {'ttft_ms': self.ttft if config.stream else None,
                'first_reasoning_ms': self.first_reasoning if config.stream else None,
                'first_tool_ms': self.first_tool if config.stream else None,
                'max_silence_ms': max(self.intervals) if self.intervals else None,
                'tpot_estimated_ms': estimate, 'itl_ms': None, 'body_tokens_estimated': body_tokens,
                'tokenizer_encoding': config.tokenizer_encoding,
                'token_timing_reason': 'TPOT 仅为所选 tokenizer 的正文估算，首片段可能含多 token；ITL 缺少逐 token 时间',
                'chunk_intervals_ms': self.intervals,
                'text_chars': self.text_chars, 'tool_calls': len(self.tools), 'tools_valid': tools_valid,
                'truncated': self.truncated, 'protocol_complete': self.done,
                'events': self.events, 'events_truncated': self.event_count > len(self.events),
                'usage': self.safe_usage()}

    def safe_usage(self):
        if not self.usage:
            return None
        # Numeric allowlist: upstream can put arbitrary (including sensitive) text in usage.
        return {k: v for k, v in self.usage.items()
                if k in ('prompt_tokens', 'completion_tokens', 'input_tokens', 'output_tokens', 'total_tokens')
                and type(v) is int and v >= 0}


async def measure(client, url, key, config, request_id, stage_id, planned, probe=None):
    start = time.perf_counter()
    parser = StreamMeasurement(config.api_type, bool(config.tokenizer_encoding))
    row = {'request_id': request_id, 'stage': stage_id, 'started_at': datetime.now(timezone.utc).isoformat(),
           'schedule_lag_ms': max(0, (start - planned) * 1000), 'http_status': None,
           'success': False, 'error': None, 'retry_after': None, 'request_trace_id': None}
    timeout_phase = 'first_response'
    try:
        async with asyncio.timeout(config.timeout_s):
            async with asyncio.timeout(config.first_response_timeout_s):
                body = config.body()
                headers = {'Authorization': 'Bearer ' + key, 'Accept': 'text/event-stream' if config.stream else 'application/json'}
                if probe == 'missing_auth':
                    headers.pop('Authorization')
                elif probe == 'invalid_parameters':
                    body['messages' if config.api_type == 'chat/completions' else 'input'] = 123456
                elif probe == 'unknown_model':
                    body['model'] = 'llmeval-nonexistent-' + uuid.uuid4().hex
                response = await client.send(client.build_request('POST', url, json=body, headers=headers), stream=True)
            try:
                row['http_status'] = response.status_code
                timeout_phase = 'response_body'
                row['request_trace_id'] = redact_evidence(response.headers.get('x-request-id', '')[:256], key)
                retry = response.headers.get('retry-after')
                # Retry-After is metadata, not an arbitrary response body.
                if retry:
                    from email.utils import parsedate_to_datetime
                    try:
                        row['retry_after'] = int(retry) if retry.isdigit() else parsedate_to_datetime(retry).isoformat()
                    except (ValueError, TypeError, OverflowError):
                        row['retry_after_invalid'] = True
                if response.status_code >= 300:
                    row['error'] = 'http_error'
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk[:8192])
                        if len(content) >= 8192:
                            break
                    try:
                        error_data = json.loads(content).get('error', {})
                        if isinstance(error_data, dict):
                            row['upstream_error'] = redact_evidence({k: error_data[k] for k in
                                ('code', 'type', 'message', 'param') if k in error_data and isinstance(error_data[k], (str, int))}, key)
                    except (ValueError, AttributeError):
                        pass
                elif config.stream:
                    if 'text/event-stream' not in response.headers.get('content-type', '').lower():
                        raise ValueError('expected_event_stream')
                    # aiter_lines handles UTF-8 boundaries and CRLF. Frame size and stream
                    # size are bounded; read deadlines include heartbeats without content.
                    lines = response.aiter_lines().__aiter__()
                    data, event = [], ''
                    received = 0
                    while not parser.done:
                        elapsed = time.perf_counter() - start
                        wait = config.silence_timeout_s - (elapsed - parser.last_progress)
                        timeout_phase = 'content_silence'
                        if parser.event_count == 0:
                            if config.first_response_timeout_s - elapsed < wait:
                                timeout_phase = 'first_response'
                            wait = min(wait, config.first_response_timeout_s - elapsed)
                        if wait <= 0:
                            raise TimeoutError('content_silence_timeout')
                        try:
                            async with asyncio.timeout(wait):
                                line = await anext(lines)
                        except StopAsyncIteration:
                            raise ValueError('incomplete_stream') from None
                        received += len(line)
                        if received > 8 * 1024 * 1024 or len(line) > 262144:
                            raise ValueError('response_too_large')
                        if not line:
                            if data:
                                parser.feed('\n'.join(data), event, (time.perf_counter() - start) * 1000)
                            data, event = [], ''
                        elif line.startswith('data:'):
                            data.append(line[5:].lstrip(' '))
                        elif line.startswith('event:'):
                            event = line[6:].lstrip(' ')
                else:
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 8 * 1024 * 1024:
                            raise ValueError('response_too_large')
                    value = json.loads(content)
                    if not isinstance(value, dict):
                        raise ValueError('invalid_response')
                    parser.consume_final(value, (time.perf_counter() - start) * 1000)
            finally:
                await response.aclose()
    except asyncio.CancelledError:
        row['error'] = 'cancelled'
    except (TimeoutError, httpx.TimeoutException):
        row['error'] = 'timeout'
        row['timeout_phase'] = 'request_total' if time.perf_counter() - start >= config.timeout_s else timeout_phase
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        # Only our static identifiers are persisted, never json decoder/request text.
        known = {'incomplete_stream', 'stream_error', 'missing_finish_reason', 'expected_event_stream',
                 'response_too_large', 'tool_arguments_too_large', 'invalid_response_status', 'response_error'}
        row['error'] = str(exc) if str(exc) in known else 'invalid_response'
    except httpx.HTTPError as exc:
        row['error'] = 'transport_error'
        row['transport_error_type'] = type(exc).__name__
        cause = exc
        for _ in range(5):
            if getattr(cause, 'errno', None) is not None:
                row['transport_errno'] = cause.errno
                break
            cause = cause.__cause__
            if cause is None:
                break
    ended = time.perf_counter()
    try:
        measured = await asyncio.to_thread(parser.result, config) if config.tokenizer_encoding else parser.result(config)
    except asyncio.CancelledError:
        measured = parser.result(config.model_copy(update={'tokenizer_encoding': None}))
        measured['token_timing_reason'] = '停止期间未完成 tokenizer 估算；网络阶段结果已保留'
    row.update(measured)
    if probe:
        expected = {'missing_auth': [401, 403], 'invalid_parameters': [400, 422], 'unknown_model': [400, 404]}
        row['probe'] = probe
        row['expected_statuses'] = expected[probe]
        row['probe_verdict'] = ('passed' if row['http_status'] in expected[probe] else
                                'not_verified' if row['http_status'] is None or row['http_status'] >= 500 else 'failed')
    row['e2e_ms'] = (ended - start) * 1000
    scene = row['tools_valid'] is True if config.template == 'tools' else row['text_chars'] > 0
    row['success'] = bool(row['http_status'] is not None and 200 <= row['http_status'] < 300
                          and row['protocol_complete'] and not row['error'] and scene and not row['truncated'])
    if row['protocol_complete'] and not row['error'] and not scene:
        row['error'] = 'invalid_tools' if config.template == 'tools' else 'no_text'
    return redact_evidence(row, key)


def summarize(rows, elapsed_s, slots=0, skipped=0):
    rows = [r for r in rows if not r.get('probe')]
    success = [r for r in rows if r['success']]
    usage_rows = [r for r in rows if r.get('usage') and
                  ('completion_tokens' in r['usage'] or 'output_tokens' in r['usage'])]
    tokens = sum(r['usage'].get('completion_tokens', r['usage'].get('output_tokens', 0)) for r in usage_rows)
    return {'requests': len(rows), 'successes': len(success), 'success_rate': len(success) / len(rows) if rows else None,
            'http_successes': sum(200 <= (r.get('http_status') or 0) < 300 for r in rows),
            'protocol_complete': sum(r['protocol_complete'] for r in rows),
            'truncated': sum(r['truncated'] for r in rows), 'planned_slots': slots, 'skipped_slots': skipped,
            'errors': dict(Counter(r['error'] for r in rows if r.get('error'))),
            'timeout_phases': dict(Counter(r['timeout_phase'] for r in rows if r.get('timeout_phase'))),
            'http_statuses': dict(Counter(str(r.get('http_status')) for r in rows)),
            'elapsed_s': elapsed_s, 'completed_rps': len(rows) / elapsed_s if elapsed_s else None,
            'successful_rps': len(success) / elapsed_s if elapsed_s else None,
            'output_tokens': tokens if usage_rows else None, 'usage_samples': len(usage_rows),
            'output_tokens_per_s': tokens / elapsed_s if usage_rows and elapsed_s else None,
            'throughput_window': '正式阶段开始至排空结束；输出用量按完成请求聚合',
            'metrics': {k: distribution([r[k] for r in success if r.get(k) is not None],
                         len(success) - sum(r.get(k) is not None for r in success)) for k in METRICS},
            'failed_e2e_ms': distribution([r['e2e_ms'] for r in rows if not r['success']]),
            'chunk_interval_ms': distribution([v for r in success for v in r['chunk_intervals_ms']]),
            'schedule_lag_ms': distribution([r['schedule_lag_ms'] for r in rows]),
            'performance_verdict': '未配置 SLO，不判定性能通过', 'goodput': None}


def checks(rows, strict=False):
    statuses = Counter(r.get('http_status') for r in rows)
    rate = [r for r in rows if r.get('http_status') in (429, 503)]
    observed = [
        {'name': '流式/协议完整性', 'status': 'failed' if any(r.get('error') == 'incomplete_stream' for r in rows) else
         ('passed' if rows and all(r['protocol_complete'] for r in rows) else 'not_verified'), 'evidence': '客户端协议终止事件'},
        {'name': '工具调用完整性', 'status': 'failed' if any(r['tools_valid'] is False for r in rows) else
         ('passed' if any(r['tools_valid'] is True for r in rows) else 'not_applicable'), 'evidence': '最终 JSON 与模板 schema'},
        {'name': 'Retry-After', 'status': ('passed' if all(r['retry_after'] is not None for r in rate) else
         ('failed' if strict else 'not_verified')) if rate else 'not_verified', 'evidence': f'观察到 {len(rate)} 个 429/503'},
        {'name': '服务端取消后资源释放', 'status': 'not_verified', 'evidence': '需要服务端监控证据，客户端关闭不能证明'},
        {'name': '错误码原因映射', 'status': 'not_verified', 'evidence': '需受控触发原因；仅观察状态码不能证明映射正确'},
        {'name': '限流与降载恢复', 'status': 'not_verified', 'evidence': f'429={statuses[429]}，503={statuses[503]}；参考各阶段报告，无恢复阈值'},
    ]
    names = {'missing_auth': '缺少凭证', 'invalid_parameters': '无效参数', 'unknown_model': '不存在的模型'}
    observed.extend({'name': names[r['probe']], 'status': r['probe_verdict'],
        'evidence': f"受控触发 HTTP {r['http_status']}，预期 {r['expected_statuses']}"} for r in rows if r.get('probe'))
    return observed


class BenchmarkRun:
    def __init__(self, config: BenchmarkConfig, root: Path, run_id=None):
        self.config = config
        root.mkdir(parents=True, exist_ok=True)
        self._global_lease = (root / '.benchmark.lock').open('a')
        try:
            fcntl.flock(self._global_lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._global_lease.close()
            raise ValueError('该结果目录已有压测执行中（CLI/Web 共用）') from None
        self.run_id = run_id or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8]
        self.folder = root / 'benchmark' / self.run_id
        self.folder.mkdir(parents=True, exist_ok=False)
        self._lease = (self.folder / '.running.lock').open('a')
        fcntl.flock(self._lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.stop_requested = False
        self.cancel_requested = False
        self.rows = []
        self.tasks = set()
        self.slots = self.skipped = 0
        self.started = time.perf_counter()
        self.measure_start = None
        self.stage_reports = []
        self.samples = []
        self.environment = {'python': platform.python_version(), 'system': platform.system(),
                            'machine': platform.machine(), 'cpu_count': os.cpu_count(),
                            'nofile_soft_limit': resource.getrlimit(resource.RLIMIT_NOFILE)[0]}
        self.record = {'schema_version': 1, 'metric_definition_version': 'body-chunk-v1',
                       'run_id': self.run_id, 'mode': 'benchmark', 'model': config.model,
                       'status': 'validating', 'outcome': 'running',
                       'started_at': datetime.now(timezone.utc).isoformat(), 'config': self.public_config(),
                       'environment': self.environment, 'stage_reports': [], 'samples': [], 'active': 0,
                       'warnings': ['并发数可配置；100～500 为验证目标，当前环境能力未经认证。',
                                    'ITL/TPOT 缺少逐 token 证据；片段间隔单独报告。'], 'checks': []}
        self.persist()

    def public_config(self):
        cfg = self.config.model_dump()
        prompt = cfg.pop('prompt')
        cfg['prompt_sha256'] = hashlib.sha256((prompt if prompt is not None else TEMPLATES[self.config.template]).encode()).hexdigest()
        cfg['custom_prompt'] = prompt is not None
        cfg['template_version'] = 1
        return cfg

    def stop(self, immediate=False):
        self.stop_requested = True
        self.cancel_requested |= immediate

    def read_stop_control(self):
        try:
            control = json.loads((self.folder / 'stop.json').read_text())
            self.stop(bool(control.get('immediate')))
        except (OSError, ValueError):
            pass

    def persist(self):
        self.record['active'] = len(self.tasks)
        self.record['elapsed_ms'] = (time.perf_counter() - self.started) * 1000
        if self.measure_start:
            self.record['summary'] = summarize(self.rows, time.perf_counter() - self.measure_start, self.slots, self.skipped)
        self.record['stage_reports'] = self.stage_reports
        self.record['samples'] = self.samples[-600:]
        self.record['checks'] = checks(self.rows, self.config.strict_errors)
        atomic_json(self.folder / 'result.json', self.record)

    async def execute(self, base_url, key):
        url = base_url.rstrip('/') + '/' + self.config.api_type
        self.record['config']['endpoint_sha256'] = hashlib.sha256(base_url.encode()).hexdigest()
        max_concurrency = max(s.concurrency for s in self.config.stages)
        # The HTTP pool must not secretly cap configured concurrency at 100.
        limits = httpx.Limits(max_connections=max_concurrency, max_keepalive_connections=max_concurrency)
        try:
            if self.config.tokenizer_encoding:
                await asyncio.to_thread(tokenizer, self.config.tokenizer_encoding)
            async with httpx.AsyncClient(limits=limits, timeout=None, follow_redirects=False, trust_env=False) as client:
                self.record['status'] = 'warming_up'
                with (self.folder / 'warmup.jsonl').open('a', encoding='utf-8') as out:
                    for i in range(self.config.warmup):
                        if self.stop_requested:
                            break
                        warm = asyncio.create_task(measure(client, url, key, self.config, f'w{i}', -1, time.perf_counter()))
                        self.tasks.add(warm)
                        while not warm.done():
                            self.read_stop_control()
                            if self.cancel_requested or time.perf_counter() - self.started >= self.config.total_timeout_s:
                                self.stop(True)
                                cancel_once(warm)
                            await asyncio.sleep(.05)
                        self.tasks.discard(warm)
                        out.write(json.dumps(await warm, ensure_ascii=False) + '\n')
                self.measure_start = time.perf_counter()
                with (self.folder / 'requests.jsonl').open('a', encoding='utf-8') as out:
                    if self.config.suite == 'selfcheck':
                        for probe in ('missing_auth', 'invalid_parameters', 'unknown_model'):
                            if self.stop_requested:
                                break
                            task = asyncio.create_task(measure(client, url, key, self.config, probe, -2, time.perf_counter(), probe))
                            self.tasks.add(task)
                            while not task.done():
                                self.read_stop_control()
                                if self.cancel_requested or time.perf_counter() - self.started >= self.config.total_timeout_s:
                                    self.stop(True)
                                    cancel_once(task)
                                await asyncio.sleep(.05)
                            self.tasks.discard(task)
                            row = await task
                            self.rows.append(row)
                            out.write(json.dumps(row, ensure_ascii=False) + '\n')
                            out.flush()
                    self.measure_start = time.perf_counter()
                    for stage_id, stage in enumerate(self.config.stages):
                        if self.stop_requested:
                            break
                        stage_start = time.perf_counter()
                        first = len(self.rows)
                        slots_before, skipped_before = self.slots, self.skipped
                        self.record.update(status='running', stage=stage_id, target_concurrency=stage.concurrency, target_rps=stage.rps)
                        launched = 0
                        next_slot = stage_start
                        last_snapshot = 0
                        while True:
                            self.read_stop_control()
                            now = time.perf_counter()
                            if now - self.started >= self.config.total_timeout_s:
                                self.stop(True)
                                self.record['stop_reason'] = 'total_timeout'
                            if self.cancel_requested:
                                for task in self.tasks:
                                    cancel_once(task)
                            done = {t for t in self.tasks if t.done()}
                            for task in done:
                                if task.cancelled():
                                    continue
                                row = task.result()
                                self.rows.append(row)
                                out.write(json.dumps(row, ensure_ascii=False) + '\n')
                            self.tasks -= done
                            if done:
                                out.flush()
                            measured = [r for r in self.rows if not r.get('probe')] if self.config.error_rate_stop else []
                            if self.config.error_rate_stop and len(measured) >= self.config.error_rate_min_samples:
                                failures = sum(not r['success'] for r in measured)
                                if failures / len(measured) >= self.config.error_rate_stop:
                                    self.stop()
                                    self.record['stop_reason'] = 'error_rate'
                            active = (not self.stop_requested and launched < stage.requests
                                      and (stage.duration_s is None or now - stage_start < stage.duration_s))
                            if active:
                                if stage.rps:
                                    # Drop missed/saturated slots; never backlog a burst of requests.
                                    if now >= next_slot:
                                        missed = int((now - next_slot) * stage.rps)
                                        missed = min(missed, stage.requests - launched - 1)
                                        self.skipped += missed
                                        self.slots += missed
                                        launched += missed
                                        next_slot += missed / stage.rps
                                        self.slots += 1
                                        launched += 1
                                        if len(self.tasks) < stage.concurrency:
                                            self.tasks.add(asyncio.create_task(measure(client, url, key, self.config,
                                                f'{stage_id}-{launched}', stage_id, next_slot)))
                                        else:
                                            self.skipped += 1
                                        next_slot += 1 / stage.rps
                                else:
                                    target = stage.concurrency if not stage.ramp_up_s else max(1, min(stage.concurrency,
                                        math.ceil(stage.concurrency * (now - stage_start) / stage.ramp_up_s)))
                                    for _ in range(min(max(0, target - len(self.tasks)), stage.requests - launched)):
                                        launched += 1
                                        self.slots += 1
                                        self.tasks.add(asyncio.create_task(measure(client, url, key, self.config,
                                            f'{stage_id}-{launched}', stage_id, now)))
                            elif self.tasks:
                                self.record['status'] = 'stopping' if self.stop_requested else 'draining'
                            if now - last_snapshot >= 1:
                                usage = resource.getrusage(resource.RUSAGE_SELF)
                                self.samples.append({'at_s': now - self.measure_start, 'active': len(self.tasks),
                                    'completed': len(self.rows), 'successes': sum(r['success'] for r in self.rows),
                                    'rss_bytes': usage.ru_maxrss if platform.system() == 'Darwin' else usage.ru_maxrss * 1024,
                                    'cpu_seconds': usage.ru_utime + usage.ru_stime, 'stage': stage_id})
                                self.persist()
                                last_snapshot = now
                            if not active and not self.tasks:
                                break
                            await asyncio.sleep(min(.01, 1 / stage.rps) if stage.rps else .005)
                        self.stage_reports.append({'stage': stage_id, 'config': stage.model_dump(),
                            'summary': summarize(self.rows[first:], time.perf_counter() - stage_start,
                                                 self.slots - slots_before, self.skipped - skipped_before)})
            self.record['status'] = 'stopped' if self.stop_requested else 'completed'
        except Exception:
            self.record.update(status='failed', error='压测执行器失败；已保留完整样本')
            for task in self.tasks:
                cancel_once(task)
            await asyncio.gather(*self.tasks, return_exceptions=True)
            self.tasks.clear()
        finally:
            self.record['outcome'] = self.record['status']
            self.record['finished_at'] = datetime.now(timezone.utc).isoformat()
            self.persist()
            atomic_json(self.folder / 'summary.json', self.record.get('summary', {}))
            report = render_report(self.record)
            (self.folder / 'report.md').write_text(report, encoding='utf-8')
            (self.folder / 'summary.txt').write_text(report, encoding='utf-8')
            with (self.folder / 'requests.csv').open('w', newline='', encoding='utf-8') as csv_file:
                fields = ['request_id', 'stage', 'http_status', 'success', 'error', 'truncated', *METRICS]
                writer = csv.DictWriter(csv_file, fieldnames=fields, extrasaction='ignore')
                writer.writeheader()
                writer.writerows(self.rows)
            self._lease.close()
            self._global_lease.close()
        return self.record


def render_report(record):
    s = record.get('summary', {})
    lines = ['# LLM 压测报告', '', f"任务：{record['run_id']}；状态：{record['status']}",
             '', '未配置 SLO，不判定性能合格。ITL 与 TPOT 当前不可精确测量；片段间隔不等于 token 间隔。', '',
             f"请求 {s.get('requests', 0)}；成功 {s.get('successes', 0)}；跳过计划槽位 {s.get('skipped_slots', 0)}", '',
             '| 指标（ms） | 样本 | mean | p95 | p99 |', '|---|---:|---:|---:|---:|']
    for name, metric in s.get('metrics', {}).items():
        lines.append(f"| {name} | {metric['count']} | {metric['mean']} | {metric['p95']} | {metric['p99']} |")
    lines += ['', f"完成吞吐：{s.get('completed_rps')} req/s；成功吞吐：{s.get('successful_rps')} req/s",
              f"输出吞吐：{s.get('output_tokens_per_s')} token/s；usage 样本：{s.get('usage_samples', 0)}", '',
              '统计窗口包含排空；失败耗时与错误率请结合 summary.json 查看。', '', '## 自检证据', '']
    lines += [f"- {c['name']}：{c['status']}；{c['evidence']}" for c in record.get('checks', [])]
    return '\n'.join(lines) + '\n'


def compare(a, b):
    differences = {k: {'baseline': a.get('config', {}).get(k), 'candidate': b.get('config', {}).get(k)}
                   for k in set(a.get('config', {})) | set(b.get('config', {}))
                   if a.get('config', {}).get(k) != b.get('config', {}).get(k)}
    metrics = {}
    for name in METRICS:
        left = a.get('summary', {}).get('metrics', {}).get(name, {})
        right = b.get('summary', {}).get('metrics', {}).get(name, {})
        metrics[name] = {}
        for stat in ('mean', 'p95', 'p99'):
            x, y = left.get(stat), right.get(stat)
            metrics[name][stat] = {'baseline': x, 'candidate': y,
                'delta': y - x if x is not None and y is not None else None,
                'change_pct': (y - x) / x * 100 if x and y is not None else None}
    throughput = {}
    for name in ('completed_rps', 'successful_rps', 'output_tokens_per_s', 'success_rate'):
        x, y = a.get('summary', {}).get(name), b.get('summary', {}).get(name)
        throughput[name] = {'baseline': x, 'candidate': y,
            'delta': y - x if x is not None and y is not None else None,
            'change_pct': (y - x) / x * 100 if x and y is not None else None}
    return {'condition_differences': differences, 'metrics': metrics, 'throughput': throughput,
            'metric_compatible': a.get('metric_definition_version') == b.get('metric_definition_version'),
            'baseline_samples': a.get('summary', {}).get('successes', 0),
            'candidate_samples': b.get('summary', {}).get('successes', 0)}
