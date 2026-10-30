"""1Password CLI adapter. Secrets travel through stdin/stdout in memory only."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import subprocess


def settings_from_file(path: Path | None) -> dict[str, str]:
    values = {}
    if path and path.is_file():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            key, sep, value = line.partition('=')
            if sep and key.strip() in ('ONEPASSWORD_ACCOUNT', 'ONEPASSWORD_VAULT'):
                value = value.strip()
                if value.startswith('"'):
                    try:
                        value = json.loads(value)
                    except ValueError:
                        raise ValueError('1Password 账号或保险库配置格式无效') from None
                values[key.strip()] = value.strip("'")
    return {'account': values.get('ONEPASSWORD_ACCOUNT') or os.getenv('OP_ACCOUNT', ''),
            'vault': values.get('ONEPASSWORD_VAULT') or os.getenv('BO_LLMEVAL_OP_VAULT', '')}


def _run(args: list[str], settings: dict, payload=None) -> str:
    account = settings.get('account', '').strip()
    if not account:
        raise ValueError('请先配置 1Password 账号，并在桌面客户端解锁及启用 CLI 集成')
    try:
        result = subprocess.run(['op', *args, '--account', account],
                                input=json.dumps(payload) if payload is not None else None,
                                text=True, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('无法访问 1Password CLI，请检查 op 是否已安装、客户端已解锁且 CLI 集成已启用') from None
    if result.returncode:
        # Never expose CLI stdout/stderr: an upstream failure can contain secret values.
        raise ValueError('1Password 操作失败，请检查账号、保险库权限及桌面客户端解锁状态')
    return result.stdout


def is_reference(value: str) -> bool:
    return value.startswith('op://')


def validate_reference(value: str):
    if not re.fullmatch(r'op://[^/\s?#]+/[^/\s?#]+/[^/\s?#]+', value):
        raise ValueError('1Password 密钥引用格式无效')


def resolve(value: str, path: Path | None = None, *, settings: dict | None = None) -> str:
    if not is_reference(value):
        return value
    validate_reference(value)
    result = _run(['read', value, '--no-newline'], settings if settings is not None else settings_from_file(path))
    if not result or '\n' in result or '\r' in result:
        raise ValueError('1Password 密钥为空或包含换行，请检查对应条目')
    return result


def archive(reference: str, settings: dict):
    validate_reference(reference)
    vault, item, _ = reference[5:].split('/')
    _run(['item', 'delete', item, '--vault', vault, '--archive'], settings)


def create(value: str, title: str, settings: dict, created: list[str]) -> str:
    vault = settings.get('vault', '').strip()
    if not vault:
        raise ValueError('请先指定用于保存密钥的 1Password 保险库')
    payload = {'title': 'bo-llmeval · ' + title, 'category': 'API_CREDENTIAL',
               'tags': ['bo-llmeval'],
               'fields': [{'id': 'credential', 'label': 'credential', 'type': 'CONCEALED', 'value': value}]}
    raw = _run(['item', 'create', '-', '--vault', vault, '--format', 'json'], settings, payload)
    try:
        item = json.loads(raw)
        reference = f"op://{item['vault']['id']}/{item['id']}/credential"
        validate_reference(reference)
    except (ValueError, KeyError, TypeError):
        raise ValueError('1Password 返回的条目信息无效；请检查保险库中是否已创建条目后重试') from None
    created.append(reference)
    if resolve(reference, settings=settings) != value:
        raise ValueError('1Password 密钥读回核验失败，原配置未更改')
    return reference
