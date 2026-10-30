"""Alibaba 网关格式与本地模拟端到端契约；不调用外部生成服务。"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/test-openai-api.py'
spec = importlib.util.spec_from_file_location('video_script_alibaba', SCRIPT)
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)
MODEL = 'wan2.6-i2v-flash'
IMAGE = 'https://example.com/bear.png'
KEY = 'offline-alibaba-test-marker'
MP4 = b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 2048


def args(**overrides):
    values = dict(prompt='小熊吃蛋糕', reference_image=[IMAGE], reference_video=[], reference_audio=[],
                  content=False, duration=None, seconds=5, size=None, resolution='720p', ratio='16:9',
                  generate_audio=True, watermark=False, first_frame=None, last_frame=None)
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize('audio', [True, False])
def test_top_level_mapping_and_false(audio):
    assert script.alibaba_video_payload(args(generate_audio=audio), MODEL) == {
        'model': MODEL, 'prompt': '小熊吃蛋糕', 'input_reference': IMAGE, 'seconds': 5,
        'resolution': '720p', 'audio': audio, 'watermark': False,
    }
    assert script.alibaba_video_payload(args(duration=10), MODEL)['seconds'] == 10


def test_default_seedance_and_other_models_are_not_rewritten():
    original = script.video_payload(args(), MODEL)
    assert original['content'][1]['image_url']['url'] == IMAGE
    assert original['generate_audio'] is True and original['ratio'] == '16:9'
    assert 'audio' not in original and 'image' not in original
    native = script.seedance_payload(args(), 'seedance-model')
    assert native['duration'] == 5 and 'content' in native and 'seconds' not in native
    images = [IMAGE, 'https://example.com/last.png']
    other = script.alibaba_video_payload(args(reference_image=images), 'wan2.7-i2v')
    assert other['images'] == images  # 不将 flash 单图限制扩散到其他模型，也不截取第一张。
    assert 'image' not in other


@pytest.fixture
def gateway():
    state = {'calls': [], 'polls': 0, 'scenario': 'ok'}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, value, status=200):
            data = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'video/mp4' if isinstance(value, bytes) else 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['calls'].append(('POST', self.path, self.headers.get('Authorization'), body))
            assert self.path == '/v1/videos'
            assert body['input_reference'] == IMAGE and 'content' not in body and 'generate_audio' not in body
            self.reply({'id': 'task-offline', 'status': 'queued'})

        def do_GET(self):
            state['calls'].append(('GET', self.path, self.headers.get('Authorization'), None))
            if self.path in ('/movie.mp4', '/v1/videos/task-offline/content'):
                if state['scenario'] == 'download_failed':
                    return self.reply({'error': KEY}, 403)
                return self.reply(MP4)
            assert self.path in ('/v1/video/generations/task-offline', '/v1/videos/task-offline')
            state['polls'] += 1
            status = 'IN_PROGRESS' if state['polls'] == 1 else 'SUCCESS'
            if state['scenario'] == 'failed':
                status = 'FAILURE'
            if self.path.startswith('/v1/videos/'):
                return self.reply({'id': 'task-offline', 'status': 'in_progress' if state['polls'] == 1 else 'completed'})
            detail = {'task_id': 'task-offline', 'status': status, 'fail_reason': KEY if status == 'FAILURE' else '',
                      'data': {'output': {'task_status': 'SUCCEEDED'},
                               'usage': {'duration': 5, 'SR': '720P', 'audio': False}}}
            if state['scenario'] != 'no_url':
                detail['result_url'] = state['base'] + '/movie.mp4'
            self.reply({'code': 'success', 'data': detail})

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    state['base'] = f'http://127.0.0.1:{server.server_port}'
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


def run_cli(tmp_path, gateway, extra=()):
    command = [sys.executable, str(SCRIPT), '--config', os.devnull, '--api-type', 'video',
               '--video-request-format', 'alibaba', '--resolution', '720p',
               '--prompt', '小熊吃蛋糕', '--no-generate-audio', '--no-watermark', '--seconds', '5',
               '--poll-interval', '0.01', '--max-wait', '5', '--output', str(tmp_path), *extra]
    if '--reference-image' not in extra:
        command.append('--no-reference')
    env = dict(os.environ, OPENAI_API_KEY=KEY, OPENAI_BASE_URL=gateway['base'] + '/v1', OPENAI_MODEL_ID=MODEL)
    return subprocess.run(command, env=env, input='', capture_output=True, text=True, timeout=10)


@pytest.mark.parametrize('scenario,query_api,outcome', [
    ('ok', 'generations', 'completed'), ('no_url', 'generations', 'completed'),
    ('ok', 'videos', 'completed'), ('failed', 'generations', 'failed'),
    ('download_failed', 'generations', 'completed_download_failed'),
])
def test_cli_gateway_poll_download_and_usage(tmp_path, gateway, scenario, query_api, outcome):
    gateway['scenario'] = scenario
    proc = run_cli(tmp_path, gateway, ['--reference-image', IMAGE, '--video-query-api', query_api])
    assert proc.returncode == (0 if outcome == 'completed' else 1 if outcome == 'failed' else 2), proc.stderr
    saved = next(tmp_path.glob('*/result.json')).read_text()
    result = json.loads(saved)
    assert result['outcome'] == outcome
    assert result['video_request_format'] == 'alibaba'
    assert result['request_body'] == gateway['calls'][0][3]
    assert result['request_body']['audio'] is False and 'ratio' not in result['request_body']
    assert KEY not in saved + proc.stdout + proc.stderr
    if query_api == 'generations':
        assert result['usage'] == {'duration': 5, 'SR': '720P', 'audio': False}
        assert result['usage_source'] == '$.data.data.usage'
    else:
        assert result['usage'] is None
    downloads = [c for c in gateway['calls'] if c[1] in ('/movie.mp4', '/v1/videos/task-offline/content')]
    if outcome == 'failed':
        assert not downloads
    elif scenario == 'no_url' or query_api == 'videos':
        assert downloads[0][1] == '/v1/videos/task-offline/content'
        assert downloads[0][2] == 'Bearer ' + KEY
    else:
        assert downloads[0][2] is None  # 直链下载不得携带网关凭据。


@pytest.mark.parametrize('images', [[], [IMAGE, IMAGE]])
def test_cli_rejects_missing_or_multiple_images_before_post(tmp_path, gateway, images):
    flags = [value for image in images for value in ('--reference-image', image)]
    proc = run_cli(tmp_path, gateway, flags)
    assert proc.returncode == 1
    assert '必须且只能提供 1 张参考图片' in proc.stderr
    assert gateway['calls'] == []
    assert not list(tmp_path.glob('*/result.json'))


def test_web_parameter_mapping_validation_and_query_only(tmp_path, gateway):
    app = create_app(config_path=tmp_path / 'missing-config', results_root=tmp_path / 'results')
    payload = dict(profile_id='custom', custom_base_url=gateway['base'] + '/v1', custom_api_key=KEY,
                   api_type='video', video_request_format='alibaba', model=MODEL, prompt='小熊吃蛋糕',
                   reference_image=[IMAGE], generate_audio=False, watermark=False, seconds=5,
                   resolution='720p', ratio='16:9', poll_interval=0.01, max_wait=5)
    with TestClient(app) as client:
        for invalid in ({'reference_image': []}, {'reference_image': [IMAGE, IMAGE]},
                        {'reference_audio': [IMAGE]}, {'content_array': True},
                        {'video_request_format': 'unknown'}, {'api_type': 'bailian-video'}):
            response = client.post('/api/tests/run', json={**payload, **invalid})
            assert response.status_code == 400, response.text
        assert gateway['calls'] == []
        for query_only in (False, True):
            body = {**payload, **({'task_id': 'task-offline', 'reference_image': [], 'model': ''} if query_only else {})}
            before = len(gateway['calls'])
            response = client.post('/api/tests/run', json=body)
            assert response.status_code == 200, response.text
            run_id = response.json()['run_id']
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                run = client.get('/api/tests/runs/' + run_id).json()
                if run.get('finished_at'):
                    break
                time.sleep(0.02)
            assert run['status'] == 'completed', run
            requests = gateway['calls'][before:]
            if query_only:
                assert all(c[0] == 'GET' for c in requests)
            else:
                assert requests[0][3]['audio'] is False
                assert requests[0][3]['watermark'] is False
                assert 'ratio' not in requests[0][3]
