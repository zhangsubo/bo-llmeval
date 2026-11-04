"""素材库 Web 适配层：复用零依赖 CLI；凭证由页面临时提供或从本机钥匙串读取，不进入日志或结果文件。"""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import time
from typing import Any, Literal
import uuid

from scripts import ecloud_assets

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

_SPEC = importlib.util.spec_from_file_location(
    'volcengine_assets', Path(__file__).resolve().parents[1] / 'scripts/test-volcengine-assets.py')
assets = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(assets)


class AssetRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: Literal['volcengine', 'ecloud'] = 'volcengine'
    endpoint: str = ''
    use_saved_credentials: bool = False
    asset_profile_id: str | None = None
    auth: Literal['aksk', 'bearer'] = 'aksk'
    access_key: SecretStr = SecretStr('')
    secret_key: SecretStr = SecretStr('')
    security_token: SecretStr = SecretStr('')
    api_key: SecretStr = SecretStr('')
    byted_token: SecretStr = SecretStr('')
    action: str = 'ListAssetGroups'
    region: str = 'cn-beijing'
    payload: dict[str, Any] = Field(default_factory=dict)
    wait: bool = False
    timeout: float = Field(default=60, gt=0, le=120, allow_inf_nan=False)
    max_wait: float = Field(default=300, gt=0, le=600, allow_inf_nan=False)
    poll_interval: float = Field(default=3, ge=1, le=60, allow_inf_nan=False)
    confirm_delete: bool = False


def run_asset_request(raw: dict, results_root: Path, config_path: Path | None = None) -> dict:
    try:
        body = AssetRequest.model_validate(raw)
    except ValidationError as exc:
        # Pydantic 的默认错误包含原始 input，不能把含凭证请求回显给浏览器。
        raise ValueError('素材库参数格式错误，请检查必填项及超时范围') from exc
    if body.use_saved_credentials or body.asset_profile_id:
        from .config_store import DEFAULT_CONFIG_PATH, read_raw_config
        from .asset_credentials import read_credentials, credential_path
        path = config_path or DEFAULT_CONFIG_PATH
        groups = read_raw_config(path, resolve_secrets=False)['asset_profiles']
        if body.asset_profile_id:
            saved = next((p for p in groups if p['id'] == body.asset_profile_id), None)
            if saved is None:
                raise ValueError('素材库配置组不存在，请重新选择')
        elif len(groups) == 1:
            saved = groups[0]
        else:
            raise ValueError('请选择素材库配置组')
        if not saved['endpoint'] or not saved['has_credentials']:
            raise ValueError('请先在配置管理中保存素材库根地址和 AK/SK')
        if body.auth != 'aksk':
            raise ValueError('已保存的素材库凭证仅支持 AK/SK 鉴权')
        if body.endpoint and assets.endpoint_root(body.endpoint) != assets.endpoint_root(saved['endpoint']):
            raise ValueError('使用已保存凭证时不可修改根地址，请在配置管理中修改或切换临时凭证')
        from scripts import secret_store
        if secret_store.is_reference(saved.get('credential_store', '')):
            try:
                pair = json.loads(secret_store.resolve(saved['credential_store'], path))
                ak, sk = pair['access_key'], pair['secret_key']
                if not isinstance(ak, str) or not isinstance(sk, str) or not ak or not sk:
                    raise ValueError()
            except (KeyError, TypeError, ValueError):
                raise ValueError('无法读取 1Password 素材库凭证，请检查条目和解锁状态') from None
        else:
            ak, sk = read_credentials(credential_path(path, saved['id']))
        body.endpoint = saved['endpoint']
        body.access_key, body.secret_key = SecretStr(ak), SecretStr(sk)
    provider = ecloud_assets if body.provider == 'ecloud' else assets
    if body.provider == 'ecloud' and (body.auth != 'aksk' or body.security_token.get_secret_value()):
        raise ValueError('移动云仅支持 AK/SK OpenAPI 签名，不支持 Bearer 或 STS')
    if body.wait and body.action not in ('CreateAsset', 'GetAsset'):
        raise ValueError('仅素材上传或状态查询支持轮询')
    if body.action not in provider.ACTIONS:
        raise ValueError('不支持的素材库操作')
    if body.action.startswith('Delete') and not body.confirm_delete:
        raise ValueError('删除操作需要明确确认')
    if not body.region.strip() or any(c.isspace() for c in body.region):
        raise ValueError('Region 不能为空或包含空白字符')
    secrets = [getattr(body, name).get_secret_value() for name in
               ('access_key', 'secret_key', 'security_token', 'api_key', 'byted_token')]
    ak, sk, token, bearer, byted = secrets
    credentials = (ak, sk, token, '') if body.auth == 'aksk' else ('', '', '', bearer)
    if any('\r' in item or '\n' in item for item in secrets):
        raise ValueError('凭证不能包含换行符')
    if (body.auth == 'aksk' and (not ak.strip() or not sk.strip())) or (body.auth == 'bearer' and not bearer.strip()):
        raise ValueError('请填写 AK/SK 或 Bearer API Key')
    args = assets.make_parser().parse_args([])
    args.action, args.auth, args.region = body.action, body.auth, body.region
    args.wait, args.timeout = body.wait, body.timeout
    args.max_wait, args.poll_interval = body.max_wait, body.poll_interval
    try:
        endpoint = assets.endpoint_root(body.endpoint)
        payload = (ecloud_assets.prepare(body.action, body.payload, byted) if body.provider == 'ecloud'
                   else assets.build_payload(args, initial_body=body.payload, byted_token=byted))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('请求参数无效：请检查根地址、操作必填字段、素材 URL 或认证凭证') from None
    category = 'ecloud-assets' if body.provider == 'ecloud' else 'volcengine-assets'
    folder = results_root / category / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    start = time.monotonic()
    report = {'mode': category, 'action': body.action, 'success': False,
              'base_url': endpoint, 'endpoint': endpoint, 'auth': body.auth,
              'started_at': datetime.now(timezone.utc).isoformat(), 'request_body': payload[2] if body.provider == 'ecloud' else payload, 'requests': []}
    try:
        provider.execute(args, endpoint, payload, credentials, report)
        report['success'] = True
    except Exception as exc:
        report['error'] = str(exc)
    report['outcome'] = 'completed' if report['success'] else 'failed'
    if report['success'] and body.action in ('CreateAsset', 'GetAsset') and report.get('result', {}).get('Status') != 'Active':
        report['outcome'] = 'submitted'
    report['elapsed_ms'] = round((time.monotonic() - start) * 1000, 2)
    for row in report['requests']:
        row.update(phase=row['action'], method=row.get('method', 'POST'), body=row.get('response'),
                   duration_ms=round(row['elapsed_seconds'] * 1000, 2))
    safe = assets.safe_report(report, secrets)
    text = json.dumps(safe, ensure_ascii=False, indent=2)
    (folder / 'result.json').write_text(text + '\n', encoding='utf-8')
    (folder / 'summary.txt').write_text(text + '\n', encoding='utf-8')
    response = {'report': safe, 'category': category, 'folder': folder.name}
    # 仅本次请求的响应携带会话链接；历史结果、磁盘及全局状态均不保留。
    if report['success'] and body.action == 'CreateVisualValidateSession':
        response['validation_session'] = {'h5_link': report['result']['H5Link'],
                                          'byted_token': report['result']['BytedToken']}
        if body.provider == 'ecloud':
            response['validation_session']['expires_in'] = report['result']['ExpiresIn']
    return response
