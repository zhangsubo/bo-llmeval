"""本地协议契约与端到端测试，不调用付费生成服务。"""
import base64
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.results_store import get_result_detail, list_results

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/test-openai-api.py'
KEY = 'media-test-secret-marker'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
MP4 = b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 2048


@pytest.fixture
def upstream():
    state = {'calls': [], 'scenario': 'ok', 'polls': 0}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def reply(self, data, code=200, mime='application/json', location=None):
            raw = json.dumps(data).encode() if isinstance(data, dict) else data
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(raw)))
            if location:
                self.send_header('Location', location)
            self.end_headers()
            self.wfile.write(raw)
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['calls'].append(('POST', self.path, dict(self.headers), body))
            if self.path.endswith('/chat/completions'):
                state['analyzer_payload'] = body
                return self.reply({'choices': [{'message': {'content': '接口诊断完成 ' + KEY}}]})
            if state['scenario'] == 'http_error':
                return self.reply({'code': 'InvalidApiKey', 'message': KEY}, 401)
            if self.path.endswith('/images/generations'):
                if state['scenario'] == 'empty':
                    return self.reply({'data': []})
                items = [{'url': state['base'] + '/image.png'} for _ in range(body.get('n', 1))]
                if state['scenario'] == 'large_base64':
                    items = [{'b64_json': base64.b64encode(PNG + b'0' * (2 * 1024 * 1024)).decode()}]
                if state['scenario'] == 'base64':
                    items = [{'url': '', 'b64_json': base64.b64encode(PNG).decode()}]
                if state['scenario'] == 'partial':
                    items.append({'url': state['base'] + '/bad.png'})
                if state['scenario'] == 'redirect':
                    items = [{'url': state['base'] + '/redirect'}]
                return self.reply({'data': items, 'usage': {'image_count': len(items)}})
            assert self.path == '/api/v1/services/aigc/video-generation/video-synthesis'
            return self.reply({'output': {} if state['scenario'] == 'missing_id' else {'task_id': 'task-test', 'task_status': 'PENDING'}})
        def do_GET(self):
            state['calls'].append(('GET', self.path, dict(self.headers), None))
            if self.path == '/image.png':
                return self.reply(PNG, mime='image/png')
            if self.path == '/bad.png':
                return self.reply(b'<html>not an image</html>', mime='image/png')
            if self.path == '/redirect':
                return self.reply(b'', 302, location='/image.png')
            if self.path == '/video.mp4':
                return self.reply(MP4, mime='video/mp4')
            assert self.path == '/api/v1/tasks/task-test'
            state['polls'] += 1
            status = 'RUNNING' if state['polls'] == 1 else 'SUCCEEDED'
            if state['scenario'] == 'timeout':
                status = 'RUNNING'
            if state['scenario'] == 'failed':
                status = 'FAILED'
            output = {'task_id': 'task-test', 'task_status': status, 'video_url': state['base'] + '/video.mp4'}
            if state['scenario'] == 'failed':
                output.update(code='InvalidParameter', message=KEY)
            if state['scenario'] == 'wrong_id':
                output['task_id'] = 'other-task'
            if state['scenario'] == 'no_url':
                output.pop('video_url')
            return self.reply({'output': output, 'usage': {'duration': 5, 'video_count': 1}})
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    state['base'] = f'http://127.0.0.1:{server.server_port}'
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state
    server.shutdown()
    server.server_close()
    thread.join()


def execute(tmp_path, upstream, api='images/generations', model='qwen-image-3.0', extra=(), stdin=''):
    env = dict(os.environ, OPENAI_API_KEY=KEY, OPENAI_BASE_URL=upstream['base'] + ('/compatible-mode/v1' if api == 'images/generations' else '/v1'), OPENAI_MODEL_ID=model)
    dest = tmp_path / api.replace('/', '-')
    cmd = [sys.executable, str(SCRIPT), '--config', os.devnull, '--api-type', api,
           '--prompt', '生成测试', '--output', str(dest), '--poll-interval', '0.01', '--max-wait', '5', *extra]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, input=stdin, timeout=15)
    reports = list(dest.glob('*/result.json'))
    assert KEY not in result.stdout + result.stderr
    if not reports:
        return result, None, None
    saved = reports[0].read_text()
    assert KEY not in saved + reports[0].with_name('summary.txt').read_text()
    return result, json.loads(saved), reports[0].parent


@pytest.mark.parametrize('scenario', ['ok', 'base64', 'redirect'])
def test_images_download_and_detail(tmp_path, upstream, scenario):
    upstream['scenario'] = scenario
    result, data, folder = execute(tmp_path, upstream, extra=['--image-count', '2', '--size', '1024x1024', '--seed', '0', '--negative-prompt', '模糊', '--reference-image', 'https://example.com/input.png'])
    assert result.returncode == 0, result.stdout + result.stderr
    assert (folder / 'image-1.png').read_bytes() == PNG
    assert (folder / 'image-1.png').stat().st_mode & 0o777 == 0o600
    body = upstream['calls'][0][3]
    assert body['image'] == ['https://example.com/input.png']
    assert body['seed'] == 0 and body['n'] == 2
    assert 'stream' not in body and 'input' not in body
    for method, path, headers, _ in upstream['calls']:
        assert headers.get('Authorization') == ('Bearer ' + KEY if method == 'POST' else None)
    detail = get_result_detail('images-generations', folder.name, tmp_path)
    assert detail['images'][0]['local_url'].endswith('/image-1.png')
    assert 'ttft_ms' not in detail['digest']['timing_analysis']
    assert detail['digest']['media']['visual_quality_evaluated'] is False


@pytest.mark.parametrize('scenario,outcome', [('partial','completed_download_failed'),('empty','failed'),('http_error','failed')])
def test_image_failures(tmp_path, upstream, scenario, outcome):
    upstream['scenario'] = scenario
    result, data, folder = execute(tmp_path, upstream)
    assert result.returncode != 0
    assert data['outcome'] == outcome
    assert not list(folder.glob('*.partial'))
    if scenario == 'partial':
        assert (folder / 'image-1.png').exists()
        assert not (folder / 'image-2.png').exists()
        digest = get_result_detail('images-generations', folder.name, tmp_path)['digest']
        assert any('部分图片保存失败' in f for f in digest['auto_findings'])
        assert not any('--task-id' in f for f in digest['auto_findings'])


@pytest.mark.parametrize('model', ['wan2.7-t2v-2026-06-12','wan2.6-i2v-flash'])
def test_bailian_video_contract(tmp_path, upstream, model):
    extra = ['--duration','6','--resolution','1080p','--no-prompt-extend','--seed','0']
    if model == 'wan2.6-i2v-flash':
        extra += ['--reference-image','https://example.com/first.png','--no-generate-audio']
    result, data, folder = execute(tmp_path, upstream, 'bailian-video', model, extra)
    assert result.returncode == 0, result.stdout + result.stderr
    create = upstream['calls'][0]
    assert create[2]['X-Dashscope-Async'] == 'enable'
    body = create[3]
    assert body['input']['prompt'] == '生成测试'
    assert body['parameters']['resolution'] == '1080P'
    assert body['parameters']['duration'] == 6
    assert body['parameters']['prompt_extend'] is False
    if model == 'wan2.6-i2v-flash':
        assert body['input']['img_url'] == 'https://example.com/first.png'
        assert body['parameters']['audio'] is False
        assert 'ratio' not in body['parameters']
    else:
        assert 'audio' not in body['parameters']
    assert data['task_id'] == 'task-test' and data['usage']['duration'] == 5
    assert data['usage_status'] == 'reported' and data['usage_source'] == '$.usage'
    assert data['completion_ms'] >= data['create_ms']
    assert (folder / 'video.mp4').read_bytes() == MP4
    assert upstream['calls'][-1][2].get('Authorization') is None
    detail = get_result_detail('bailian-video', folder.name, tmp_path)
    assert detail['digest']['timing_analysis']['poll_count'] == 2
    assert list_results(tmp_path)[0]['prompt_preview'] == '生成测试'


@pytest.mark.parametrize('scenario,outcome', [('failed','failed'),('wrong_id','failed'),('missing_id','failed'),('no_url','completed_download_failed'),('timeout','timeout'),('http_error','failed')])
def test_bailian_failures(tmp_path, upstream, scenario, outcome):
    upstream['scenario'] = scenario
    extra = ['--max-wait','0.5'] if scenario == 'timeout' else []
    result, data, folder = execute(tmp_path, upstream, 'bailian-video','wan2.7-t2v-2026-06-12',extra)
    assert result.returncode != 0
    assert data['outcome'] == outcome
    assert sum(c[0] == 'POST' for c in upstream['calls']) == 1


def test_query_only(tmp_path, upstream):
    result, data, folder = execute(tmp_path, upstream, 'bailian-video', '', ['--task-id','task-test'])
    assert result.returncode == 0, result.stdout
    assert all(c[0] == 'GET' for c in upstream['calls'])
    assert data['query_only'] and 'query_completion_ms' in data


@pytest.mark.parametrize('api,model,extra', [
    ('images/generations','qwen-image-3.0',['--image-count','7']),
    ('images/generations','qwen-image-3.0',['--size','10x10']),
    ('images/generations','qwen-image-3.0',['--stream']),
    ('bailian-video','wan2.6-i2v-flash',[]),
    ('bailian-video','wan2.7-t2v-2026-06-12',['--duration','2.5']),
    ('bailian-video','wan2.7-t2v-2026-06-12',['--resolution','4k']),
    ('bailian-video','wan2.6-i2v-flash',['--reference-image','asset://asset-123']),
])
def test_invalid_args_do_not_submit(tmp_path, upstream, api, model, extra):
    result, data, folder = execute(tmp_path, upstream, api, model, extra)
    assert result.returncode != 0 and not upstream['calls']


@pytest.mark.parametrize('api,model,extra', [
    ('images/generations','qwen-image-3.0',{'image_count':2,'size':'1024x1024'}),
    ('bailian-video','wan2.7-t2v-2026-06-12',{}),
    ('bailian-video','wan2.6-i2v-flash',{'reference_image':['https://example.com/first.png']}),
])
def test_web_run_results_and_analysis(tmp_path, upstream, api, model, extra):
    cfg = tmp_path / 'config'
    cfg.write_text(f'ANALYZER_BASE_URL={upstream["base"]}/v1\nANALYZER_API_KEY={KEY}\nANALYZER_MODEL=mock-analyzer\n')
    app = create_app(config_path=cfg, results_root=tmp_path / 'results')
    with TestClient(app) as client:
        response = client.post('/api/tests/run', json=dict(profile_id='custom',custom_base_url=upstream['base']+'/v1',custom_api_key=KEY,api_type=api,model=model,prompt='Web测试',poll_interval=0.01,auto_analyze=True,**extra))
        assert response.status_code == 200, response.text
        run_id = response.json()['run_id']
        for _ in range(150):
            run = client.get('/api/tests/runs/'+run_id).json()
            if run.get('finished_at'):
                break
            time.sleep(0.05)
        assert run['status'] == 'completed', run
        assert run['analysis'] and '接口诊断完成' in run['analysis']['content']
        messages = upstream['analyzer_payload']['messages']
        assert '不得评价画质' in messages[0]['content']
        assert api in messages[1]['content']
        assert 'visual_quality_evaluated' in messages[1]['content']
        assert KEY not in json.dumps(run)
        detail = run['detail']
        if api == 'images/generations':
            url = detail['images'][0]['local_url']
            assert client.get(url).content == PNG
            assert client.get(url.replace('image-1.png','result.json')).status_code == 400
            path = Path(detail['folder_path']) / 'image-9.png'
            path.symlink_to(cfg)
            assert client.get(url.replace('image-1.png','image-9.png')).status_code == 404
        else:
            assert client.get(detail['video_url']).content == MP4


def test_bailian_interrupt_preserves_task(tmp_path, upstream):
    upstream['scenario'] = 'timeout'
    dest = tmp_path / 'cancel'
    env = dict(os.environ, OPENAI_API_KEY=KEY, OPENAI_BASE_URL=upstream['base']+'/v1', OPENAI_MODEL_ID='wan2.7-t2v-2026-06-12')
    process = subprocess.Popen([sys.executable, str(SCRIPT), '--config', os.devnull,
        '--api-type', 'bailian-video', '--prompt', '测试中断', '--output', str(dest), '--poll-interval','0.05'],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(100):
            if upstream['polls']:
                break
            time.sleep(0.03)
        assert upstream['polls']
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 130
        report = json.loads(next(dest.glob('*/result.json')).read_text())
        assert report['outcome'] == 'interrupted' and report['task_id'] == 'task-test'
        assert KEY not in stdout + stderr
        assert sum(c[0] == 'POST' for c in upstream['calls']) == 1
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


@pytest.mark.parametrize('model,extra,expected', [
    ('wanx-v2', ['--image-count','10','--quality','high','--background','transparent','--moderation','low','--response-format','url','--user','test-user'],
     {'n':10,'quality':'high','background':'transparent','moderation':'low','response_format':'url','user':'test-user'}),
    ('cogview-4', ['--quality','medium','--size','768x1344'], {'quality':'medium','size':'768x1344','n':1}),
    ('cogview-3-plus', ['--style','natural'], {'style':'natural','n':1}),
])
def test_nexhina_image_parameters(tmp_path, upstream, model, extra, expected):
    result, report, folder = execute(tmp_path, upstream, model=model, extra=extra + ['--image','https://example.com/input.png'])
    assert result.returncode == 0, result.stdout + result.stderr
    body = upstream['calls'][0][3]
    assert body['image'] == 'https://example.com/input.png'
    assert all(body[k] == v for k, v in expected.items())
    assert not {'prompt_extend','watermark','seed','negative_prompt'}.intersection(body)
    assert len(report['images']) == expected['n']


@pytest.mark.parametrize('model,extra', [
    ('wanx-v2',['--image-count','11']),
    ('cogview-3-plus',['--image-count','2']),
    ('cogview-4',['--size','auto']),
    ('cogview-4',['--background','transparent']),
    ('wanx-v2',['--style','natural']),
    ('wanx-v2',['--prompt-extend']),
    ('wanx-v2',['--image','https://example.com/1.png','--image','https://example.com/2.png']),
])
def test_nexhina_invalid_parameters(tmp_path, upstream, model, extra):
    result, _, _ = execute(tmp_path, upstream, model=model, extra=extra)
    assert result.returncode != 0 and not upstream['calls']


def test_large_base64_input_output_and_report(tmp_path, upstream):
    upstream['scenario'] = 'large_base64'
    reference = 'data:image/png;base64,' + base64.b64encode(PNG + b'1' * 200000).decode()
    result, report, folder = execute(tmp_path, upstream, model='wanx-v2',
        extra=['--image-input-stdin','--response-format','b64_json'], stdin=json.dumps([reference]))
    assert result.returncode == 0, result.stdout + result.stderr
    assert upstream['calls'][0][3]['image'] == reference
    assert (folder / 'image-1.png').stat().st_size > 2 * 1024 * 1024
    assert (folder / 'result.json').stat().st_size < 10000
    assert 'Base64 image omitted' in report['request_body']['image']


def test_web_nexhina_reference_stdin_and_parameters(tmp_path, upstream):
    reference = 'data:image/png;base64,' + base64.b64encode(PNG + b'2' * 200000).decode()
    cfg = tmp_path / 'config'
    cfg.write_text('')
    with TestClient(create_app(config_path=cfg, results_root=tmp_path / 'results')) as client:
        result = client.post('/api/tests/run', json={
            'profile_id':'custom','custom_base_url':upstream['base']+'/v1','custom_api_key':KEY,
            'api_type':'images/generations','model':'wanx-v2','prompt':'测试通用图片',
            'reference_image':[reference],'quality':'high','response_format':'url','image_count':2,
            'background':'transparent','moderation':'auto','user':'web-user'})
        assert result.status_code == 200
        run_id = result.json()['run_id']
        for _ in range(200):
            run = client.get('/api/tests/runs/'+run_id).json()
            if run.get('finished_at'):
                break
            time.sleep(.05)
        assert run['status'] == 'completed', run
        body = upstream['calls'][0][3]
        assert body['image'] == reference and body['quality'] == 'high' and body['user'] == 'web-user'
        assert 'prompt_extend' not in body and len(run['detail']['images']) == 2
        assert reference not in json.dumps(run)
