"""Web 素材库的真实本地 HTTP 集成测试，不访问供应商。"""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
import pytest

from backend.main import create_app


@pytest.fixture
def supplier():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            action = parse_qs(urlsplit(self.path).query)['Action'][0]
            calls.append((self.path, body, self.headers['Authorization']))
            if action in ('ListAssetGroups', 'ListAssets') and body.get('Filter', {}).get('GroupType') not in ('AIGC', 'LivenessFace'):
                raw = json.dumps({'ResponseMetadata': {'Error': {
                    'Code': 'InvalidParameter', 'Message': 'Filter.GroupType is required.'}}}).encode()
                self.send_response(400)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            result = {}
            if action == 'CreateVisualValidateSession':
                result = {'H5Link': 'https://example.com/auth?pl=test-web-session-secret', 'BytedToken': 'test-web-token'}
            elif action == 'GetVisualValidateResult':
                result = {'GroupId': 'group-person-test'}
            elif action == 'CreateAsset':
                result = {'Id': 'asset-web-test'}
            elif action == 'GetAsset':
                result = {'Id': body['Id'], 'Status': 'Active'}
            elif action == 'ListAssetGroups':
                result = {'Items': [{'Id': 'group-person-test'}]}
            if body.get('Name') == 'fail':
                result = {'Error': {'Message': 'echo test-web-sk test-web-token', 'Code': 'TestError'}}
            raw = json.dumps({'Result': result}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', calls
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(config_path=tmp_path / 'config', results_root=tmp_path / 'results'))


def payload(endpoint, action='ListAssetGroups', **kwargs):
    return {'endpoint': endpoint, 'access_key': 'test-web-ak', 'secret_key': 'test-web-sk',
            'action': action, 'payload': {'ProjectName': 'portrait-project'}, **kwargs}


def test_portrait_to_asset_flow(client, supplier, tmp_path):
    endpoint, calls = supplier
    res = client.post('/api/assets/run', json=payload(endpoint, 'CreateVisualValidateSession',
        payload={'CallbackURL': 'https://example.com/callback', 'ProjectName': 'portrait-project'}))
    assert res.status_code == 200
    assert res.headers['cache-control'] == 'no-store'
    data = res.json()
    assert data['validation_session']['byted_token'] == 'test-web-token'
    assert 'test-web-token' not in json.dumps(data['report'])
    res = client.post('/api/assets/run', json=payload(endpoint, 'GetVisualValidateResult', byted_token='test-web-token'))
    group = res.json()['report']['group_id']
    assert group == 'group-person-test'
    res = client.post('/api/assets/run', json=payload(endpoint, 'CreateAsset', wait=True, poll_interval=1,
        payload={'GroupId': group, 'URL': 'https://example.com/image.png', 'AssetType': 'Image', 'ProjectName': 'portrait-project'}))
    assert res.json()['report']['asset_uri'] == 'asset://asset-web-test'
    reports = list((tmp_path / 'results').rglob('*.json'))
    assert len(reports) == 3
    for file in reports:
        assert all(secret not in file.read_text() for secret in ('test-web-sk', 'test-web-ak', 'test-web-token', 'test-web-session-secret'))
    assert all(urlsplit(path).path == '/' and body['ProjectName'] == 'portrait-project' for path, body, _ in calls)
    assert calls[1][1] == {'ProjectName': 'portrait-project', 'BytedToken': 'test-web-token'}
    history = client.get('/api/results', params={'category': 'volcengine-assets'}).json()
    assert history['total'] == 3
    assert all(item['outcome'] == 'completed' for item in history['items'])
    detail = client.get(f'/api/results/{data["category"]}/{data["folder"]}').json()
    assert 'test-web-token' not in json.dumps(detail)


@pytest.mark.parametrize('patch', [
    {'endpoint': 'https://example.com/api/v3'}, {'endpoint': 'https://example.com/v1'},
    {'endpoint': 'https://example.com/business-api'}, {'secret_key': ''}, {'max_wait': 601},
    {'action': 'unsupported'}, {'action': 'DeleteAsset', 'payload': {'Id': 'asset-test'}},
    {'action': 'GetVisualValidateResult'}, {'payload': {'ProjectName': ['bad']}},
    {'payload': {'BytedToken': 'test-web-token'}, 'action': 'GetVisualValidateResult'},
])
def test_invalid_requests_do_not_send(client, supplier, patch):
    endpoint, calls = supplier
    res = client.post('/api/assets/run', json={**payload(endpoint), **patch})
    assert res.status_code == 400
    assert not calls
    assert 'test-web-sk' not in res.text and 'test-web-token' not in res.text


def test_delete_confirmation_and_business_error(client, supplier):
    endpoint, calls = supplier
    res = client.post('/api/assets/run', json=payload(endpoint, 'DeleteAsset', confirm_delete=True, payload={'Id': 'asset-test'}))
    assert res.json()['report']['success']
    res = client.post('/api/assets/run', json=payload(endpoint, 'CreateAssetGroup', byted_token='test-web-token', payload={'Name': 'fail'}))
    assert not res.json()['report']['success']
    assert res.json()['report']['outcome'] == 'failed'
    assert 'test-web-sk' not in res.text and 'test-web-token' not in res.text
    assert len(calls) == 2


def test_concurrent_credentials_are_isolated(client, supplier):
    endpoint, calls = supplier
    def run(token):
        return client.post('/api/assets/run', json=payload(endpoint, 'GetVisualValidateResult',
            auth='bearer', api_key=f'bearer-{token}', byted_token=token)).json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ['one', 'two']))
    assert all(result['report']['success'] for result in results)
    assert len(calls) == 2
    for _, body, authorization in calls:
        assert authorization == f'Bearer bearer-{body["BytedToken"]}'


def test_create_without_wait_is_submitted(client, supplier):
    endpoint, _ = supplier
    response = client.post('/api/assets/run', json=payload(endpoint, 'CreateAsset',
        payload={'GroupId': 'group-test', 'AssetType': 'Image', 'URL': 'https://example.com/a.png'}))
    assert response.json()['report']['success']
    assert response.json()['report']['outcome'] == 'submitted'
    assert 'asset_uri' not in response.json()['report']


@pytest.mark.parametrize('action', ['ListAssetGroups', 'ListAssets'])
@pytest.mark.parametrize('group_type', [None, 'AIGC', 'LivenessFace'])
def test_list_group_type_sent(client, supplier, action, group_type):
    endpoint, calls = supplier
    body = {'ProjectName': 'default'}
    if group_type:
        body['Filter'] = {'GroupType': group_type, 'Name': 'portrait'}
    response = client.post('/api/assets/run', json=payload(endpoint, action, payload=body))
    assert response.json()['report']['success']
    assert calls[0][1]['Filter']['GroupType'] == (group_type or 'AIGC')
    if group_type:
        assert calls[0][1]['Filter']['Name'] == 'portrait'


@pytest.mark.parametrize('filters', [None, [], 'AIGC', {'GroupType': ''}, {'GroupType': 'Human'}])
def test_invalid_list_filters_blocked(client, supplier, filters):
    endpoint, calls = supplier
    response = client.post('/api/assets/run', json=payload(endpoint, payload={'Filter': filters}))
    assert response.status_code == 400
    assert not calls


@pytest.fixture
def keychain(monkeypatch):
    from backend import asset_credentials
    store = {}
    monkeypatch.setattr(asset_credentials, 'write_credentials', lambda path, ak, sk: store.update({str(path): (ak, sk)}))
    monkeypatch.setattr(asset_credentials, 'read_credentials', lambda path: store[str(path)])
    monkeypatch.setattr(asset_credentials, 'delete_credentials', lambda path: store.pop(str(path), None))
    return store


def test_saved_asset_config_roundtrip_and_run(client, supplier, tmp_path, keychain):
    endpoint, calls = supplier
    config = {'profiles': [], 'assets': {'endpoint': endpoint, 'access_key': 'test-web-ak', 'secret_key': 'test-web-sk'}}
    saved = client.put('/api/config', json=config)
    assert saved.status_code == 200, saved.text
    assert saved.json()['assets']['configured']
    assert 'test-web-ak' not in saved.text and 'test-web-sk' not in saved.text
    disk = (tmp_path / 'config').read_text()
    assert 'ASSET_ENDPOINT=' in disk and 'op://' in disk
    assert 'test-web-ak' not in disk and 'test-web-sk' not in disk
    assert (tmp_path / 'config').stat().st_mode & 0o777 == 0o600
    assert 'test-web-sk' not in client.get('/api/config').text
    for change in ({'profiles': []}, {'profiles': [], 'assets': {'endpoint': endpoint}}):
        assert client.put('/api/config', json=change).json()['assets']['configured']
        assert not keychain
        assert client.get('/api/config').json()['assets']['credential_store'].startswith('op://')
    # 保存素材库配置后，原模型测试 CLI 仍能读取同一配置文件。
    from backend.config_store import _load_script_module
    assert _load_script_module().load_profiles(tmp_path / 'config', required=True) == []
    res = client.post('/api/assets/run', json={'use_saved_credentials': True, 'action': 'ListAssetGroups',
                                             'payload': {'Filter': {'GroupType': 'AIGC'}}})
    assert res.status_code == 200 and res.json()['report']['success']
    assert 'test-web-ak' in calls[-1][2]
    assert 'test-web-ak' not in res.text and 'test-web-sk' not in res.text
    assert all('test-web-sk' not in f.read_text() for f in (tmp_path / 'results').rglob('*.json'))
    before = len(calls)
    res = client.post('/api/assets/run', json={'use_saved_credentials': True, 'endpoint': 'https://other.example.com'})
    assert res.status_code == 400 and len(calls) == before
    res = client.put('/api/config', json={'profiles': [], 'assets': {'endpoint': endpoint, 'clear_credentials': True}})
    assert not res.json()['assets']['configured'] and not keychain
    assert client.post('/api/assets/run', json={'use_saved_credentials': True}).status_code == 400


@pytest.mark.parametrize('asset_config', [
    {'endpoint': 'https://example.com/v1'},
    {'endpoint': 'https://example.com', 'access_key': 'test-ak'},
    {'access_key': 'test-ak', 'secret_key': 'test-sk'},
    {'endpoint': 'https://example.com', 'access_key': 'test-ak', 'secret_key': 'test-sk', 'clear_credentials': True},
])
def test_invalid_asset_config_does_not_write(client, tmp_path, keychain, asset_config):
    response = client.put('/api/config', json={'profiles': [], 'assets': asset_config})
    assert response.status_code == 400
    assert not (tmp_path / 'config').exists() and not keychain
    assert 'test-ak' not in response.text and 'test-sk' not in response.text


def test_onepassword_error_does_not_write_config(client, tmp_path, monkeypatch):
    from scripts import secret_store
    def fail(*args):
        raise ValueError('无法访问 macOS 钥匙串')
    monkeypatch.setattr(secret_store, 'create', fail)
    response = client.put('/api/config', json={'profiles': [], 'assets': {
        'endpoint': 'https://example.com', 'access_key': 'test-ak', 'secret_key': 'test-sk'}})
    assert response.status_code == 400 and not (tmp_path / 'config').exists()


def test_keychain_secrets_pass_through_stdin_only(tmp_path, monkeypatch):
    from backend import asset_credentials
    import base64
    from types import SimpleNamespace
    calls = []
    encoded = base64.b64encode(json.dumps({'access_key': 'fake-ak', 'secret_key': 'fake-sk'}).encode()).decode()
    def execute(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=encoded if 'find-generic-password' in args else '')
    monkeypatch.setattr(asset_credentials.sys, 'platform', 'darwin')
    monkeypatch.setattr(asset_credentials.subprocess, 'run', execute)
    asset_credentials.write_credentials(tmp_path / 'config', 'fake-ak', 'fake-sk')
    assert calls[0][0] == ['/usr/bin/security', '-i']
    assert encoded in calls[0][1]['input']
    assert all(encoded not in ' '.join(args) for args, _ in calls)


def test_multiple_asset_profiles_keep_identity_and_credentials(client, supplier, tmp_path, keychain):
    endpoint, calls = supplier
    from backend.asset_credentials import credential_path
    groups = [dict(name='第一组', endpoint=endpoint, access_key='group-a-ak', secret_key='group-a-sk'),
              dict(name='第二组', endpoint=endpoint, access_key='group-b-ak', secret_key='group-b-sk')]
    response = client.put('/api/config', json={'asset_profiles': groups})
    assert response.status_code == 200, response.text
    saved = response.json()['asset_profiles']
    a, b = saved
    assert a['id'] != b['id'] and all(p['configured'] for p in saved)
    assert 'group-a-ak' not in response.text and 'group-b-sk' not in (tmp_path/'config').read_text()
    # 不指定组时不猜测凭证；相同根地址也必须按 ID 路由。
    assert client.post('/api/assets/run', json={'use_saved_credentials': True}).status_code == 400
    for profile, marker in [(b, 'group-b-ak'), (a, 'group-a-ak')]:
        res = client.post('/api/assets/run', json={'asset_profile_id':profile['id'], 'action':'ListAssetGroups',
                                                 'payload':{'Filter':{'GroupType':'AIGC'}}})
        assert res.status_code == 200 and res.json()['report']['success']
        assert marker in calls[-1][2] and marker not in res.text
    # 调整顺序和名称时，空凭证保留对应 ID 的旧值。
    response = client.put('/api/config', json={'asset_profiles': [dict(b, name='第二组改名'), a]})
    assert response.status_code == 200
    saved = response.json()['asset_profiles']
    assert [p['id'] for p in saved] == [b['id'], a['id']]
    from scripts import secret_store
    pair = json.loads(secret_store.resolve(saved[0]['credential_store'], tmp_path/'config'))
    assert pair == {'access_key':'group-b-ak','secret_key':'group-b-sk'}
    # 删除第一组不会删除或错配另一组凭证。
    response = client.put('/api/config', json={'asset_profiles': [saved[0]]})
    assert response.status_code == 200 and len(response.json()['asset_profiles']) == 1
    assert str(credential_path(tmp_path/'config', a['id'])) not in keychain
    assert client.post('/api/assets/run', json={'asset_profile_id':a['id']}).status_code == 400
    assert client.put('/api/config', json={'profiles': []}).json()['asset_profiles'][0]['id'] == b['id']
    from backend.config_store import _load_script_module
    assert _load_script_module().load_profiles(tmp_path/'config', required=True) == []
    assert client.put('/api/config', json={'asset_profiles': []}).json()['asset_profiles'] == []
    assert not keychain and client.get('/api/config').json()['asset_profiles'] == []


def test_legacy_asset_profile_migration(client, supplier, tmp_path, keychain):
    endpoint, calls = supplier
    (tmp_path/'config').write_text(f'ASSET_ENDPOINT={endpoint}\nASSET_CREDENTIAL_STORE=macos-keychain\n')
    keychain[str(tmp_path/'config')] = ('legacy-ak','legacy-sk')
    group = client.get('/api/config').json()['asset_profiles'][0]
    assert group['id'] == 'legacy' and group['configured']
    response = client.put('/api/config', json={'asset_profiles':[dict(group, name='保留旧组'),
        dict(name='新增组',endpoint=endpoint,access_key='new-ak',secret_key='new-sk')]})
    assert response.status_code == 200
    res = client.post('/api/assets/run',json={'asset_profile_id':'legacy','payload':{'Filter':{'GroupType':'AIGC'}}})
    assert res.json()['report']['success'] and 'legacy-ak' in calls[-1][2]
    assert not keychain  # 迁移核验成功并更新配置后清理旧钥匙串


def test_asset_groups_validate_all_before_changes(client, tmp_path, keychain):
    response = client.put('/api/config',json={'asset_profiles':[
        dict(name='有效',endpoint='https://example.com',access_key='one-ak',secret_key='one-sk'),
        dict(name='无效',endpoint='https://example.com/v1',access_key='two-ak',secret_key='two-sk')]})
    assert response.status_code == 400 and not keychain and not (tmp_path/'config').exists()
    assert client.put('/api/config',json={'asset_profiles':[{'id':'unknown','endpoint':'https://example.com'}]}).status_code == 400


def test_multi_group_save_failure_restores_credentials(client, tmp_path, keychain, monkeypatch, fake_onepassword):
    from scripts import secret_store
    groups = [dict(name=name, endpoint='https://example.com', access_key=name+'-ak', secret_key=name+'-sk')
              for name in ('a', 'b')]
    saved = client.put('/api/config', json={'asset_profiles': groups}).json()['asset_profiles']
    before_file, before_keys = (tmp_path/'config').read_bytes(), keychain.copy()
    before_items = fake_onepassword.copy()
    original_write = secret_store.create
    def fail_second(value, title, settings, created):
        if json.loads(value)['access_key'] == 'b-new':
            raise ValueError('模拟钥匙串写入失败')
        return original_write(value, title, settings, created)
    monkeypatch.setattr(secret_store, 'create', fail_second)
    response = client.put('/api/config', json={'asset_profiles': [
        dict(saved[0], access_key='a-new', secret_key='new-sk'),
        dict(saved[1], access_key='b-new', secret_key='new-sk')]})
    assert response.status_code == 400
    assert keychain == before_keys and (tmp_path/'config').read_bytes() == before_file
    assert client.put('/api/config', json={'asset_profiles': [saved[0], saved[0]]}).status_code == 400
    assert keychain == before_keys
    assert fake_onepassword == before_items


def test_config_file_failure_restores_deleted_group(client, tmp_path, keychain, monkeypatch):
    from pathlib import Path
    response = client.put('/api/config', json={'asset_profiles': [dict(
        name='保留组', endpoint='https://example.com', access_key='restore-ak', secret_key='restore-sk')]})
    assert response.status_code == 200
    before_file, before_keys = (tmp_path/'config').read_bytes(), keychain.copy()
    original_replace = Path.replace
    def fail_config_replace(path, target):
        if target == tmp_path/'config':
            raise OSError('模拟配置文件写入失败')
        return original_replace(path, target)
    monkeypatch.setattr(Path, 'replace', fail_config_replace)
    with pytest.raises(OSError, match='模拟配置文件写入失败'):
        client.put('/api/config', json={'asset_profiles': []})
    assert keychain == before_keys and (tmp_path/'config').read_bytes() == before_file
    assert not (tmp_path/'config.tmp').exists()
