"""本地 HTTP 测试，不访问真实素材库。"""
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/test-volcengine-assets.py'
spec = importlib.util.spec_from_file_location('assets', SCRIPT)
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)
AK, SK, TOKEN = 'fake-access-marker', 'fake-secret-marker', 'fake-token-marker'


@pytest.mark.parametrize('endpoint', ['https://example.com/api/v3', 'https://example.com/v1',
    'https://example.com/business-api', 'https://example.com/?a=1', 'https://user:pass@example.com',
    'https://example.com/#x', 'file:///tmp/test', 'http://example.com:bad'])
def test_reject_non_root(endpoint):
    with pytest.raises(ValueError):
        assets.endpoint_root(endpoint)


@pytest.mark.parametrize('endpoint', ['https://example.com', 'https://example.com/', 'http://localhost:1234'])
def test_root(endpoint):
    assert assets.endpoint_root(endpoint) == endpoint.rstrip('/') + '/'


def test_signature():
    body = b'{"ProjectName":"default"}'
    url = 'https://example.com/?Action=ListAssetGroups&Version=2024-01-01'
    headers = assets.signed_headers(url, body, AK, SK, 'cn-beijing', TOKEN, '20261020T010203Z')
    # 独立构建官方文档指定的 canonical request，固定时间避免与本机时钟耦合。
    digest = hashlib.sha256(body).hexdigest()
    names = 'content-type;host;x-content-sha256;x-date;x-security-token'
    canonical = (f'POST\n/\nAction=ListAssetGroups&Version=2024-01-01\n'
                 f'content-type:application/json\nhost:example.com\nx-content-sha256:{digest}\n'
                 f'x-date:20261020T010203Z\nx-security-token:{TOKEN}\n\n{names}\n{digest}')
    key = SK.encode()
    for part in ['20261020', 'cn-beijing', 'ark', 'request']:
        key = hmac.digest(key, part.encode(), 'sha256')
    signing = 'HMAC-SHA256\n20261020T010203Z\n20261020/cn-beijing/ark/request\n' + hashlib.sha256(canonical.encode()).hexdigest()
    expected = hmac.new(key, signing.encode(), 'sha256').hexdigest()
    assert headers['Authorization'].endswith('Signature=' + expected)
    assert headers['X-Security-Token'] == TOKEN


@pytest.mark.parametrize('scenario', ['success', 'failed', 'business_error', 'http_error', 'invalid_json', 'redirect', 'timeout', 'missing_id'])
@pytest.mark.parametrize('auth', ['aksk', 'bearer'])
def test_http_flow(tmp_path, scenario, auth):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append((self.path, dict(self.headers), body))
            action = parse_qs(urlsplit(self.path).query)['Action'][0]
            result = {'Id': 'asset-test'}
            code = 200
            if action == 'GetAsset':
                result['Status'] = 'Active' if scenario != 'timeout' else 'Processing'
                result['URL'] = 'https://example.com/a?X-Tos-Signature=download-secret'
            data = {'Result': result}
            if scenario == 'failed':
                data = {'Result': {'Status': 'Failed', 'Error': {'Message': SK}}}
            if scenario == 'business_error':
                data = {'ResponseMetadata': {'Error': {'Message': SK + TOKEN + AK}}}
            if scenario == 'missing_id':
                data = {'Result': {}}
            if scenario == 'http_error':
                code = 403
            if scenario == 'redirect':
                code = 307
            raw = json.dumps(data).encode() if scenario != 'invalid_json' else SK.encode()
            self.send_response(code)
            if scenario == 'redirect':
                self.send_header('Location', '/leak')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        run = subprocess.run([sys.executable, str(SCRIPT), '--endpoint', f'http://127.0.0.1:{server.server_port}',
            '--auth', auth, '--action', 'CreateAsset', '--group-id', 'group-test', '--url', 'https://example.com/image.png',
            '--asset-type', 'Image', '--wait', '--poll-interval', '0.01',
            # 普通协议测试预留调度时间，只有 timeout 场景使用短截止时间。
            '--max-wait', '0.06' if scenario == 'timeout' else '5',
            '--output-dir', str(tmp_path)], env={**os.environ, 'VOLC_ACCESS_KEY': AK,
            'VOLC_SECRET_KEY': SK, 'VOLC_SECURITY_TOKEN': TOKEN, 'VOLC_ASSET_API_KEY': TOKEN}, text=True, capture_output=True, timeout=15)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert run.returncode == (0 if scenario == 'success' else 1), run.stderr
    saved = next(tmp_path.rglob('result.json')).read_text()
    for secret in (AK, SK, TOKEN, 'download-secret'):
        assert secret not in run.stdout + run.stderr + saved
    for path, headers, body in calls:
        assert urlsplit(path).path == '/'
        assert parse_qs(urlsplit(path).query)['Version'] == ['2024-01-01']
        assert headers['Authorization'].startswith('HMAC-SHA256 Credential=' if auth == 'aksk' else 'Bearer ')
        assert body['ProjectName'] == 'default'
    if scenario == 'success':
        assert len(calls) == 2
        assert json.loads(saved)['asset_uri'] == 'asset://asset-test'
    if scenario == 'redirect':
        assert len(calls) == 1


def test_payload_and_options(tmp_path):
    body = tmp_path / 'body.json'
    body.write_text('{"ProjectName":"custom","Filter":{"Name":"demo"},"PageNumber":2}')
    args = assets.make_parser().parse_args(['--body-file', str(body)])
    assert assets.build_payload(args)['ProjectName'] == 'custom'
    assert assets.build_payload(args)['PageNumber'] == 2
    args.project_name = 'override'
    assert assets.build_payload(args)['ProjectName'] == 'override'
    args.wait = True
    with pytest.raises(ValueError, match='--wait'):
        assets.build_payload(args)


@pytest.mark.parametrize('action', assets.ACTIONS)
def test_all_actions_payload(action, monkeypatch):
    monkeypatch.setenv('VOLC_BYTED_TOKEN', 'fake-session-marker')
    argv = ['--action', action]
    if action == 'CreateVisualValidateSession':
        argv += ['--callback-url', 'https://example.com/callback']
    if action.startswith(('Get', 'Update', 'Delete')):
        argv += ['--id', 'test-id']
    if action == 'CreateAssetGroup':
        argv += ['--name', '测试组']
    if action == 'CreateAsset':
        argv += ['--group-id', 'test-group', '--url', 'https://example.com/image.png', '--asset-type', 'Image']
    assert assets.build_payload(assets.make_parser().parse_args(argv))['ProjectName'] == 'default'


@pytest.mark.parametrize('action,result,ok', [
    ('CreateVisualValidateSession', {'BytedToken': 'fake-session-marker', 'H5Link': 'https://example.com/h5?pl=fake-link-marker'}, True),
    ('CreateVisualValidateSession', {'BytedToken': 'fake-session-marker'}, False),
    ('CreateVisualValidateSession', {'BytedToken': 'fake-session-marker', 'H5Link': 'javascript:bad'}, False),
    ('GetVisualValidateResult', {'GroupId': 'group-real-test'}, True),
    ('GetVisualValidateResult', {}, False),
    ('GetVisualValidateResult', {'Error': {'Message': 'expired fake-session-marker'}}, False),
])
def test_portrait_http(tmp_path, action, result, ok):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append((self.path, payload))
            raw = json.dumps({'Result': result}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    argv = [sys.executable, str(SCRIPT), '--endpoint', f'http://127.0.0.1:{server.server_port}',
            '--action', action, '--project-name', 'portrait-project', '--output-dir', str(tmp_path)]
    if action == 'CreateVisualValidateSession':
        argv += ['--callback-url', 'https://example.com/callback', '--show-validation-session']
    try:
        run = subprocess.run(argv, env={**os.environ, 'VOLC_ACCESS_KEY': AK, 'VOLC_SECRET_KEY': SK,
            'VOLC_BYTED_TOKEN': 'fake-session-marker'}, capture_output=True, text=True, timeout=5)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert run.returncode == (0 if ok else 1), run.stderr
    path, body = calls[0]
    assert urlsplit(path).path == '/'
    assert parse_qs(urlsplit(path).query)['Action'] == [action]
    assert body['ProjectName'] == 'portrait-project'
    assert 'Id' not in body and 'GroupType' not in body
    saved = next(tmp_path.rglob('result.json')).read_text()
    assert 'fake-session-marker' not in saved
    assert 'fake-link-marker' not in saved
    assert AK not in saved + run.stdout and SK not in saved + run.stdout
    if action == 'CreateVisualValidateSession' and ok:
        assert 'H5Link: https://example.com/h5?pl=fake-link-marker' in run.stdout
    else:
        assert 'fake-session-marker' not in run.stdout
    if action == 'GetVisualValidateResult':
        assert body['BytedToken'] == 'fake-session-marker'
        if ok:
            assert json.loads(saved)['group_id'] == 'group-real-test'


@pytest.mark.parametrize('url', ['javascript:bad', 'https://u:p@example.com/callback', 'https://example.com/#x'])
def test_bad_callback(url):
    args = assets.make_parser().parse_args(['--action', 'CreateVisualValidateSession', '--callback-url', url])
    with pytest.raises(ValueError, match='CallbackURL'):
        assets.build_payload(args)


def test_portrait_missing_token(monkeypatch):
    monkeypatch.delenv('VOLC_BYTED_TOKEN', raising=False)
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: False)
    with pytest.raises(ValueError, match='VOLC_BYTED_TOKEN'):
        assets.build_payload(assets.make_parser().parse_args(['--action', 'GetVisualValidateResult']))


def test_session_redaction():
    data = {'BytedToken': 'secret', 'H5Link': 'https://example.com/?pl=secret',
            'CallbackURL': 'https://example.com/?bytedToken=secret&resultCode=10000'}
    assert 'secret' not in json.dumps(assets.sanitize(data, []))


@pytest.mark.parametrize('action', ['ListAssetGroups', 'ListAssets'])
def test_list_type_cli_and_json_priority(action):
    parser = assets.make_parser()
    args = parser.parse_args(['--action', action])
    assert assets.build_payload(args)['Filter'] == {'GroupType': 'AIGC'}
    original = {'Filter': {'GroupType': 'LivenessFace', 'Name': 'demo', 'GroupIds': ['group-test']}}
    assert assets.build_payload(args, initial_body=original)['Filter'] == original['Filter']
    args = parser.parse_args(['--action', action, '--group-type', 'AIGC'])
    assert assets.build_payload(args, initial_body=original)['Filter'] == {
        'GroupType': 'AIGC', 'Name': 'demo', 'GroupIds': ['group-test']}
    assert original['Filter']['GroupType'] == 'LivenessFace'
    args = parser.parse_args(['--action', action, '--group-type', 'LivenessFace'])
    assert assets.build_payload(args)['Filter']['GroupType'] == 'LivenessFace'
