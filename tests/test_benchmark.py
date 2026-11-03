import asyncio
import json
from pathlib import Path
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.benchmark import BenchmarkConfig, BenchmarkRun, Stage, StreamMeasurement, compare, distribution, measure, summarize
from backend.main import create_app


def chat(text='hello', finish='stop', usage=True):
    values = [{'choices': [{'index': 0, 'delta': {'content': text}, 'finish_reason': None}]},
              {'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]}]
    if usage:
        values.append({'choices': [], 'usage': {'completion_tokens': 2, 'prompt_tokens': 5}})
    return ''.join('data: ' + json.dumps(v) + '\n\n' for v in values) + 'data: [DONE]\n\n'


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks, delay=0):
        self.chunks, self.delay = chunks, delay
        self.closed = False

    async def __aiter__(self):
        for piece in self.chunks:
            await asyncio.sleep(self.delay)
            yield piece.encode()

    async def aclose(self):
        self.closed = True


def request_with(chunks, config=None, status=200, headers=None, delay=0):
    stream = Chunks(chunks, delay)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(
            status, headers=headers or {'content-type': 'text/event-stream'}, stream=stream))) as client:
            return await measure(client, 'http://test/v1/chat/completions', 'test-secret-value',
                config or BenchmarkConfig(model='test'), '1', 0, time.perf_counter())
    return asyncio.run(run()), stream


@pytest.mark.parametrize('value', [0, -1, 1.5, '10', True])
def test_invalid_concurrency(value):
    with pytest.raises(ValidationError):
        Stage(concurrency=value)


def test_concurrency_no_fixed_500_limit():
    assert Stage(concurrency=600).concurrency == 600
    assert Stage(concurrency=1).concurrency == 1


def test_parser_body_not_reasoning_and_metrics():
    p = StreamMeasurement('chat/completions')
    p.feed(json.dumps({'choices': [{'delta': {'role': 'assistant'}}]}), '', 100)
    p.feed(json.dumps({'choices': [{'delta': {'reasoning_content': 'think'}}]}), '', 300)
    for at in (1000, 1200, 1700):
        p.feed(json.dumps({'choices': [{'delta': {'content': 'hello'}}]}), '', at)
    p.feed(json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}]}), '', 1800)
    p.feed('[DONE]', '', 2000)
    r = p.result(BenchmarkConfig(model='test'))
    assert r['ttft_ms'] == 1000
    assert r['first_reasoning_ms'] == 300
    assert r['max_silence_ms'] == 500
    assert r['chunk_intervals_ms'] == [200, 500]
    assert r['itl_ms'] is None and r['tpot_estimated_ms'] is None
    assert r['protocol_complete']


def test_tool_fragments_interleaved():
    p = StreamMeasurement('chat/completions')
    for index in (0, 1):
        p.feed(json.dumps({'choices': [{'delta': {'tool_calls': [{'index': index, 'function': {'name': 'lookup_city', 'arguments': '{"city":'}}]}}]}), '', 10)
    for index in (1, 0):
        p.feed(json.dumps({'choices': [{'delta': {'tool_calls': [{'index': index, 'function': {'arguments': '"北京"}'}}]}}]}), '', 20)
    p.feed(json.dumps({'choices': [{'finish_reason': 'tool_calls'}]}), '', 25)
    p.feed('[DONE]', '', 30)
    r = p.result(BenchmarkConfig(model='test', template='tools'))
    assert r['tools_valid'] is True and r['tool_calls'] == 2
    assert r['ttft_ms'] is None


def test_http_200_stream_error_not_success():
    row, _ = request_with(['data: {"error":{"message":"test-secret-value"}}\n\n'])
    assert row['http_status'] == 200 and not row['success']
    assert row['error'] == 'stream_error'
    assert 'test-secret-value' not in json.dumps(row)


def test_incomplete_and_finish_reason():
    row, _ = request_with(['data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'])
    assert not row['success'] and row['error'] == 'incomplete_stream'
    row, _ = request_with(['data: [DONE]\n\n'])
    assert row['error'] == 'missing_finish_reason'


def test_length_and_missing_usage():
    row, _ = request_with([chat(finish='length', usage=False)])
    assert row['truncated'] and row['protocol_complete'] and not row['success']
    assert row['usage'] is None


def test_heartbeats_cannot_extend_total_timeout():
    cfg = BenchmarkConfig(model='test', timeout_s=.05, silence_timeout_s=1)
    row, stream = request_with([': ping\n\n'] * 100, cfg, delay=.01)
    assert row['error'] == 'timeout' and stream.closed
    assert row['e2e_ms'] < 300


def test_content_silence_closes_connection():
    cfg = BenchmarkConfig(model='test', timeout_s=1, silence_timeout_s=.04)
    row, stream = request_with([': ping\n\n'] * 100, cfg, delay=.01)
    assert row['error'] == 'timeout' and stream.closed
    assert row['e2e_ms'] < 300


def test_success_and_rate_limit():
    row, stream = request_with([chat()])
    assert row['success'] and stream.closed
    row, _ = request_with([], status=429, headers={'retry-after': '3'})
    assert not row['success'] and row['retry_after'] == 3


def test_responses_stream():
    values = [{'type': 'response.reasoning_text.delta', 'delta': 'thought'},
              {'type': 'response.output_text.delta', 'delta': 'hello'},
              {'type': 'response.completed', 'response': {'status': 'completed', 'output': [], 'usage': {'output_tokens': 2}}}]
    body = ''.join('data: ' + json.dumps(v) + '\n\n' for v in values)
    row, _ = request_with([body], BenchmarkConfig(model='test', api_type='responses'))
    assert row['success'] and row['first_reasoning_ms'] is not None
    assert row['usage']['output_tokens'] == 2


def test_nonstream_no_fake_ttft():
    value = {'choices': [{'message': {'content': 'hello'}, 'finish_reason': 'stop'}]}
    row, _ = request_with([json.dumps(value)], BenchmarkConfig(model='test', stream=False))
    assert row['success'] and row['ttft_ms'] is None


def test_distribution_and_throughput():
    d = distribution(list(range(1, 101)))
    assert (d['p50'], d['p95'], d['p99']) == (50, 95, 99)
    row, _ = request_with([chat()])
    failed = {**row, 'success': False, 'error': 'timeout'}
    s = summarize([row] * 18 + [failed] * 2, 10)
    assert s['completed_rps'] == 2 and s['successful_rps'] == 1.8
    assert s['failed_e2e_ms']['count'] == 2 and s['metrics']['ttft_ms']['count'] == 18
    assert distribution([])['p99'] is None


def test_scheduler_and_persistence(tmp_path, monkeypatch):
    active, peak = 0, 0
    original = httpx.AsyncClient
    async def handle(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(.02)
        active -= 1
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Chunks([chat()]))
    monkeypatch.setattr('backend.benchmark.httpx.AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    run = BenchmarkRun(BenchmarkConfig(model='test', stages=[Stage(concurrency=7, requests=20)], warmup=2), tmp_path)
    record = asyncio.run(run.execute('http://test/v1', 'test-secret-value'))
    assert record['status'] == 'completed'
    assert record['summary']['requests'] == 20 and record['summary']['successes'] == 20
    assert peak == 7
    assert len((run.folder / 'requests.jsonl').read_text().splitlines()) == 20
    assert len((run.folder / 'warmup.jsonl').read_text().splitlines()) == 2
    for file in run.folder.glob('*'):
        assert 'test-secret-value' not in file.read_text()


def test_rps_drops_saturated_slots(tmp_path, monkeypatch):
    original = httpx.AsyncClient
    async def handle(req):
        await asyncio.sleep(.12)
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Chunks([chat()]))
    monkeypatch.setattr('backend.benchmark.httpx.AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    run = BenchmarkRun(BenchmarkConfig(model='test', stages=[Stage(concurrency=1, requests=10, rps=100)]), tmp_path)
    record = asyncio.run(run.execute('http://test/v1', 'secret'))
    assert record['summary']['skipped_slots'] > 0
    assert record['summary']['planned_slots'] == 10
    assert record['summary']['requests'] + record['summary']['skipped_slots'] == 10


def test_stop_cancels_requests_and_preserves_partial(tmp_path, monkeypatch):
    original = httpx.AsyncClient
    monkeypatch.setattr('backend.benchmark.httpx.AsyncClient', lambda **kw: original(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Chunks([': ping\n\n'] * 100, .02))), **kw))
    run = BenchmarkRun(BenchmarkConfig(model='test', stages=[Stage(concurrency=4, requests=100)]), tmp_path)
    async def scenario():
        task = asyncio.create_task(run.execute('http://test/v1', 'secret'))
        await asyncio.sleep(.08)
        run.stop(True)
        return await task
    rec = asyncio.run(scenario())
    assert rec['status'] == 'stopped'
    assert rec['summary']['requests'] == 4
    assert rec['summary']['errors']['cancelled'] == 4


def test_compare_zero_and_conditions():
    a = {'config': {'model': 'a'}, 'summary': {'metrics': {'ttft_ms': {'p95': 0}}}}
    b = {'config': {'model': 'b'}, 'summary': {'metrics': {'ttft_ms': {'p95': 4}}}}
    result = compare(a, b)
    assert result['metrics']['ttft_ms']['p95']['change_pct'] is None
    assert result['condition_differences']['model']['candidate'] == 'b'


def test_api_history_and_invalid_config(tmp_path):
    app = create_app(config_path=tmp_path / 'config', results_root=tmp_path)
    with TestClient(app) as client:
        assert client.get('/api/benchmarks').json() == {'items': []}
        assert client.post('/api/benchmarks', json={'config': {'model': 't', 'stages': [{'concurrency': 0}]}}).status_code == 400
        assert client.get('/api/benchmarks/missing').status_code == 404
        assert client.get('/api/benchmarks/compare?baseline=..&candidate=a').status_code == 404
        run = BenchmarkRun(BenchmarkConfig(model='t'), tmp_path)
        assert client.get('/api/benchmarks/' + run.run_id).json()['status'] == 'validating'
        run._lease.close()
        data = client.get('/api/benchmarks/' + run.run_id).json()
        assert data['status'] == 'interrupted'


def test_tpot_explicit_tokenizer_only(monkeypatch):
    class Encoding:
        def encode(self, value, **kwargs):
            return value.split()
    monkeypatch.setattr('backend.benchmark.tokenizer', lambda name: Encoding())
    p = StreamMeasurement('chat/completions', capture_text=True)
    p.text('one two ', 1000)
    p.text('three four', 1600)
    r = p.result(BenchmarkConfig(model='test', tokenizer_encoding='cl100k_base'))
    assert r['body_tokens_estimated'] == 4 and r['tpot_estimated_ms'] == 200
    assert r['itl_ms'] is None


def test_upstream_error_redacted():
    row, _ = request_with([json.dumps({'error': {'message': 'bad test-secret-value https://example.com/?sig=private', 'code': 'InvalidKey'}})], status=401)
    assert row['upstream_error']['code'] == 'InvalidKey'
    assert 'test-secret-value' not in json.dumps(row) and 'sig=private' not in json.dumps(row)


def test_event_name_cannot_leak_key():
    row, _ = request_with(['event: test-secret-value\n' + chat()])
    assert 'test-secret-value' not in json.dumps(row)


def test_selfcheck_excluded_from_performance(tmp_path, monkeypatch):
    original = httpx.AsyncClient
    async def handle(req):
        body = json.loads(req.content)
        code = 401 if 'authorization' not in req.headers else 400 if not isinstance(body.get('messages'), list) else 404 if body['model'].startswith('llmeval-nonexistent-') else 200
        return httpx.Response(code, headers={'content-type': 'text/event-stream'}, stream=Chunks([chat()] if code == 200 else ['{}']))
    monkeypatch.setattr('backend.benchmark.httpx.AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    run = BenchmarkRun(BenchmarkConfig(model='test', suite='selfcheck', stages=[Stage(requests=2, concurrency=1)]), tmp_path)
    r = asyncio.run(run.execute('http://test/v1', 'secret'))
    assert r['summary']['requests'] == 2 and r['summary']['successes'] == 2
    assert all(c['status'] == 'passed' for c in r['checks'][-3:])
    assert len((run.folder / 'requests.jsonl').read_text().splitlines()) == 5


def test_benchmark_analyzer_has_separate_scope():
    from scripts.analyzer import build_diagnostic_digest, build_analyzer_messages
    digest = build_diagnostic_digest({'mode': 'benchmark', 'summary': {'errors': {'timeout': 2}}, 'model': 'test'})
    assert digest['health_level'] == 'warning'
    msgs = build_analyzer_messages(digest)
    assert 'SLO' in msgs[0]['content'] and 'tokenizer' in msgs[0]['content']


def test_cli_web_shared_lock_and_stop(tmp_path):
    from backend.benchmark_service import BenchmarkManager
    run = BenchmarkRun(BenchmarkConfig(model='test'), tmp_path)
    with pytest.raises(ValueError, match='CLI/Web'):
        BenchmarkRun(BenchmarkConfig(model='test'), tmp_path)
    manager = BenchmarkManager(tmp_path / 'config', tmp_path)
    assert manager.get(run.run_id)['status'] == 'validating'
    manager.stop(run.run_id, immediate=True)
    run.read_stop_control()
    assert run.cancel_requested
    run._lease.close()
    run._global_lease.close()
    assert manager.get(run.run_id)['status'] == 'interrupted'


def test_api_create_poll_export_and_c2pa(tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            data = chat().encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with TestClient(create_app(config_path=tmp_path/'config', results_root=tmp_path)) as client:
            response = client.post('/api/benchmarks', json={
                'profile_id': 'custom', 'custom_base_url': f'http://127.0.0.1:{server.server_port}/v1',
                'custom_api_key': 'integration-fake-key', 'config': {'model': 'test', 'stages': [{'concurrency': 3, 'requests': 6}]}})
            assert response.status_code == 200, response.text
            run_id = response.json()['run_id']
            for _ in range(200):
                result = client.get('/api/benchmarks/'+run_id).json()
                if result['status'] in ('completed', 'failed'):
                    break
                time.sleep(.01)
            assert result['status'] == 'completed' and result['summary']['successes'] == 6
            # Completion snapshot may be visible just before report files are published.
            for _ in range(100):
                if (tmp_path/'benchmark'/run_id/'requests.csv').is_file():
                    break
                time.sleep(.01)
            for kind in ('json', 'jsonl', 'csv', 'markdown'):
                export = client.get(f'/api/benchmarks/{run_id}/export/{kind}')
                assert export.status_code == 200 and 'integration-fake-key' not in export.text
            detail = client.get(f'/api/results/benchmark/{run_id}').json()
            assert detail['digest']['mode'] == 'benchmark'
            c2pa = client.post(f'/api/results/benchmark/{run_id}/c2pa').json()
            assert c2pa['execution'] == 'error' and c2pa['discovery'] == 'unknown'
    finally:
        server.shutdown()
        server.server_close()
