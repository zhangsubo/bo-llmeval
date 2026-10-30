#!/usr/bin/env python3
"""火山方舟素材库测试工具（Python 标准库，无第三方依赖）。

Endpoint 仅接受根地址，原样请求 /?Action=...&Version=2024-01-01。
默认 AK/SK 签名；兼容网关可显式选择 --auth bearer。
凭证仅从环境变量或隐藏输入读取，不写入结果。
"""
import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from urllib import error, parse, request
import uuid

ACTIONS = tuple(verb + noun for noun in ('AssetGroup', 'Asset')
                for verb in ('Create', 'Get', 'Update', 'Delete')) + (
                    'ListAssetGroups', 'ListAssets',
                    'CreateVisualValidateSession', 'GetVisualValidateResult')


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # 不向重定向目标转发签名或 Bearer 凭证。


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError('必须为有限正数')
    return number


def endpoint_root(value):
    value = value.strip()
    parts = parse.urlsplit(value)
    if (parts.scheme not in ('http', 'https') or not parts.hostname
            or parts.username is not None or parts.password is not None
            or parts.path not in ('', '/') or parts.query or parts.fragment
            or any(c.isspace() for c in value)):
        raise ValueError('Endpoint 必须是 HTTP(S) 根地址，不含路径、查询参数或账号密码')
    _ = parts.port
    return parse.urlunsplit((parts.scheme, parts.netloc, '/', '', ''))


def signed_headers(url, body, ak, sk, region, token='', timestamp=None):
    """遵循官方 SDK 的 HMAC-SHA256 签名，签署实际发送的 Host/query/body。"""
    parts = parse.urlsplit(url)
    stamp = timestamp or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    host = parts.netloc
    if parts.port in (80, 443):
        host = host.rsplit(':', 1)[0]
    headers = {'Content-Type': 'application/json', 'Host': host,
               'X-Date': stamp, 'X-Content-Sha256': hashlib.sha256(body).hexdigest()}
    if token:
        headers['X-Security-Token'] = token
    lowered = {key.lower(): value for key, value in headers.items()}
    names = ';'.join(sorted(lowered))
    canonical_headers = ''.join(f'{key}:{lowered[key]}\n' for key in sorted(lowered))
    canonical = '\n'.join(('POST', '/', parts.query, canonical_headers, names,
                           headers['X-Content-Sha256']))
    scope = f'{stamp[:8]}/{region}/ark/request'
    to_sign = '\n'.join(('HMAC-SHA256', stamp, scope,
                         hashlib.sha256(canonical.encode()).hexdigest()))
    signing_key = sk.encode()
    for part in (stamp[:8], region, 'ark', 'request'):
        signing_key = hmac.new(signing_key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(signing_key, to_sign.encode(), hashlib.sha256).hexdigest()
    headers['Authorization'] = (f'HMAC-SHA256 Credential={ak}/{scope}, '
                                f'SignedHeaders={names}, Signature={signature}')
    return headers


def sanitize(value, secrets):
    if isinstance(value, dict):
        return {sanitize(str(key), secrets): '[REDACTED]' if re.sub(r'[-_]', '', str(key)).lower()
                in ('authorization', 'apikey', 'accesskey', 'secretkey', 'securitytoken',
                    'signature', 'bytedtoken', 'h5link')
                else sanitize(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in sorted(filter(None, secrets), key=len, reverse=True):
            value = value.replace(secret, '[REDACTED]')
            value = value.replace(json.dumps(secret)[1:-1], '[REDACTED]')
        # 素材下载链接可能含临时访问签名，不在报告中保留可用凭证。
        value = re.sub(r'(?i)([?&](?:X-Tos-[^=&\s]+|X-Amz-[^=&\s]+|token|signature|bytedToken|accessKeyId|secretAccessKey|sessionToken|pl)=)[^&\s"<>]*',
                       r'\1[REDACTED]', value)
    return value


def make_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--endpoint', default=os.getenv('VOLC_ASSET_ENDPOINT') or 'https://token.peopleopen.cn/', help='根地址；默认 https://token.peopleopen.cn/，可用 VOLC_ASSET_ENDPOINT 覆盖')
    parser.add_argument('--auth', choices=('aksk', 'bearer'), default='aksk')
    parser.add_argument('--region', default=os.getenv('VOLC_REGION', 'cn-beijing'))
    parser.add_argument('--action', choices=ACTIONS, default='ListAssetGroups')
    parser.add_argument('--project-name', default=None, help='默认 default；未指定时保留 JSON 内的值')
    parser.add_argument('--id', help='素材或素材组 Id')
    parser.add_argument('--group-id')
    parser.add_argument('--group-type', choices=('AIGC', 'LivenessFace'),
                        help='列表查询的素材组类型；缺省 AIGC，保留 body-file 中显式的 Filter.GroupType')
    parser.add_argument('--name')
    parser.add_argument('--description')
    parser.add_argument('--url', help='公共可访问的素材 HTTP(S) URL')
    parser.add_argument('--asset-type', choices=('Image', 'Video', 'Audio'))
    parser.add_argument('--callback-url', help='真人认证完成后的 HTTP(S) 回调页面')
    parser.add_argument('--show-validation-session', action='store_true',
                        help='显式在终端显示一次 H5Link/BytedToken（不写入报告）；交互终端默认显示')
    parser.add_argument('--body-file', type=Path, help='高级筛选/分页等 JSON 对象；命令行字段优先')
    parser.add_argument('--wait', action='store_true', help='CreateAsset/GetAsset 后轮询至 Active/Failed')
    parser.add_argument('--poll-interval', type=positive, default=3)
    parser.add_argument('--timeout', type=positive, default=60, help='单次 HTTP 超时秒数')
    parser.add_argument('--max-wait', type=positive, default=300, help='轮询总时限秒数')
    parser.add_argument('--output-dir', type=Path, default=Path(__file__).resolve().parent / 'test-results' / 'volcengine-assets')
    return parser


def build_payload(args, *, initial_body=None, byted_token=None):
    body = (dict(initial_body) if initial_body is not None else
            json.loads(args.body_file.read_text(encoding='utf-8')) if args.body_file else {})
    if not isinstance(body, dict):
        raise ValueError('--body-file 必须包含 JSON 对象')
    for key, attr in (('Id', 'id'), ('GroupId', 'group_id'), ('Name', 'name'),
                      ('Description', 'description'), ('URL', 'url'), ('AssetType', 'asset_type'),
                      ('CallbackURL', 'callback_url')):
        if getattr(args, attr) is not None:
            body[key] = getattr(args, attr)
    if args.project_name is not None:
        body['ProjectName'] = args.project_name
    body.setdefault('ProjectName', 'default')
    if args.action in ('ListAssetGroups', 'ListAssets'):
        filters = body.get('Filter', {})
        if not isinstance(filters, dict):
            raise ValueError('Filter 必须是 JSON 对象')
        filters = dict(filters)
        if args.group_type is not None:
            filters['GroupType'] = args.group_type
        filters.setdefault('GroupType', 'AIGC')
        if filters['GroupType'] not in ('AIGC', 'LivenessFace'):
            raise ValueError('Filter.GroupType 必须为 AIGC 或 LivenessFace')
        body['Filter'] = filters
    elif args.group_type is not None:
        raise ValueError('--group-type 仅用于 ListAssetGroups/ListAssets')
    required = ['ProjectName']
    if args.action.startswith(('Get', 'Update', 'Delete')) and args.action != 'GetVisualValidateResult':
        required += ['Id']
    if args.action == 'CreateVisualValidateSession':
        required += ['CallbackURL']
    if args.action == 'GetVisualValidateResult':
        # 会话凭证不从命令行或 JSON 文件读取，避免留在历史记录或文件中。
        if 'BytedToken' in body:
            raise ValueError('BytedToken 请通过 VOLC_BYTED_TOKEN 或隐藏输入提供，不放入 body-file')
        body['BytedToken'] = byted_token if byted_token is not None else credential('VOLC_BYTED_TOKEN')
        required += ['BytedToken']
    elif args.callback_url is not None and args.action != 'CreateVisualValidateSession':
        raise ValueError('--callback-url 仅用于 CreateVisualValidateSession')
    if args.action == 'CreateAssetGroup':
        required += ['Name']
        body.setdefault('GroupType', 'AIGC')
        if body['GroupType'] != 'AIGC':
            raise ValueError('CreateAssetGroup 仅用于 AIGC；真人素材组须通过真人认证获取')
    if args.action == 'CreateAsset':
        required += ['GroupId', 'URL', 'AssetType']
    for key in required:
        if not isinstance(body.get(key), str) or not body[key].strip():
            raise ValueError(f'{args.action} 缺少有效字段 {key}')
    if 'URL' in body:
        parts = parse.urlsplit(body['URL'])
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
            raise ValueError('URL 必须是公共可访问的 HTTP(S) 素材地址')
    if 'AssetType' in body and body['AssetType'] not in ('Image', 'Video', 'Audio'):
        raise ValueError('AssetType 必须为 Image/Video/Audio')
    if 'CallbackURL' in body:
        parts = parse.urlsplit(body['CallbackURL'])
        if (parts.scheme not in ('http', 'https') or not parts.hostname
                or parts.username is not None or parts.password is not None or parts.fragment
                or any(c.isspace() for c in body['CallbackURL'])):
            raise ValueError('CallbackURL 必须是有效的 HTTP(S) 页面地址，不含账号密码或 fragment')
    if args.wait and args.action not in ('CreateAsset', 'GetAsset'):
        raise ValueError('--wait 仅用于 CreateAsset/GetAsset')
    return body


def credential(env_name):
    value = os.getenv(env_name, '')
    if not value and sys.stdin.isatty():
        value = getpass.getpass(f'{env_name}（不回显、不保存）: ')
    if not value or any(c in value for c in '\r\n'):
        raise ValueError(f'请设置有效的 {env_name} 环境变量')
    return value


def execute(args, endpoint, payload, credentials, report):
    ak, sk, token, bearer = credentials
    opener = request.build_opener(NoRedirect())

    def call(action, body, timeout):
        url = endpoint + '?' + parse.urlencode({'Action': action, 'Version': '2024-01-01'})
        encoded = json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        headers = (signed_headers(url, encoded, ak, sk, args.region, token) if args.auth == 'aksk'
                   else {'Content-Type': 'application/json', 'Authorization': f'Bearer {bearer}'})
        row = {'action': action, 'request': body}
        report['requests'].append(row)
        start = time.monotonic()
        try:
            try:
                response = opener.open(request.Request(url, data=encoded, headers=headers, method='POST'), timeout=timeout)
            except error.HTTPError as exc:
                response = exc
            with response:
                row['http_status'] = response.code
                raw = response.read().decode('utf-8', errors='replace')
            try:
                data = json.loads(raw)
            except ValueError:
                row['response'] = raw
                raise ValueError(f'{action} HTTP {row["http_status"]} 返回非 JSON') from None
            row['response'] = data
            if not 200 <= row['http_status'] < 300:
                raise ValueError(f'{action} HTTP {row["http_status"]}')
            if not isinstance(data, dict):
                raise ValueError(f'{action} 返回格式错误：期望 JSON 对象')
            metadata = data.get('ResponseMetadata', {})
            if data.get('Error') or data.get('error') or (isinstance(metadata, dict) and metadata.get('Error')):
                raise ValueError(f'{action} 返回业务错误，见 response')
            result = data.get('Result', data)
            if not isinstance(result, dict):
                raise ValueError(f'{action} Result 必须是对象')
            if result.get('Error') or result.get('Status') == 'Failed':
                raise ValueError(f'{action} 素材处理失败，见 response')
            if action == 'GetAsset':
                if result.get('Status') not in ('Processing', 'Active'):
                    raise ValueError(f'GetAsset 返回未知素材状态：{result.get("Status")}')
                if result.get('Id', body['Id']) != body['Id']:
                    raise ValueError('GetAsset 返回的 Id 与请求不一致')
            return result
        finally:
            row['elapsed_seconds'] = round(time.monotonic() - start, 3)

    result = call(args.action, payload, args.timeout)
    if args.action == 'CreateVisualValidateSession':
        for key in ('BytedToken', 'H5Link'):
            if not isinstance(result.get(key), str) or not result[key].strip():
                raise ValueError(f'CreateVisualValidateSession 未返回有效 {key}')
        link = parse.urlsplit(result['H5Link'])
        if link.scheme not in ('http', 'https') or not link.hostname:
            raise ValueError('CreateVisualValidateSession 返回无效 H5Link')
        report['next_step'] = '本人打开 H5Link 完成认证，回调 resultCode=10000 后调用 GetVisualValidateResult'
    if args.action == 'GetVisualValidateResult':
        if not isinstance(result.get('GroupId'), str) or not result['GroupId'].strip():
            raise ValueError('尚未取得有效 GroupId；请确认真人认证已完成、BytedToken 未过期且 ProjectName 一致')
        report['group_id'] = result['GroupId']
    asset_id = result.get('Id') or (payload.get('Id') if args.action == 'GetAsset' else None)
    if args.action == 'CreateAsset' and (not isinstance(asset_id, str) or not asset_id):
        raise ValueError('CreateAsset 未返回有效 Id')
    if args.wait:
        deadline = time.monotonic() + args.max_wait
        while result.get('Status') != 'Active':
            if args.action == 'GetAsset' or result.get('Status') is not None:
                if result.get('Status') != 'Processing':
                    raise ValueError(f'未知素材状态：{result.get("Status")}')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('等待素材 Active 超时，可用 GetAsset 继续查询')
            time.sleep(min(args.poll_interval, remaining))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('等待素材 Active 超时，可用 GetAsset 继续查询')
            result = call('GetAsset', {'Id': asset_id, 'ProjectName': payload['ProjectName']}, min(args.timeout, remaining))
            if result.get('Id', asset_id) != asset_id:
                raise ValueError('GetAsset 返回的 Id 与请求不一致')
    if args.action in ('CreateAsset', 'GetAsset') and result.get('Status') == 'Active' and asset_id:
        report['asset_uri'] = f'asset://{asset_id}'
    report['result'] = result


def safe_report(report, secrets):
    """CLI/Web 共用脱敏，连同错误信息中的会话凭证一起处理。"""
    secrets = list(secrets)
    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower() in ('bytedtoken', 'h5link') and isinstance(item, str):
                    secrets.append(item)
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
    collect(report)
    return sanitize(report, secrets)


def main(argv=None):
    args = make_parser().parse_args(argv)
    start = time.monotonic()
    secrets = [os.getenv(name, '') for name in ('VOLC_ACCESS_KEY', 'VOLC_SECRET_KEY', 'VOLC_SECURITY_TOKEN', 'VOLC_ASSET_API_KEY', 'VOLC_BYTED_TOKEN')]
    report = {'mode': 'volcengine-assets', 'action': args.action, 'requests': [], 'success': False,
              'started_at': datetime.now(timezone.utc).isoformat()}
    folder = None
    try:
        endpoint = args.endpoint
        if not endpoint and sys.stdin.isatty():
            endpoint = input('Endpoint 根地址: ').strip()
        if not endpoint:
            raise ValueError('请设置 --endpoint 或 VOLC_ASSET_ENDPOINT')
        endpoint = endpoint_root(endpoint)
        payload = build_payload(args)
        secrets.append(payload.get('BytedToken', ''))
        if args.auth == 'aksk':
            credentials = (credential('VOLC_ACCESS_KEY'), credential('VOLC_SECRET_KEY'), os.getenv('VOLC_SECURITY_TOKEN', ''), '')
        else:
            credentials = ('', '', '', credential('VOLC_ASSET_API_KEY'))
        secrets.extend(credentials)
        folder = args.output_dir / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8])
        folder.mkdir(parents=True)
        report.update(endpoint=endpoint, base_url=endpoint, auth=args.auth)
        execute(args, endpoint, payload, credentials, report)
        report['success'] = True
    except (ValueError, OSError, error.URLError, TimeoutError) as exc:
        report['error'] = str(exc)
    except KeyboardInterrupt:
        report['error'] = '用户中断；已发送的创建/删除请求不会自动撤销，请查询确认状态'
    report['outcome'] = 'completed' if report['success'] else 'failed'
    if report['success'] and args.action in ('CreateAsset', 'GetAsset') and report.get('result', {}).get('Status') != 'Active':
        report['outcome'] = 'submitted'
    report['elapsed_ms'] = round((time.monotonic() - start) * 1000, 2)
    safe = safe_report(report, secrets)
    text = json.dumps(safe, ensure_ascii=False, indent=2)
    if folder:
        try:
            (folder / 'result.json').write_text(text + '\n', encoding='utf-8')
            (folder / 'summary.txt').write_text(text + '\n', encoding='utf-8')
        except OSError as exc:
            print(sanitize(f'保存结果失败：{exc}', secrets), file=sys.stderr)
            print(text)
            return 1
    print(text)
    if report['success'] and args.action == 'CreateVisualValidateSession':
        if args.show_validation_session or sys.stdout.isatty():
            print('以下一次性认证信息仅供本人使用，不写入报告：')
            print(f'H5Link: {report["result"]["H5Link"]}')
            print(f'BytedToken: {report["result"]["BytedToken"]}')
        else:
            print('认证信息已脱敏；需要获取链接时在交互终端运行，或显式添加 --show-validation-session。')
    if folder:
        print(f'结果目录：{folder}')
    return 0 if report['success'] else 1


if __name__ == '__main__':
    sys.exit(main())
