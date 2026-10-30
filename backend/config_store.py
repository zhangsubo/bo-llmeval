"""读写与管理 scripts/openai-api-config 配置文件（含被测配置组与 AI 解读专用配置）。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import uuid
import threading
from functools import wraps
from typing import Any

from . import asset_credentials
from scripts import secret_store


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / 'scripts'
DEFAULT_CONFIG_PATH = SCRIPTS_DIR / 'openai-api-config'


def _load_script_module():
    spec = importlib.util.spec_from_file_location('test_openai_api', SCRIPTS_DIR / 'test-openai-api.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_analyzer_module():
    spec = importlib.util.spec_from_file_location('analyzer', SCRIPTS_DIR / 'analyzer.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mask_api_key(key: str) -> str:
    if not key:
        return ''
    if secret_store.is_reference(key):
        return '••••（1Password）'
    if len(key) <= 8:
        return '****' + key[-2:] if len(key) > 2 else '****'
    return f'{key[:4]}****{key[-4:]}'


def _quote_config_value(val: str) -> str:
    if val == '':
        return ''
    escaped = val.replace('\\', '\\\\').replace('"', '\\"').replace('\n', ' ').replace('\r', ' ')
    return f'"{escaped}"'


def read_raw_config(config_path: Path | None = None, *, resolve_secrets: bool = True) -> dict[str, Any]:
    """读取配置文件中的完整信息（内部使用，包含真实 key）。"""
    path = (config_path or DEFAULT_CONFIG_PATH).expanduser().resolve()
    script_mod = _load_script_module()
    analyzer_mod = _load_analyzer_module()

    asset_config = {'endpoint': '', 'has_credentials': False, 'credential_store': ''}
    asset_groups = {}
    profiles: list[dict[str, Any]] = []
    if path.is_file():
        content = path.read_text(encoding='utf-8-sig')
        groups: dict[int, dict[str, str]] = {}
        for line_number, line in enumerate(content.splitlines(), 1):
            parsed = analyzer_mod._parse_config_line(line, line_number)
            if not parsed:
                continue
            key_name, val = parsed
            asset_match = re.fullmatch(r'ASSET_(ID|NAME|ENDPOINT|CREDENTIAL_STORE)([1-9][0-9]*)', key_name)
            if asset_match:
                field, number = asset_match.groups()
                asset_groups.setdefault(int(number), {})[field] = val
            if key_name == 'ASSET_ENDPOINT':
                asset_config['endpoint'] = val
            elif key_name == 'ASSET_CREDENTIAL_STORE':
                asset_config['has_credentials'] = val == 'macos-keychain' or secret_store.is_reference(val)
                asset_config['credential_store'] = val
            m = re.fullmatch(r'(NAME|BASE_URL|API_KEY)([1-9][0-9]*)', key_name)
            if m:
                field, num_str = m.groups()
                groups.setdefault(int(num_str), {})[field] = val
        for idx, number in enumerate(sorted(groups.keys()), 1):
            grp = groups[number]
            raw_base = grp.get('BASE_URL', '').strip()
            raw_key = grp.get('API_KEY', '').strip()
            raw_name = grp.get('NAME', '').strip()
            if not raw_base and not raw_key and not raw_name:
                continue
            normalized_base = raw_base
            if raw_base:
                try:
                    normalized_base = script_mod.normalize_base(raw_base)
                except ValueError:
                    normalized_base = raw_base
            profiles.append({
                'id': number,
                'index': idx,
                'name': raw_name or f'配置 {number}',
                'base_url': normalized_base,
                'api_key': secret_store.resolve(raw_key, path) if resolve_secrets else raw_key,
            })

    asset_profiles = []
    seen_ids = set()
    for number in sorted(asset_groups):
        group = asset_groups[number]
        profile_id = group.get('ID', '')
        asset_credentials.credential_path(path, profile_id)
        if profile_id in seen_ids:
            raise ValueError('素材库配置组 ID 重复')
        seen_ids.add(profile_id)
        asset_profiles.append({'id': profile_id, 'name': group.get('NAME') or f'素材库配置 {number}',
                               'endpoint': group.get('ENDPOINT', ''),
                               'credential_store': group.get('CREDENTIAL_STORE', ''),
                               'has_credentials': group.get('CREDENTIAL_STORE') == 'macos-keychain' or secret_store.is_reference(group.get('CREDENTIAL_STORE', ''))})
    if not asset_profiles and (asset_config['endpoint'] or asset_config['has_credentials']):
        asset_profiles = [dict(asset_config, id='legacy', name='素材库配置 1')]
    if asset_profiles:
        asset_config = asset_profiles[0]
    analyzer_cfg = analyzer_mod.load_analyzer_config(path, resolve_secrets=resolve_secrets)
    return {
        'config_path': str(path),
        'onepassword': secret_store.settings_from_file(path),
        'profiles': profiles,
        'analyzer': analyzer_cfg,
        'assets': asset_config,
        'asset_profiles': asset_profiles,
    }


def get_public_config(config_path: Path | None = None) -> dict[str, Any]:
    """返回前端展示用的脱敏配置信息。"""
    raw = read_raw_config(config_path, resolve_secrets=False)
    public_profiles = []
    for p in raw['profiles']:
        key = p.get('api_key') or ''
        public_profiles.append({
            'id': p['id'],
            'name': p['name'],
            'base_url': p['base_url'],
            'api_key_masked': mask_api_key(key),
            'has_key': bool(key),
            'valid': bool(p['base_url'] and key),
        })
    analyzer = raw['analyzer']
    a_key = analyzer.get('api_key') or ''
    public_analyzer = {
        'name': analyzer.get('name') or 'AI 结果解读模型',
        'base_url': analyzer.get('base_url') or '',
        'model': analyzer.get('model') or '',
        'api_key_masked': mask_api_key(a_key),
        'has_key': bool(a_key),
        'configured': bool(analyzer.get('configured')),
    }
    return {
        'config_path': raw['config_path'],
        'onepassword': raw['onepassword'],
        'profiles': public_profiles,
        'analyzer': public_analyzer,
        'asset_profiles': [dict(p, configured=bool(p['endpoint'] and p['has_credentials'])) for p in raw['asset_profiles']],
        'assets': dict(raw['assets'], configured=bool(raw['assets']['endpoint'] and raw['assets']['has_credentials'])),
    }


_SAVE_LOCK = threading.RLock()


def _serialized_save(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with _SAVE_LOCK:
            return fn(*args, **kwargs)
    return wrapped


@_serialized_save
def save_config(
    profiles_input: list[dict[str, Any]] | None,
    analyzer_input: dict[str, Any] | None = None,
    config_path: Path | None = None,
    assets_input: dict[str, Any] | None = None,
    asset_profiles_input: list[dict[str, Any]] | None = None,
    onepassword_input: dict[str, str] | None = None,
) -> dict[str, Any]:
    """保存被测配置列表与 AI 解读模型配置到 scripts/openai-api-config。
    若某项的 api_key 未传入或等于原掩码值，则自动保留原文件中的真实 key。
    """
    path = (config_path or DEFAULT_CONFIG_PATH).expanduser().resolve()
    script_mod = _load_script_module()
    existing = read_raw_config(path, resolve_secrets=False)
    if profiles_input is None:
        profiles_input = existing['profiles']
    op_settings = onepassword_input if onepassword_input is not None else existing['onepassword']
    existing_by_id = {p['id']: p for p in existing['profiles']}
    existing_by_base_name = {(p['name'], p['base_url']): p for p in existing['profiles']}

    lines = [
        '# OpenAI 兼容接口测试与 AI 解读配置文件（由可视化平台自动维护，CLI 脚本可直接读取）',
        '# 四种接口共用配置格式；运行时选择 chat/completions、responses、video 或 seedance。',
        '# seedance 为火山方舟原生协议；Base URL 为 https://ark.cn-beijing.volces.com/api/v3。',
        '',
    ]

    next_num = 1
    for item in profiles_input:
        name = (item.get('name') or '').strip() or f'配置 {next_num}'
        base_url = (item.get('base_url') or '').strip()
        if base_url:
            base_url = script_mod.normalize_base(base_url)
        provided_key = (item.get('api_key') or '').strip()

        # 查找旧 key
        old_profile = None
        if item.get('id') in existing_by_id:
            old_profile = existing_by_id[item['id']]
        elif (name, base_url) in existing_by_base_name:
            old_profile = existing_by_base_name[(name, base_url)]

        if not provided_key or (old_profile and provided_key == mask_api_key(old_profile.get('api_key', ''))):
            final_key = old_profile.get('api_key', '') if old_profile else ''
        else:
            final_key = provided_key

        if not base_url and not final_key:
            continue
        if not base_url or not final_key:
            raise ValueError(f'配置「{name}」的 Base URL 和 API Key 必须同时填写')

        lines.append(f'NAME{next_num}={_quote_config_value(name)}')
        lines.append(f'BASE_URL{next_num}={_quote_config_value(base_url)}')
        lines.append(f'API_KEY{next_num}={_quote_config_value(final_key)}')
        lines.append('')
        next_num += 1

    # 处理 AI 解读专用配置
    old_analyzer = existing['analyzer']
    if analyzer_input is not None:
        a_name = (analyzer_input.get('name') or 'AI 结果解读模型').strip()
        a_base = (analyzer_input.get('base_url') or '').strip()
        if a_base:
            a_base = script_mod.normalize_base(a_base)
        a_model = (analyzer_input.get('model') or '').strip()
        provided_a_key = (analyzer_input.get('api_key') or '').strip()
        old_a_key = old_analyzer.get('api_key', '')
        if analyzer_input.get('clear_key'):
            final_a_key = ''
        elif not provided_a_key or provided_a_key == mask_api_key(old_a_key):
            final_a_key = old_a_key
        else:
            final_a_key = provided_a_key
    else:
        a_name = old_analyzer.get('name') or 'AI 结果解读模型'
        a_base = old_analyzer.get('base_url') or ''
        a_model = old_analyzer.get('model') or ''
        final_a_key = old_analyzer.get('api_key') or ''

    lines.append('# AI 结果解读专用大模型配置（独立于被测配置，支持任意 OpenAI 兼容 chat/completions 接口）')
    lines.append(f'ANALYZER_NAME={_quote_config_value(a_name)}')
    lines.append(f'ANALYZER_BASE_URL={_quote_config_value(a_base)}')
    lines.append(f'ANALYZER_API_KEY={_quote_config_value(final_a_key)}')
    lines.append(f'ANALYZER_MODEL={_quote_config_value(a_model)}')
    lines.append('')

    old_groups = existing['asset_profiles']
    old_by_id = {p['id']: p for p in old_groups}
    if assets_input is not None and asset_profiles_input is not None:
        raise ValueError('不可同时更新单组和多组素材库配置')
    # 兼容旧客户端的单组更新，其他组不受影响。
    if assets_input is not None:
        first_id = old_groups[0]['id'] if old_groups else 'legacy'
        asset_profiles_input = [dict(assets_input, id=first_id,
                                     name=old_groups[0]['name'] if old_groups else '素材库配置 1'), *old_groups[1:]]
    new_groups = [dict(group) for group in old_groups]
    operations = []
    if asset_profiles_input is not None:
        from .assets_service import assets as asset_script
        new_groups = []
        used_ids = set()
        # 先校验所有组，再操作钥匙串，避免后续无效行导致前面的凭证已更改。
        for number, item in enumerate(asset_profiles_input, 1):
            profile_id = item.get('id') or uuid.uuid4().hex
            if item.get('id') and profile_id not in old_by_id and not (assets_input is not None and profile_id == 'legacy'):
                raise ValueError('素材库配置组不存在，请刷新配置后重试')
            if profile_id in used_ids:
                raise ValueError('素材库配置组 ID 重复')
            used_ids.add(profile_id)
            old = old_by_id.get(profile_id, {})
            endpoint = (item.get('endpoint') or '').strip()
            if endpoint:
                try:
                    endpoint = asset_script.endpoint_root(endpoint)
                except ValueError:
                    raise ValueError('素材库根地址必须是 HTTP(S) 根地址，不含路径、账号密码或查询参数') from None
            ak, sk = (item.get('access_key') or '').strip(), (item.get('secret_key') or '').strip()
            if any(c in ak + sk for c in ('\r', '\n', '\x00')):
                raise ValueError('AK/SK 不能包含控制字符')
            if bool(ak) != bool(sk):
                raise ValueError('更新素材库凭证时请同时填写 AK 和 SK；均留空则保留原值')
            if item.get('clear_credentials') and (ak or sk):
                raise ValueError('清除凭证不能同时填写新 AK/SK')
            if ak and not endpoint:
                raise ValueError('保存素材库 AK/SK 前请填写根地址')
            has_credentials = bool(old.get('has_credentials'))
            secret_path = asset_credentials.credential_path(path, profile_id)
            if item.get('clear_credentials'):
                if has_credentials:
                    operations.append(('delete', secret_path, '', ''))
                has_credentials = False
            elif ak:
                operations.append(('write', secret_path, ak, sk))
                has_credentials = True
            new_groups.append({'id': profile_id, 'name': (item.get('name') or '').strip() or f'素材库配置 {number}',
                               'endpoint': endpoint, 'has_credentials': has_credentials,
                               'credential_store': old.get('credential_store', '')})
        for old in old_groups:
            if old['id'] not in used_ids and old['has_credentials']:
                operations.append(('delete', asset_credentials.credential_path(path, old['id']), '', ''))
    # Immutable 1Password items: failures cannot overwrite secrets used by the old config.
    created = []
    legacy_cleanup = []
    temp = path.with_name(path.name + '.tmp')
    try:
        moved_account = op_settings != existing['onepassword']
        for index, line in enumerate(lines):
            key, sep, value = line.partition('=')
            if not sep or not re.fullmatch(r'(API_KEY[1-9][0-9]*|ANALYZER_API_KEY)', key):
                continue
            parsed = _load_analyzer_module()._parse_config_line(line, index + 1)
            value = parsed[1]
            if value and (not secret_store.is_reference(value) or moved_account):
                value = secret_store.resolve(value, settings=existing['onepassword'])
                title = 'AI 解读模型' if key == 'ANALYZER_API_KEY' else _load_analyzer_module()._parse_config_line(lines[index - 2], index - 1)[1]
                value = secret_store.create(value, title, op_settings, created)
            lines[index] = key + '=' + _quote_config_value(value)
        operations_by_path = {p: (op, ak, sk) for op, p, ak, sk in operations}
        for group in new_groups:
            secret_path = asset_credentials.credential_path(path, group['id'])
            operation = operations_by_path.get(secret_path)
            ref = group.get('credential_store', '')
            if not group['has_credentials']:
                group['credential_store'] = ''
                continue
            if operation and operation[0] == 'write':
                value = json.dumps({'access_key': operation[1], 'secret_key': operation[2]})
            elif secret_store.is_reference(ref):
                if not moved_account:
                    continue
                value = secret_store.resolve(ref, settings=existing['onepassword'])
            else:
                ak, sk = asset_credentials.read_credentials(secret_path)
                value = json.dumps({'access_key': ak, 'secret_key': sk})
            group['credential_store'] = secret_store.create(value, group['name'] + ' · AK/SK', op_settings, created)
        for old in old_groups:
            if old.get('credential_store') == 'macos-keychain':
                legacy_cleanup.append(asset_credentials.credential_path(path, old['id']))
        lines += ['ONEPASSWORD_ACCOUNT=' + _quote_config_value(op_settings.get('account', '')),
                  'ONEPASSWORD_VAULT=' + _quote_config_value(op_settings.get('vault', '')),
                  '# 素材库配置只保存 1Password 引用；AK/SK 不落盘']
        for number, group in enumerate(new_groups, 1):
            for field, value in [('ID', group['id']), ('NAME', group['name']), ('ENDPOINT', group['endpoint']),
                                 ('CREDENTIAL_STORE', group.get('credential_store', ''))]:
                lines.append(f'ASSET_{field}{number}=' + _quote_config_value(value))
        first = new_groups[0] if new_groups else {}
        lines += ['ASSET_ENDPOINT=' + _quote_config_value(first.get('endpoint', '')),
                  'ASSET_CREDENTIAL_STORE=' + _quote_config_value(first.get('credential_store', '')), '']
        path.parent.mkdir(parents=True, exist_ok=True)
        # Open with restrictive permissions from the outset (the file contains references only).
        import os
        with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as handle:
            handle.write('\n'.join(lines))
        temp.chmod(0o600)
        temp.replace(path)
    except Exception:
        for ref in created:
            try:
                secret_store.archive(ref, op_settings)
            except ValueError:
                pass  # May leave a recoverable orphan; original configuration remains authoritative.
        temp.unlink(missing_ok=True)
        raise
    warnings = []
    for secret_path in legacy_cleanup:
        try:
            asset_credentials.delete_credentials(secret_path)
        except ValueError:
            warnings.append('1Password 已保存，但旧钥匙串项清理失败，请解锁后清理旧凭证')
    result = get_public_config(path)
    result['credential_warnings'] = warnings
    return result
