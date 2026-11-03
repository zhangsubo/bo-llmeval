import json
from pathlib import Path
from scripts.c2pa_check import inspect_video, normalize, check_and_save


def test_separate_signature_binding_trust():
    data = {'active_manifest': 'm', 'manifests': {'m': {'claim_generator': 'test'}},
        'validation_results': {'activeManifest': {'success': [{'code': 'claimSignature.validated'}, {'code': 'assertion.bmffHash.match'}],
                                                 'failure': [{'code': 'signingCredential.untrusted'}]}}}
    result = normalize(data)
    assert result['signature'] == 'passed'
    assert result['content_binding'] == 'passed'
    assert result['trust'] == 'untrusted'
    data['validation_results']['activeManifest']['failure'].append({'code': 'assertion.bmffHash.mismatch'})
    assert normalize(data)['content_binding'] == 'failed'


def test_unknown_not_success():
    result = normalize({'active_manifest': 'm', 'manifests': {'m': {}}})
    assert result['signature'] == result['trust'] == result['content_binding'] == 'unknown'


def test_missing_binary_is_not_missing_manifest(tmp_path):
    (tmp_path / 'video.mp4').write_bytes(b'fake file')
    result = check_and_save(tmp_path, executable=str(tmp_path / 'not-installed'))
    assert result['execution'] == 'error' and result['discovery'] == 'unknown'
    assert (tmp_path / 'c2pa.json').is_file()
    assert len(list((tmp_path / 'c2pa-history').glob('*.json'))) == 1


def test_adapter_offline_and_no_claim(tmp_path):
    binary = tmp_path / 'validator'
    binary.write_text('''#!/usr/bin/env python3
import sys,json
if '--version' in sys.argv:
    print('c2patool test')
else:
    settings=json.load(open(sys.argv[sys.argv.index('--settings')+1]))
    assert settings['verify']['remote_manifest_fetch'] is False
    assert settings['verify']['ocsp_fetch'] is False
    print('Error: No claim found',file=sys.stderr)
    sys.exit(1)
''')
    binary.chmod(0o700)
    path = tmp_path / 'video.mp4'
    path.write_bytes(b'fake')
    result = inspect_video(path, executable=str(binary))
    assert result['execution'] == 'completed' and result['discovery'] == 'missing'


def test_symlink_rejected(tmp_path):
    (tmp_path / 'source').write_bytes(b'fake')
    (tmp_path / 'video.mp4').symlink_to(tmp_path / 'source')
    assert inspect_video(tmp_path / 'video.mp4')['execution'] == 'error'


def test_sensitive_manifest_values_redacted():
    result = normalize({'active_manifest': 'm', 'manifests': {'m': {'assertions': [
        {'api_key': 'should-not-appear', 'url': 'https://example.com/file?signature=hidden', 'value': 'Bearer hidden'}]}}})
    assert 'should-not-appear' not in json.dumps(result)
    assert 'hidden' not in json.dumps(result)


def test_sdk_worker_offline_settings_and_trust(tmp_path, monkeypatch, capsys):
    import sys
    from types import SimpleNamespace
    from scripts.c2pa_sdk_worker import main
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'verify': {'verify_after_reading': True,
        'remote_manifest_fetch': False, 'ocsp_fetch': False}}))
    anchors = tmp_path / 'anchors.pem'
    anchors.write_text('test anchors')
    captured = {}

    class Context:
        @classmethod
        def from_dict(cls, config):
            captured.update(config)
            return cls()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class Reader(Context):
        def __init__(self, path, context):
            assert path == 'video.mp4'

        def json(self):
            return '{"active_manifest":"test"}'

    monkeypatch.setitem(sys.modules, 'c2pa', SimpleNamespace(Context=Context, Reader=Reader, C2paError=Exception))
    monkeypatch.setattr(sys, 'argv', ['worker', 'video.mp4', '--settings', str(settings), 'trust', '--trust_anchors', str(anchors)])
    assert main() == 0
    assert captured['verify'] == {'verify_after_reading': True, 'remote_manifest_fetch': False, 'ocsp_fetch': False}
    assert captured['trust']['trust_anchors'] == 'test anchors'
    assert json.loads(capsys.readouterr().out)['active_manifest'] == 'test'


def test_sdk_rejects_malformed_video(tmp_path, monkeypatch):
    import pytest
    pytest.importorskip('c2pa')
    monkeypatch.delenv('C2PATOOL_PATH', raising=False)
    path = tmp_path / 'video.mp4'
    path.write_bytes(b'not a video')
    result = inspect_video(path)
    assert result['engine'] == 'c2pa-python'
    assert result['execution'] == 'error'
    assert result['discovery'] == 'unknown'
