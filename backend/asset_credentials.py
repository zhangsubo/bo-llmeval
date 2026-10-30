"""素材库 AK/SK 仅存 macOS Keychain，不写入普通配置文件或进程命令行。"""
import base64
import hashlib
import json
import re
from pathlib import Path
import subprocess
import sys


def credential_path(config_path: Path, profile_id: str) -> Path:
    if profile_id == 'legacy':
        return config_path
    if not re.fullmatch(r'[0-9a-f]{32}', profile_id):
        raise ValueError('无效的素材库配置组 ID')
    return config_path.with_name(config_path.name + '.asset-' + profile_id)


def service_name(config_path: Path) -> str:
    return 'bo-llmeval.assets.' + hashlib.sha256(str(config_path.resolve()).encode()).hexdigest()


def _security(args, **kwargs):
    if sys.platform != 'darwin':
        raise ValueError('保存素材库凭证需要 macOS 钥匙串；其他系统请使用页面临时凭证')
    try:
        return subprocess.run(['/usr/bin/security', *args], capture_output=True, text=True,
                              timeout=30, **kwargs)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('无法访问 macOS 钥匙串，请检查是否已解锁') from None


def read_credentials(config_path: Path) -> tuple[str, str]:
    result = _security(['find-generic-password', '-a', 'bo-llmeval', '-s', service_name(config_path), '-w'])
    if result.returncode:
        raise ValueError('素材库凭证不可读取，请解锁钥匙串或在配置管理中重新保存 AK/SK')
    try:
        data = json.loads(base64.b64decode(result.stdout.strip(), validate=True))
        ak, sk = data['access_key'], data['secret_key']
        if not isinstance(ak, str) or not isinstance(sk, str) or not ak or not sk:
            raise ValueError()
        return ak, sk
    except (ValueError, KeyError, TypeError):
        raise ValueError('钥匙串中的素材库凭证无效，请重新保存') from None


def write_credentials(config_path: Path, access_key: str, secret_key: str):
    encoded = base64.b64encode(json.dumps({'access_key': access_key, 'secret_key': secret_key}).encode()).decode()
    # base64 和固定 service/account 不含交互命令分隔符，秘密通过 stdin 传入。
    command = f'add-generic-password -U -a bo-llmeval -s {service_name(config_path)} -w {encoded}\n'
    _security(['-i'], input=command)
    # security 交互模式的退出码不足以证明单条命令成功，必须读回核验。
    if read_credentials(config_path) != (access_key, secret_key):
        raise ValueError('素材库凭证未成功保存到钥匙串')


def delete_credentials(config_path: Path):
    result = _security(['delete-generic-password', '-a', 'bo-llmeval', '-s', service_name(config_path)])
    if result.returncode not in (0, 44):
        raise ValueError('无法清除素材库钥匙串凭证')
