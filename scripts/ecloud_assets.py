"""移动云 MoMA 素材库 REST 适配，Python 标准库实现。"""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
import time
from urllib import error, parse, request
import uuid

PREFIX = '/api/openapi-maas/exp/aicc/v2'
AUTH_ACTIONS = {
    'CreateVisualValidateSession': '/real-person-auth/sessions',
    'GetVisualValidateResult': '/real-person-auth/asset-group/by-byted-token',
    'ConfirmVisualValidateResult': '/real-person-auth/verification-result',
}
ACTIONS = tuple(verb + noun for noun in ('AssetGroup', 'Asset')
                for verb in ('Create', 'Get', 'Update', 'Delete')) + (
                    'ListAssetGroups', 'ListAssets', *AUTH_ACTIONS)


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def signed_query(method, path, ak, sk, timestamp=None, nonce=None):
    """OpenAPI V2.0：只签 URI query，JSON body 不参与签名。"""
    # 移动云网关按北京时间解释字面量 Z（与标准 UTC 语义不同）。
    # 显式指定 UTC+8，避免部署机器的本地时区改变签名时间。
    stamp = timestamp or datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%dT%H:%M:%SZ')
    params = {'AccessKey': ak, 'Timestamp': stamp,
              'SignatureMethod': 'HmacSHA256', 'SignatureVersion': 'V2.0',
              'SignatureNonce': nonce or uuid.uuid4().hex}
    canonical = parse.urlencode(sorted(params.items()), quote_via=parse.quote, safe='~')
    source = '\n'.join((method.upper(), parse.quote(path, safe='~'), hashlib.sha256(canonical.encode()).hexdigest()))
    signature = hmac.new(('BC_SIGNATURE&' + sk).encode(), source.encode(), hashlib.sha256).hexdigest()
    return canonical + '&Signature=' + signature


def prepare(action, payload, byted_token=''):
    if action not in ACTIONS:
        raise ValueError('不支持的移动云素材库操作')
    body = dict(payload)
    if action in AUTH_ACTIONS:
        allowed = {'callbackUrl'} if action == 'CreateVisualValidateSession' else set()
        if set(body) - allowed:
            raise ValueError('认证参数请使用专用字段')
        if action == 'CreateVisualValidateSession':
            callback = body.get('callbackUrl')
            if callback:
                check_url(callback)
                if parse.urlsplit(callback).query or parse.urlsplit(callback).fragment:
                    raise ValueError('自定义回调地址不可含 query 或 fragment')
            else:
                body = {}
        else:
            if not byted_token.strip():
                raise ValueError('请填写 BytedToken')
            body = {'bytedToken': byted_token}
        return 'POST', PREFIX + AUTH_ACTIONS[action], body
    is_group = 'Group' in action
    resource = '/asset-group' if is_group else '/asset'
    name = 'groupName' if is_group else 'assetName'
    if action.startswith('List'):
        allowed = {'pageNo', 'pageSize', 'groupType', 'groupIds', name}
        if not is_group:
            allowed.add('statuses')
        body.setdefault('pageNo', 1)
        body.setdefault('pageSize', 10)
        for key in ('pageNo', 'pageSize'):
            if type(body[key]) is not int or body[key] < 1:
                raise ValueError('分页参数必须为正整数')
        if body.get('groupType') not in ('AIGC', 'LivenessFace'):
            raise ValueError('请指定素材组类型')
        for key in ('groupIds', 'statuses'):
            if key in body and (not isinstance(body[key], list) or any(not isinstance(v, str) or not v for v in body[key])):
                raise ValueError('列表筛选字段必须是字符串数组')
        if 'statuses' in body and any(v not in ('PROCESSING', 'ACTIVE', 'FAILED') for v in body['statuses']):
            raise ValueError('素材状态无效')
        method, path = 'POST', resource + '/query'
    else:
        allowed = set()
        verb = action.removesuffix('AssetGroup' if is_group else 'Asset')
        method = {'Create': 'POST', 'Get': 'GET', 'Update': 'PUT', 'Delete': 'DELETE'}[verb]
        path = resource
        if verb != 'Create':
            ident = body.pop('id', '')
            if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', ident):
                raise ValueError('请填写有效的资源 ID')
            path += '/' + ident
        if verb in ('Create', 'Update'):
            allowed = {name, 'description'} if is_group else {name}
        if verb == 'Create':
            required = [name]
            if is_group:
                allowed.add('groupType')
                body.setdefault('groupType', 'AIGC')
                if body['groupType'] != 'AIGC':
                    raise ValueError('真人素材组必须通过真人认证创建')
            else:
                allowed.update(('groupId', 'assetUrl', 'assetType'))
                required += ['groupId', 'assetUrl', 'assetType']
                check_url(body.get('assetUrl'))
                if body.get('assetType') not in ('Image', 'Video', 'Audio'):
                    raise ValueError('素材类型无效')
            for key in required:
                if not isinstance(body.get(key), str) or not body[key].strip():
                    raise ValueError('创建操作缺少必填字段')
    if set(body) - allowed:
        raise ValueError('存在不支持的移动云参数，请按文档填写字段')
    for key, limit in ((name, 64), ('description', 300)):
        if key in body and (not isinstance(body[key], str) or len(body[key]) > limit):
            raise ValueError(f'{key} 必须为不超过 {limit} 字符的文本')
    return method, PREFIX + path, body


def check_url(value):
    if not isinstance(value, str) or any(c.isspace() for c in value):
        raise ValueError('请填写有效的 HTTP(S) URL')
    parts = parse.urlsplit(value)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username is not None or parts.password is not None:
        raise ValueError('请填写有效的 HTTP(S) URL')
    _ = parts.port


def execute(args, endpoint, payload, credentials, report):
    ak, sk, _, _ = credentials
    opener = request.build_opener(NoRedirect())

    def call(action, prepared, timeout):
        method, path, body = prepared
        row = {'action': action, 'method': method, 'path': path, 'request': body}
        report['requests'].append(row)
        start = time.monotonic()
        try:
            url = endpoint.rstrip('/') + path + '?' + signed_query(method, path, ak, sk)
            encoded = json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode() if method in ('POST', 'PUT') else None
            req = request.Request(url, data=encoded, headers={'Content-Type': 'application/json', 'Accept': 'application/json'}, method=method)
            try:
                response = opener.open(req, timeout=timeout)
            except error.HTTPError as exc:
                response = exc
            with response:
                row['http_status'] = response.code
                raw = response.read().decode('utf-8', errors='replace')
            try:
                data = json.loads(raw)
            except ValueError:
                # 原始 HTML 可能回显带签名 URL，不保存。
                raise ValueError(f'{action} 返回非 JSON（HTTP {row["http_status"]}）') from None
            row['response'] = data
            if not 200 <= row['http_status'] < 300:
                raise ValueError(f'{action} HTTP {row["http_status"]}')
            if not isinstance(data, dict) or data.get('state') != 'OK' or data.get('errorCode'):
                raise ValueError(f'{action} 返回业务错误或无效结构，见 response')
            value = data.get('body')
            if action in ('CreateAsset', 'GetVisualValidateResult', 'ConfirmVisualValidateResult'):
                if not isinstance(value, str) or not value.strip():
                    raise ValueError('接口未返回有效资源 ID')
                return {'Id' if action == 'CreateAsset' else 'GroupId': value}
            if action.startswith('Delete'):
                if value is not True:
                    raise ValueError('接口未确认删除成功')
                return {'deleted': True}
            if not isinstance(value, dict):
                raise ValueError('接口 body 必须是对象')
            result = dict(value)
            if action == 'CreateVisualValidateSession':
                check_url(value.get('h5Link'))
                if not isinstance(value.get('bytedToken'), str) or not value['bytedToken'].strip() or type(value.get('expiresIn')) is not int or value['expiresIn'] <= 0:
                    raise ValueError('认证会话缺少有效 token 或有效期')
                result.update(H5Link=value['h5Link'], BytedToken=value['bytedToken'], ExpiresIn=value['expiresIn'])
            if 'assetId' in value:
                result['Id'] = value['assetId']
            elif 'groupId' in value:
                result['Id'] = value['groupId']
            if 'status' in value:
                result['Status'] = {'ACTIVE': 'Active', 'PROCESSING': 'Processing', 'FAILED': 'Failed'}.get(value['status'], 'Unknown')
            if action == 'GetAsset':
                if value.get('assetId') != path.rsplit('/', 1)[-1]:
                    raise ValueError('返回素材 ID 与请求不一致')
                if result.get('Status') not in ('Active', 'Processing'):
                    raise ValueError('素材处理失败或返回未知状态，见 response')
            if action.startswith('List') and (not isinstance(value.get('data'), list) or type(value.get('total')) is not int):
                raise ValueError('列表返回缺少 data 或 total')
            return result
        except (error.URLError, OSError):
            # 网络异常可能带完整的签名 URL，使用固定消息。
            raise ValueError('移动云请求未完成，请检查连接及超时；写操作可能已生效，请先查询') from None
        finally:
            row['elapsed_seconds'] = round(time.monotonic() - start, 3)

    result = call(args.action, payload, args.timeout)
    report['result'] = result
    if args.action in ('GetVisualValidateResult', 'ConfirmVisualValidateResult'):
        report['group_id'] = result['GroupId']
    if args.action == 'CreateAssetGroup':
        if not isinstance(result.get('Id'), str) or not result['Id']:
            raise ValueError('创建素材组未返回有效 groupId')
        report['group_id'] = result['Id']
    asset_id = result.get('Id')
    if args.wait:
        deadline = time.monotonic() + args.max_wait
        while result.get('Status') != 'Active':
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('等待素材入库超时，可继续查询状态')
            time.sleep(min(args.poll_interval, remaining))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('等待素材入库超时，可继续查询状态')
            result = call('GetAsset', prepare('GetAsset', {'id': asset_id}), min(args.timeout, remaining))
            report['result'] = result
    if args.action in ('CreateAsset', 'GetAsset') and result.get('Status') == 'Active':
        report['asset_uri'] = f'asset://{asset_id}'
