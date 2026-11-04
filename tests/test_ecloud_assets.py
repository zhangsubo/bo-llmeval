"""移动云 REST 合约与真实本地 HTTP 回归，不调用云端。"""
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from fastapi.testclient import TestClient
import pytest

from backend.main import create_app
from scripts import ecloud_assets as cloud


@pytest.fixture
def supplier():
    calls = []
    behavior = {'state': 'OK', 'status': 'ACTIVE'}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def handle_request(self):
            url = urlsplit(self.path)
            params = {k: v[0] for k, v in parse_qs(url.query).items()}
            signature = params.pop('Signature')
            canonical = '&'.join(f'{quote(k, safe="~")}={quote(v, safe="~")}' for k, v in sorted(params.items()))
            source = f'{self.command}\n{quote(url.path, safe="~")}\n{hashlib.sha256(canonical.encode()).hexdigest()}'
            assert signature == hmac.new(b'BC_SIGNATURE&test-cloud-sk', source.encode(), hashlib.sha256).hexdigest()
            assert params['AccessKey'] == 'test-cloud-ak'
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or '{}')
            path = url.path.removeprefix(cloud.PREFIX)
            calls.append((self.command, path, body))
            if path.endswith('/sessions'):
                result = {'bytedToken': 'test-cloud-token', 'h5Link': 'https://example.com/auth?pl=test-session', 'expiresIn': 120}
            elif path.startswith('/real-person-auth/'):
                result = 'group-real'
            elif self.command == 'DELETE':
                result = True
            elif path.endswith('/query'):
                result = {'data': [], 'total': 0}
            elif path == '/asset':
                result = 'asset-test'
            elif path.startswith('/asset/'):
                result = {'assetId': 'asset-test', 'status': behavior['status']}
            else:
                result = {'groupId': 'group-test', 'groupType': 'AIGC'}
            data = {'state': behavior['state'], 'body': result, 'errorMessage': 'echo test-cloud-sk test-cloud-token' if behavior['state'] != 'OK' else None}
            if behavior.get('echo_url'):
                data['debug'] = self.path
            raw = json.dumps(data).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_POST = do_GET = do_PUT = do_DELETE = handle_request

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield f'http://127.0.0.1:{server.server_port}', calls, behavior
    server.shutdown()
    server.server_close()
    worker.join()


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(config_path=tmp_path / 'config', results_root=tmp_path / 'results'))


def run(client, endpoint, action, payload=None, **kwargs):
    return client.post('/api/assets/run', json={'provider': 'ecloud', 'endpoint': endpoint,
        'access_key': 'test-cloud-ak', 'secret_key': 'test-cloud-sk', 'action': action,
        'payload': payload or {}, **kwargs})


@pytest.mark.parametrize('action,payload,method,path', [
    ('ListAssetGroups', {'groupType': 'AIGC'}, 'POST', '/asset-group/query'),
    ('ListAssets', {'groupType': 'LivenessFace'}, 'POST', '/asset/query'),
    ('CreateAssetGroup', {'groupName': '测试组'}, 'POST', '/asset-group'),
    ('GetAssetGroup', {'id': 'group-test'}, 'GET', '/asset-group/group-test'),
    ('UpdateAssetGroup', {'id': 'group-test', 'description': '更新'}, 'PUT', '/asset-group/group-test'),
    ('DeleteAssetGroup', {'id': 'group-test'}, 'DELETE', '/asset-group/group-test'),
    ('CreateAsset', {'groupId': 'group-test', 'assetName': '图片', 'assetUrl': 'https://example.com/a.png', 'assetType': 'Image'}, 'POST', '/asset'),
    ('GetAsset', {'id': 'asset-test'}, 'GET', '/asset/asset-test'),
    ('UpdateAsset', {'id': 'asset-test', 'assetName': '更名'}, 'PUT', '/asset/asset-test'),
    ('DeleteAsset', {'id': 'asset-test'}, 'DELETE', '/asset/asset-test'),
    ('CreateVisualValidateSession', {}, 'POST', '/real-person-auth/sessions'),
    ('GetVisualValidateResult', {}, 'POST', '/real-person-auth/asset-group/by-byted-token'),
    ('ConfirmVisualValidateResult', {}, 'POST', '/real-person-auth/verification-result'),
])
def test_rest_contract(client, supplier, action, payload, method, path):
    endpoint, calls, _ = supplier
    response = run(client, endpoint, action, payload, byted_token='test-cloud-token', confirm_delete=True)
    assert response.status_code == 200, response.text
    assert response.json()['report']['success'], response.text
    assert calls[-1][:2] == (method, path)
    assert response.json()['report']['requests'][0]['method'] == method
    assert 'ProjectName' not in calls[-1][2]
    assert 'id' not in calls[-1][2]
    if action.startswith('List'):
        assert calls[-1][2]['pageNo'] == 1
    if action == 'CreateVisualValidateSession':
        assert response.json()['validation_session']['expires_in'] == 120
        assert 'test-cloud-token' not in json.dumps(response.json()['report'])


def test_upload_poll_reports_and_redaction(client, supplier, tmp_path):
    endpoint, calls, behavior = supplier
    behavior['echo_url'] = True
    response = run(client, endpoint, 'CreateAsset', {'groupId': 'group-test', 'assetName': '图片',
        'assetUrl': 'https://example.com/a.png', 'assetType': 'Image'}, wait=True, poll_interval=1)
    data = response.json()
    assert data['report']['asset_uri'] == 'asset://asset-test'
    assert [c[0] for c in calls] == ['POST', 'GET']
    assert data['category'] == 'ecloud-assets'
    reports = list((tmp_path / 'results' / 'ecloud-assets').rglob('result.json'))
    assert len(reports) == 1
    saved = reports[0].read_text()
    assert 'test-cloud-ak' not in saved and 'test-cloud-sk' not in saved
    assert 'Signature=[REDACTED]' in saved
    detail = client.get(f'/api/results/{data["category"]}/{data["folder"]}')
    assert detail.status_code == 200


def test_failures_and_credentials_are_not_persisted(client, supplier, tmp_path):
    endpoint, _, behavior = supplier
    for state in ('ERROR', 'unexpected'):
        behavior['state'] = state
        response = run(client, endpoint, 'GetVisualValidateResult', byted_token='test-cloud-token')
        assert not response.json()['report']['success']
    behavior.update(state='OK', status='FAILED')
    assert not run(client, endpoint, 'GetAsset', {'id': 'asset-test'}).json()['report']['success']
    behavior['status'] = 'UNKNOWN'
    assert not run(client, endpoint, 'GetAsset', {'id': 'asset-test'}).json()['report']['success']
    for file in (tmp_path / 'results').rglob('*'):
        if file.is_file():
            assert not any(v in file.read_text() for v in ('test-cloud-sk', 'test-cloud-ak', 'test-cloud-token'))


@pytest.mark.parametrize('action,payload,extra', [
    ('CreateAsset', {'groupId': 'g', 'assetUrl': 'https://example.com/a.png', 'assetType': 'Image'}, {}),
    ('CreateAssetGroup', {'groupType': 'LivenessFace', 'groupName': 'x'}, {}),
    ('CreateVisualValidateSession', {'callbackUrl': 'https://example.com/callback?q=x'}, {}),
    ('GetAsset', {'id': '../escape'}, {}),
    ('ListAssets', {'groupType': 'AIGC', 'pageNo': 0}, {}),
    ('ListAssets', {'groupType': 'AIGC'}, {'auth': 'bearer', 'api_key': 'fake'}),
    ('DeleteAsset', {'id': 'asset-test'}, {}),
    ('GetVisualValidateResult', {}, {}),
    ('ListAssetGroups', {'groupType': 'AIGC'}, {'wait': True}),
])
def test_invalid_input_does_not_reach_supplier(client, supplier, action, payload, extra):
    endpoint, calls, _ = supplier
    assert run(client, endpoint, action, payload, **extra).status_code == 400
    assert not calls


def test_signature_fixed_vector():
    query = cloud.signed_query('GET', '/api/keypair', 'testid', 'testsecret', '2017-01-11T15:15:11Z', 'nonce')
    params = parse_qs(query)
    assert params['SignatureMethod'] == ['HmacSHA256']
    assert params['SignatureVersion'] == ['V2.0']
    canonical = urlencode(sorted((k, v[0]) for k, v in params.items() if k != 'Signature'), quote_via=quote)
    expected = hmac.new(b'BC_SIGNATURE&testsecret', ('GET\n%2Fapi%2Fkeypair\n' + hashlib.sha256(canonical.encode()).hexdigest()).encode(), hashlib.sha256).hexdigest()
    assert params['Signature'] == [expected]


def test_timestamp_uses_gateway_beijing_wall_clock(monkeypatch):
    """网关实测：UTC+Z 被拒绝；北京时间+字面量 Z 进入 AccessKey 校验。"""
    from datetime import datetime, timezone

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = datetime(2026, 11, 4, 20, 2, 8, tzinfo=timezone.utc)
            return instant.astimezone(tz) if tz is not None else instant.replace(tzinfo=None)

    monkeypatch.setattr(cloud, 'datetime', FrozenDateTime)
    query = cloud.signed_query('POST', cloud.PREFIX + '/asset-group', 'test-ak', 'test-sk')
    assert parse_qs(query)['Timestamp'] == ['2026-11-05T04:02:08Z']
