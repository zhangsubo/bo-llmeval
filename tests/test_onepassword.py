"""1Password migration, isolation and failure behavior without real vault access."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts import secret_store
from backend.config_store import save_config, read_raw_config, get_public_config, _load_script_module, _load_analyzer_module
from backend.main import create_app
from fastapi.testclient import TestClient

_REAL_RUN = secret_store._run


def test_migrate_plaintext_and_resolve_cli_without_leaking(tmp_path, fake_onepassword, monkeypatch):
    config = tmp_path/'config'
    config.write_text('NAME1="测试组"\nBASE_URL1="https://example.com/v1"\nAPI_KEY1="profile-fixture-secret"\n'
                      'ANALYZER_BASE_URL="https://example.com/v1"\nANALYZER_MODEL="test"\nANALYZER_API_KEY="analyzer-fixture-secret"\n')
    result = save_config(None, config_path=config)
    disk = config.read_text()
    assert 'profile-fixture-secret' not in disk and 'analyzer-fixture-secret' not in disk
    assert len(fake_onepassword) == 2
    assert _load_script_module().load_profiles(config)[0]['key'] == 'profile-fixture-secret'
    assert _load_analyzer_module().load_analyzer_config(config)['api_key'] == 'analyzer-fixture-secret'
    assert 'fixture-secret' not in json.dumps(result)
    def locked(*args, **kwargs):
        raise ValueError('1Password 已锁定')
    monkeypatch.setattr(secret_store, '_run', locked)
    assert get_public_config(config)['profiles'][0]['has_key']
    assert save_config(None, config_path=config)['analyzer']['has_key']  # 无改动不读取密钥
    with pytest.raises(ValueError, match='锁定'):
        _load_script_module().load_profiles(config)


def test_rotation_reorder_and_clear_preserve_other_keys(tmp_path, fake_onepassword):
    path = tmp_path/'config'
    first = save_config([dict(name='A',base_url='https://example.com',api_key='a-secret'),
                         dict(name='B',base_url='https://example.com',api_key='b-secret')], config_path=path)
    a,b=first['profiles']
    updated=save_config([dict(b, name='B renamed', api_key=''),dict(a,api_key='rotated-secret')],config_path=path)
    raw=read_raw_config(path)
    assert [p['api_key'] for p in raw['profiles']] == ['b-secret','rotated-secret']
    assert len(fake_onepassword)==3  # old version remains recoverable
    save_config([updated['profiles'][0]],config_path=path)
    assert read_raw_config(path)['profiles'][0]['api_key']=='b-secret'


def test_second_key_failure_keeps_plaintext_source_intact(tmp_path, fake_onepassword, monkeypatch):
    path=tmp_path/'config'
    path.write_text('BASE_URL1=https://example.com\nAPI_KEY1=old-fixture\n')
    before=path.read_bytes()
    original=secret_store.create
    def fail(value,title,settings,created):
        if title=='AI 解读模型':
            raise ValueError('模拟失败')
        return original(value,title,settings,created)
    monkeypatch.setattr(secret_store,'create',fail)
    with pytest.raises(ValueError,match='模拟失败'):
        save_config(None,dict(base_url='https://example.com',api_key='analyzer-fixture',model='mock'),path)
    assert path.read_bytes()==before and not fake_onepassword


def test_cli_transport_uses_stdin_and_redacts_errors(monkeypatch):
    calls=[]
    def run(args,**kwargs):
        calls.append((args,kwargs))
        return SimpleNamespace(returncode=1,stdout='sensitive-fixture',stderr='sensitive-fixture')
    monkeypatch.setattr(secret_store.subprocess,'run',run)
    with pytest.raises(ValueError) as exc:
        _REAL_RUN(['item','create','-'],{'account':'test'}, {'secret':'sensitive-fixture'})
    assert 'sensitive-fixture' not in str(exc.value)
    assert 'sensitive-fixture' not in str(calls[0][0])
    assert 'sensitive-fixture' in calls[0][1]['input']


def test_readback_mismatch_does_not_publish_reference(tmp_path, monkeypatch, fake_onepassword):
    real_resolve=secret_store.resolve
    def mismatch(value,*args,**kwargs):
        return 'different-fixture' if value.startswith('op://') else real_resolve(value,*args,**kwargs)
    monkeypatch.setattr(secret_store,'resolve',mismatch)
    path=tmp_path/'config'
    with pytest.raises(ValueError,match='核验失败'):
        save_config([dict(name='A',base_url='https://example.com',api_key='fixture')],config_path=path)
    assert not path.exists() and not fake_onepassword


def test_unrelated_missing_item_does_not_block_selected_profile(tmp_path, fake_onepassword):
    path=tmp_path/'config'
    save_config([dict(name='A',base_url='https://example.com',api_key='a-fixture'),
                 dict(name='B',base_url='https://example.com',api_key='b-fixture')],config_path=path)
    refs=read_raw_config(path,resolve_secrets=False)
    fake_onepassword.pop(refs['profiles'][1]['api_key'])
    from backend.runner_service import TestRunManager
    manager=TestRunManager(config_path=path, results_root=tmp_path/'results')
    assert manager._resolve_credentials({'profile_id':1})[0]=='a-fixture'
    client=TestClient(create_app(config_path=path,results_root=tmp_path/'results'))
    assert client.get('/api/config').status_code==200
